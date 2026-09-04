"""
Hazel: the weekly cycle.

ONE ordering day, predictable arrival. Multiple deliveries are fine; multiple
UNPREDICTABLE deliveries are not. The weekly cadence also does structural work:
pantry state only has to be correct once a week at a known time, which turns
continuous inventory tracking into a single reconciliation.

Cadence tiers exist to control SerpApi spend:
    weekly     volatile / high-spend slots
    monthly    stable consumables
    quarterly  long tail
A slot is only priced if it is DUE and is actually being bought this cycle.

Nothing here purchases anything. The cycle ends at a staged basket awaiting
explicit approval. That is a permanent architectural feature, not a training
wheel: the sanctioned APIs cannot check out unattended anyway, and money must
never ride on model judgment.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import alerts as A
import store as S
from optimizer import LineOption, RetailerTerms, optimize, explain
from prices import PriceError, RETAILERS, rank, search_all

# Data lives next to the code. /data/hazel on Railway and /opt/data/hazel
# on Portal are both 'the directory this file sits in', so this default is
# correct on either without an environment variable. Set HAZEL_HOME to
# split code from data.
HAZEL_HOME = os.environ.get(
    "HAZEL_HOME", os.path.dirname(os.path.abspath(__file__)))

STAGE_DIR = os.environ.get("HAZEL_STAGE", os.path.join(HAZEL_HOME, "staged"))

CADENCE_DAYS = {"weekly": 7, "monthly": 28, "quarterly": 91}

# Terms are configuration, not model judgment. Verify these against each
# retailer periodically; a stale threshold produces a wrong split.
# Local combined rate for TAXABLE goods. Groceries are exempt everywhere
# relevant, so this only ever touches non-food lines. Broomfield (80020) is
# 8.15%; Westminster is 9.0% since 2026-01-01. Override without a code change.
TAX_RATE = float(os.environ.get("HOUSEHOLD_TAX_RATE", "0.0815"))

# Food for home consumption is exempt (C.R.S. 39-26-707), and both Broomfield
# and Westminster follow the state exemption. Everything here is NOT food.
TAXABLE_CATEGORIES = {
    "household",          # cleaning, personal care, filters, wipes
    "roll_goods",         # paper towels, toilet paper
    "laundry_detergent",
    "supplements",        # dietary supplements are outside the SNAP definition
}

# Colorado taxes carbonated soft drinks but not juice, so the beverages
# category splits. Named explicitly rather than sweeping the category, which
# would wrongly tax apple_juice.
TAXABLE_SLOTS = {
    "energy_drinks_variety",
    "energy_drinks_zero",
}

EXEMPT_SLOTS = set()   # escape hatch: force-exempt a slot in a taxable category


def is_taxable(category, slot_id=None):
    """Taxability is a property of the GOODS, not of the store selling them."""
    if slot_id and slot_id in EXEMPT_SLOTS:
        return False
    if slot_id and slot_id in TAXABLE_SLOTS:
        return True
    return category in TAXABLE_CATEGORIES


# Kroger figure is PICKUP, which is what cart.py submits. Free over $35,
# $4.95 below it, $35 minimum. Delivery would be ~$9.95 and is not used.
# One tax rate everywhere: the store does not change what a good is.
DEFAULT_TERMS = {
    "kroger":  RetailerTerms("kroger",  free_ship_threshold=35.0,
                             delivery_fee=4.95, order_minimum=35.0,
                             tax_rate=TAX_RATE),
    "walmart": RetailerTerms("walmart", free_ship_threshold=35.0,
                             delivery_fee=6.99, tax_rate=TAX_RATE),
    "amazon":  RetailerTerms("amazon",  free_ship_threshold=35.0,
                             delivery_fee=6.99, membership=True,
                             tax_rate=TAX_RATE),
}


def now():
    return datetime.now(timezone.utc)


def last_priced(con, slot_id):
    r = con.execute(
        "SELECT MAX(observed_at) FROM price_observations WHERE slot_id=?",
        (slot_id,)).fetchone()
    if not r or not r[0]:
        return None
    try:
        return datetime.fromisoformat(r[0])
    except ValueError:
        return None


def is_due(con, slot, today=None):
    today = today or now()
    days = CADENCE_DAYS.get(slot.get("cadence", "monthly"), 28)
    last = last_priced(con, slot["slot_id"])
    if last is None:
        return True
    return (today - last) >= timedelta(days=days)


def price_slot(con, slot, retailers=RETAILERS, limit=8, record=True):
    """
    Acquire, normalize, filter by verdict, rank. Returns a result dict.

    Verdict filtering happens BEFORE ranking, so a rejected product cannot win
    on price. The model never sees the rejected candidates at all.
    """
    slot_id = slot["slot_id"]
    terms = json.loads(slot.get("search_terms") or "[]")
    term = terms[0] if terms else slot["display_name"]

    cands, errors = search_all(term, retailers=retailers, limit=limit,
                               category=slot.get("category"),
                               uom=slot.get("unit_of_measure"))

    kept, dropped = S.filter_candidates(
        con, slot_id,
        [{"product_key": c.product_key, "title": c.title, "brand": c.brand,
          "in_stock": c.in_stock, "stock_note": c.stock_note}
         for c in cands])
    keep_keys = {k["product_key"] for k in kept}
    filtered = [c for c in cands if c.product_key in keep_keys]

    ranked, skipped = rank(filtered)

    if record:
        for c in ranked:
            S.record_observation(con, slot_id, c.retailer, c.product_key,
                                 c.title, c.price, c.pack, c.unit_price,
                                 brand=c.brand, regular_price=c.regular_price,
                                 promo_price=c.promo_price,
                                 in_stock=c.in_stock)
            line = A.product_line(c.brand, c.title)
            S.record_pack(con, slot_id, c.retailer, line, c.product_key,
                          c.pack.total_units, c.price, c.unit_price)

    return {
        "slot_id": slot_id, "term": term, "ranked": ranked,
        "skipped": skipped, "dropped": dropped, "errors": errors,
    }


def best_per_retailer(ranked, category=None, slot_id=None):
    """
    Cheapest comparable option at each retailer -> optimizer input.

    taxable is set HERE, from the slot, because it is a property of the goods.
    Leaving it at its default of True was how tax ended up approximated at the
    retailer level.
    """
    taxable = is_taxable(category, slot_id)
    seen, out = {}, []
    for c in ranked:
        if c.unit_price is None:
            continue
        if c.retailer not in seen or c.unit_price < seen[c.retailer].unit_price:
            seen[c.retailer] = c
    for c in seen.values():
        out.append(LineOption(slot_id="", retailer=c.retailer,
                              product_key=c.product_key, title=c.title,
                              price=c.price, unit_price=c.unit_price,
                              taxable=taxable))
    return out


def run_cycle(con, slot_ids=None, terms=None, record=True, force=False):
    """
    Full weekly run. Returns a staged basket dict awaiting approval.

    Nothing is purchased. Nothing is added to any cart. The output is a
    proposal.
    """
    terms = terms or DEFAULT_TERMS
    # Captured BEFORE any recording. A prior pack variant only counts as
    # replaced if it was last seen before this cycle began; anything seen
    # during this cycle is still on the shelf and is not a shrink.
    cycle_start = now().isoformat(timespec="seconds")
    slots = ([S.get_slot(con, s) for s in slot_ids] if slot_ids
             else S.list_slots(con))
    slots = [s for s in slots if s]

    due = [s for s in slots if force or is_due(con, s)]
    skipped_slots = [s["slot_id"] for s in slots if s not in due]

    options, per_slot, all_alerts, errors = {}, {}, [], {}
    alert_budget = A.MAX_ALERTS_PER_CYCLE

    for slot in due:
        try:
            res = price_slot(con, slot, record=record)
        except PriceError as e:
            errors[slot["slot_id"]] = str(e)
            continue
        errors.update({f"{slot['slot_id']}:{k}": v
                       for k, v in res["errors"].items()})
        per_slot[slot["slot_id"]] = res

        opts = best_per_retailer(res["ranked"],
                                 category=slot.get("category"),
                                 slot_id=slot["slot_id"])
        for o in opts:
            o.slot_id = slot["slot_id"]
        if opts:
            options[slot["slot_id"]] = opts

        current = res["ranked"][0] if res["ranked"] else None
        raised = A.run_alerts(con, slot["slot_id"], res["ranked"],
                              current_pick=current, purchasing_this_cycle=True,
                              stale_before=cycle_start, budget=alert_budget)
        all_alerts.extend(raised)
        alert_budget = max(0, alert_budget - len(raised))

    assignment = optimize(options, terms) if options else None

    basket = {
        "created_at": now().isoformat(timespec="seconds"),
        "status": "AWAITING_APPROVAL",
        "slots_priced": sorted(per_slot),
        "slots_skipped_not_due": skipped_slots,
        "errors": errors,
        "alerts": all_alerts,
        "lines": [],
        "totals": {},
    }

    if assignment:
        for slot_id, opt in assignment.picks.items():
            basket["lines"].append({
                "slot_id": slot_id, "retailer": opt.retailer,
                "product_key": opt.product_key, "title": opt.title,
                "price": opt.price, "unit_price": opt.unit_price,
                "untried": S.is_untried(con, slot_id, opt.product_key),
            })
        basket["totals"] = {
            "subtotal_by_retailer": assignment.per_retailer,
            "shipping": assignment.shipping,
            "tax": {k: round(v, 2) for k, v in assignment.tax.items()},
            "landed_total": assignment.total,
            "retailers_used": assignment.retailers_used,
        }
        basket["explain"] = explain(assignment, terms)

    return basket


def stage(basket, path=None):
    """Persist the proposal. Approval is a separate, human act."""
    path = path or os.path.join(
        STAGE_DIR, f"basket_{now().strftime('%Y%m%d_%H%M%S')}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(basket, f, indent=2, default=str)
    return path


def summarize(basket) -> str:
    """Telegram-shaped summary. Untried brands are called out explicitly."""
    if not basket.get("lines"):
        msg = ["No basket staged."]
        if basket.get("errors"):
            msg.append("Errors: " + json.dumps(basket["errors"])[:300])
        if basket.get("slots_skipped_not_due"):
            msg.append(f"{len(basket['slots_skipped_not_due'])} slots not due.")
        return "\n".join(msg)

    out = ["STAGED BASKET - awaiting your approval", ""]
    by_ret = {}
    for L in basket["lines"]:
        by_ret.setdefault(L["retailer"], []).append(L)

    for r in sorted(by_ret):
        out.append(f"{r.upper()}")
        for L in by_ret[r]:
            flag = "  [UNTRIED BRAND]" if L["untried"] else ""
            up = f"  ({L['unit_price']:.3f}/100)" if L.get("unit_price") else ""
            out.append(f"  ${L['price']:>7.2f}  {L['title'][:44]}{up}{flag}")
        out.append("")

    t = basket.get("totals", {})
    for r, ship in (t.get("shipping") or {}).items():
        out.append(f"{r} shipping: " + ("FREE" if ship == 0 else f"${ship:.2f}"))
    out.append(f"LANDED TOTAL: ${t.get('landed_total', 0):.2f}")

    if basket.get("alerts"):
        out.append("")
        out.append("FLAGS:")
        for a in basket["alerts"]:
            out.append(f"  [{a['kind']}] {a['detail']} "
                       f"(${a.get('dollar_impact', 0):.2f})")

    if basket.get("errors"):
        out.append("")
        out.append(f"ERRORS: {len(basket['errors'])} "
                   f"({', '.join(list(basket['errors'])[:4])})")

    out.append("")
    out.append("Reply APPROVE to stage carts, or name a line to change.")
    return "\n".join(out)
