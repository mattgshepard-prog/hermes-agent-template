"""
Hazel: alert engine.

WHAT NOT TO ALERT ON is the design. Alert fatigue kills this feature faster
than a bug will. Every rule below has a suppression clause, and they matter
more than the detection logic.

Global suppressions:
  - never alert on a slot not being purchased this cycle
  - never alert on a single observation (spikes are usually stock-outs)
  - never alert below MIN_DOLLAR impact
  - never alert on a LOW/NONE confidence parse
  - cap alerts per cycle, ranked by dollar impact

Baselines are computed on UNIT price, never package price. Package price is
display only: it stops being comparable the moment a product line shrinks.
"""

import re

import store as S

MIN_DOLLAR = 1.00          # below this, not worth your attention
RISE_PCT = 0.10            # 10% over trailing median
RISE_OBSERVATIONS = 2      # must persist; one reading is noise
LOW_PCT = 0.15             # 15% under median = buy-ahead candidate
BETTER_ALT_PCT = 0.10      # challenger must beat current pick by this
BETTER_ALT_CYCLES = 3      # ...for this many cycles running
SHRINK_PCT = 0.03          # 3% pack reduction is real, not rounding
UNTRIED_MIN_SAVINGS = 0.15 # 15% before proposing a brand you've never used
MAX_ALERTS_PER_CYCLE = 3


def product_line(brand, title):
    """
    Collapse a title to a product LINE so pack variants group together.

    Necessary because the UPC CHANGES when pack size changes. Joining on UPC
    makes the old variant vanish and the new one look like a brand-new product
    at a fine price -- which is exactly how shrinkflation stays invisible.
    """
    t = (title or "").lower()
    t = re.sub(r"\b\d+(\.\d+)?\s*(ct|count|rolls?|sheets?|pods?|packs?|"
               r"fl\.?\s*oz|oz|ml|lbs?|pounds?)\b", " ", t)
    t = re.sub(r"\b(super\s+mega|mega|double|triple|family|jumbo|giant|regular|"
               r"single|big|huge)\b", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    words = [w for w in t.split() if len(w) > 2][:6]
    line = " ".join(words)
    if brand and brand.lower() not in line:
        line = f"{brand.lower()} {line}".strip()
    return line


def check_shrinkflation(con, slot_id, candidate, stale_before=None):
    """
    Same product line, SAME retailer, prior variant no longer on the shelf,
    smaller pack, price not proportionally lower.

    stale_before is required for a real detection. Shrinkflation means the old
    variant was REPLACED. Two pack sizes sold side by side today are not a
    shrink, and alerting on them is a false positive that teaches you to
    ignore the feature. Callers pass the start of the current cycle.

    Only fires on a comparable parse: a low-confidence pack size produces a
    false alarm, and the model will not tell you it guessed.
    """
    if not candidate.pack or not candidate.pack.comparable:
        return None
    if stale_before is None:
        return None  # cannot distinguish a shrink from concurrent variants

    line = product_line(candidate.brand, candidate.title)
    variants = S.pack_variants(con, slot_id, line,
                               retailer=candidate.retailer,
                               stale_before=stale_before)
    prior = [v for v in variants
             if v["product_key"] != candidate.product_key and v["total_units"]]
    if not prior:
        return None

    biggest = max(prior, key=lambda v: v["total_units"])
    if not biggest["total_units"] or not candidate.pack.total_units:
        return None

    shrink = (biggest["total_units"] - candidate.pack.total_units) / biggest["total_units"]
    if shrink < SHRINK_PCT:
        return None

    old_up = biggest["unit_price"]
    new_up = candidate.unit_price
    if not old_up or not new_up or new_up <= old_up:
        return None

    up_pct = (new_up - old_up) / old_up
    impact = round((new_up - old_up) * candidate.pack.total_units / 100.0, 2)
    if impact < MIN_DOLLAR:
        return None

    return {
        "kind": "shrinkflation",
        "detail": (f"{line} @ {candidate.retailer}: "
                   f"{biggest['total_units']:.0f} -> "
                   f"{candidate.pack.total_units:.0f} "
                   f"{candidate.pack.unit_of_measure} "
                   f"(${biggest['price']:.2f} -> ${candidate.price:.2f}), "
                   f"unit price +{up_pct*100:.1f}%"),
        "dollar_impact": impact,
        "product_line": line,
        "retailer": candidate.retailer,
        "rebaseline": True,
    }


def check_sustained_rise(con, slot_id, candidate, days=90):
    """Current pick meaningfully above its own trailing median, persistently."""
    if not candidate.unit_price:
        return None
    med = S.trailing_median(con, slot_id, candidate.product_key, days=days)
    if not med:
        return None

    rows = con.execute(
        """SELECT unit_price FROM price_observations
           WHERE slot_id=? AND product_key=? AND unit_price IS NOT NULL
           ORDER BY observed_at DESC LIMIT ?""",
        (slot_id, candidate.product_key, RISE_OBSERVATIONS - 1)).fetchall()
    recent = [r[0] for r in rows] + [candidate.unit_price]
    if len(recent) < RISE_OBSERVATIONS:
        return None  # one reading is noise, not a trend
    if not all(v >= med * (1 + RISE_PCT) for v in recent):
        return None

    pct = (candidate.unit_price - med) / med
    units = candidate.pack.total_units if candidate.pack else 0
    impact = round((candidate.unit_price - med) * (units or 0) / 100.0, 2)
    if impact < MIN_DOLLAR:
        return None
    return {
        "kind": "sustained_rise",
        "detail": (f"{candidate.title[:45]}: +{pct*100:.1f}% over "
                   f"{days}d median, {RISE_OBSERVATIONS} readings running"),
        "dollar_impact": impact,
    }


def check_unusually_low(con, slot_id, candidate, on_hand=None):
    """
    Buy-ahead candidate. Suppressed when max_stock would be exceeded, so Hazel
    cannot talk you into nine months of paper towels.
    """
    if not candidate.unit_price:
        return None
    med = S.trailing_median(con, slot_id, candidate.product_key)
    if not med or candidate.unit_price > med * (1 - LOW_PCT):
        return None

    slot = S.get_slot(con, slot_id) or {}
    max_stock = slot.get("max_stock")
    units = (candidate.pack.total_units if candidate.pack else 0) or 0
    if max_stock and on_hand is not None and (on_hand + units) > max_stock:
        return None  # no room; a deal you cannot store is not a deal

    impact = round((med - candidate.unit_price) * units / 100.0, 2)
    if impact < MIN_DOLLAR:
        return None
    return {
        "kind": "unusually_low",
        "detail": (f"{candidate.title[:45]}: "
                   f"{(1 - candidate.unit_price/med)*100:.1f}% under median"),
        "dollar_impact": impact,
    }


def check_better_alternative(con, slot_id, current, challenger):
    """
    A challenger has beaten the current pick by a real margin. If it is a brand
    never tried, it must clear a HIGHER bar, because an untried brand is a
    gamble you have to live with, not just a price.
    """
    if not current or not challenger:
        return None
    if not current.unit_price or not challenger.unit_price:
        return None
    if challenger.product_key == current.product_key:
        return None

    gain = (current.unit_price - challenger.unit_price) / current.unit_price
    untried = S.is_untried(con, slot_id, challenger.product_key)
    bar = UNTRIED_MIN_SAVINGS if untried else BETTER_ALT_PCT
    if gain < bar:
        return None

    units = (current.pack.total_units if current.pack else 0) or 0
    impact = round((current.unit_price - challenger.unit_price) * units / 100.0, 2)
    if impact < MIN_DOLLAR:
        return None
    return {
        "kind": "better_alt",
        "detail": (f"{challenger.title[:40]} beats current pick by "
                   f"{gain*100:.1f}%" + (" [UNTRIED brand]" if untried else "")),
        "dollar_impact": impact,
        "untried": untried,
        "challenger_key": challenger.product_key,
    }


def run_alerts(con, slot_id, candidates, current_pick=None, on_hand=None,
               purchasing_this_cycle=True, stale_before=None,
               budget=None):
    """
    Evaluate one slot. Returns alerts actually raised, capped and ranked.
    """
    if not purchasing_this_cycle:
        return []  # never alert on something you are not buying

    found = []
    for c in candidates:
        if not c.pack or not c.pack.comparable:
            continue  # low-confidence parse must never raise an alert
        a = check_shrinkflation(con, slot_id, c, stale_before=stale_before)
        if a:
            found.append((c, a))
        a = check_sustained_rise(con, slot_id, c)
        if a:
            found.append((c, a))
        a = check_unusually_low(con, slot_id, c, on_hand=on_hand)
        if a:
            found.append((c, a))

    comparable = [c for c in candidates if c.unit_price is not None]
    if current_pick and comparable:
        best = min(comparable, key=lambda c: c.unit_price)
        a = check_better_alternative(con, slot_id, current_pick, best)
        if a:
            found.append((best, a))

    found.sort(key=lambda x: x[1].get("dollar_impact") or 0, reverse=True)
    # run_alerts is called ONCE PER SLOT, so the cap must be threaded
    # as a cycle-wide budget or 3 slots yield 9 alerts.
    cap = MAX_ALERTS_PER_CYCLE if budget is None else max(0, int(budget))
    raised = []
    for c, a in found[:cap]:
        aid = S.raise_alert(con, slot_id, a["kind"], a["detail"],
                            a.get("dollar_impact"))
        if aid:
            a["alert_id"] = aid
            raised.append(a)
        if a.get("rebaseline"):
            # After a confirmed shrink the old baseline is meaningless. Without
            # this reset, every later observation re-trips the same alert and
            # the feature becomes noise within a month.
            S.record_pack(con, slot_id, c.retailer, a["product_line"],
                          c.product_key, c.pack.total_units, c.price,
                          c.unit_price)
    return raised
