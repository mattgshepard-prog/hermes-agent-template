"""
Hazel: persistent store.

Two kinds of data with OPPOSITE durability profiles live here:

  price_observations  high volume, regenerable, 90-day raw retention
  slot_verdicts       ~150 rows ever, permanent, NOT regenerable at any price

The verdict table encodes months of actually living with products. You cannot
re-derive "the Amazon Basics TP was bad" from anything. Back it up.

ARCHITECTURAL RULE: verdicts are DATA, not prose. `rejected` candidates are
removed by `filter_candidates()` before the model ever sees the list. A verdict
written into a SKILL.md is a rule the model can fail to weight; a verdict
enforced by a WHERE clause cannot be argued with.
"""

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone

# Data lives next to the code. /data/hazel on Railway and /opt/data/hazel
# on Portal are both 'the directory this file sits in', so this default is
# correct on either without an environment variable. Set HAZEL_HOME to
# split code from data.
HAZEL_HOME = os.environ.get(
    "HAZEL_HOME", os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.environ.get("HAZEL_DB", os.path.join(HAZEL_HOME, "hazel.db"))

SCHEMA = """
PRAGMA journal_mode=WAL;

-- A need slot is a FUNCTION the household needs filled, not a product.
-- This is what makes private label compete on equal footing with name brands.
CREATE TABLE IF NOT EXISTS slots (
    slot_id         TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    category        TEXT NOT NULL,
    unit_of_measure TEXT NOT NULL,
    search_terms    TEXT NOT NULL,          -- JSON list
    must_have       TEXT DEFAULT '[]',      -- JSON list of required attributes
    must_not_have   TEXT DEFAULT '[]',      -- JSON list of disqualifiers
    brand_policy    TEXT DEFAULT 'open',    -- open | locked | preferred
    locked_brand    TEXT,
    cadence         TEXT DEFAULT 'monthly', -- weekly | monthly | quarterly
    max_stock       REAL,                   -- caps buy-ahead on a low price
    active          INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL
);

-- One row per (slot, retailer, product) price reading.
CREATE TABLE IF NOT EXISTS price_observations (
    obs_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id         TEXT NOT NULL,
    retailer        TEXT NOT NULL,          -- kroger | walmart | amazon
    product_key     TEXT NOT NULL,          -- UPC / item_id / ASIN
    title           TEXT NOT NULL,
    brand           TEXT,
    price           REAL NOT NULL,          -- what you'd actually pay
    regular_price   REAL,
    promo_price     REAL,
    -- Structured pack size. NEVER a string: shrinkflation is only visible
    -- when pack size is a number you can diff over time.
    pack_count      INTEGER,
    unit_size       REAL,
    unit_of_measure TEXT,
    total_units     REAL,
    unit_price      REAL,                   -- price per 100 units, or NULL
    confidence      TEXT NOT NULL,
    parse_notes     TEXT,
    observed_at     TEXT NOT NULL,
    -- Retailer stock signal. NULL means the retailer said nothing, which is
    -- NOT the same as in stock. record_observation() has always written this
    -- and filter_candidates() reads it; the column was missing from the
    -- schema, so a virgin install failed on the first observation write.
    in_stock        INTEGER,
    FOREIGN KEY (slot_id) REFERENCES slots(slot_id)
);
CREATE INDEX IF NOT EXISTS idx_obs_slot_time
    ON price_observations(slot_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_product
    ON price_observations(slot_id, retailer, product_key, observed_at);

-- Weekly rollup so raw rows can be pruned at 90 days while the baseline
-- survives. Promo cycles run 4-8 weeks, so the baseline must outlive them or
-- every return to regular price reads as a spike.
CREATE TABLE IF NOT EXISTS price_rollups (
    slot_id         TEXT NOT NULL,
    retailer        TEXT NOT NULL,
    product_key     TEXT NOT NULL,
    week_start      TEXT NOT NULL,
    min_unit_price  REAL,
    med_unit_price  REAL,
    obs_count       INTEGER,
    PRIMARY KEY (slot_id, retailer, product_key, week_start)
);

-- THE irreplaceable table.
CREATE TABLE IF NOT EXISTS slot_verdicts (
    slot_id       TEXT NOT NULL,
    product_key   TEXT NOT NULL,
    brand         TEXT,
    title         TEXT,
    verdict       TEXT NOT NULL,   -- keep | acceptable | rejected | untried
    reason        TEXT,
    decided_at    TEXT NOT NULL,
    decided_by    TEXT DEFAULT 'matt',
    PRIMARY KEY (slot_id, product_key)
);

-- Pack size per product line, for shrinkflation. The UPC CHANGES when pack
-- size changes, so tracking by product line is the only way to see the old
-- variant vanish and a smaller one appear at the same shelf price.
CREATE TABLE IF NOT EXISTS pack_history (
    slot_id       TEXT NOT NULL,
    retailer      TEXT NOT NULL,   -- a Walmart pack vs a Kroger pack is NOT shrinkflation
    product_line  TEXT NOT NULL,   -- brand + line, e.g. "bounty select-a-size"
    product_key   TEXT NOT NULL,
    total_units   REAL,
    price         REAL,
    unit_price    REAL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    PRIMARY KEY (slot_id, retailer, product_line, product_key)
);

-- Alerts raised, so the same one is not raised twice and baselines can be
-- reset after a confirmed shrinkflation event.
CREATE TABLE IF NOT EXISTS alerts (
    alert_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id       TEXT NOT NULL,
    kind          TEXT NOT NULL,   -- shrinkflation | sustained_rise | better_alt | unusually_low
    detail        TEXT,
    dollar_impact REAL,
    raised_at     TEXT NOT NULL,
    resolved_at   TEXT,
    resolution    TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None):
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    con = sqlite3.connect(p, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------

# Store brands only one retailer can ever sell. Locking a commodity slot to
# one of these silently removes the other two rails from the comparison: they
# cannot win at any price, and the basket looks like a fair contest.
RETAILER_EXCLUSIVE_BRANDS = {
    "great value": "walmart", "equate": "walmart", "mainstays": "walmart",
    "freshness guaranteed": "walmart", "marketside": "walmart",
    "kroger": "kroger", "simple truth": "kroger", "private selection": "kroger",
    "heritage farm": "kroger", "big k": "kroger",
    "amazon basics": "amazon", "amazon grocery": "amazon",
    "amazon saver": "amazon", "amazon fresh": "amazon", "solimo": "amazon",
    "happy belly": "amazon",
}


def exclusive_retailer(brand):
    """Return the only retailer that sells this brand, or None."""
    b = (brand or "").lower()
    for name, retailer in RETAILER_EXCLUSIVE_BRANDS.items():
        if name in b:
            return retailer
    return None


def upsert_slot(con, slot_id, display_name, category, unit_of_measure,
                search_terms, must_have=None, must_not_have=None,
                brand_policy="open", locked_brand=None, cadence="monthly",
                max_stock=None, allow_exclusive=False):
    # A retailer-exclusive lock is almost always a mistake on a commodity
    # slot. Refuse it loudly rather than let it quietly decide the basket.
    if brand_policy == "locked" and locked_brand and not allow_exclusive:
        excl = exclusive_retailer(locked_brand)
        if excl:
            raise ValueError(
                f"{slot_id}: locking to {locked_brand!r} restricts this slot "
                f"to {excl} only; the other rails cannot compete. Pass "
                f"allow_exclusive=True if that is genuinely intended.")
    con.execute("""
        INSERT INTO slots (slot_id, display_name, category, unit_of_measure,
                           search_terms, must_have, must_not_have, brand_policy,
                           locked_brand, cadence, max_stock, active, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?)
        ON CONFLICT(slot_id) DO UPDATE SET
            display_name=excluded.display_name,
            category=excluded.category,
            unit_of_measure=excluded.unit_of_measure,
            search_terms=excluded.search_terms,
            must_have=excluded.must_have,
            must_not_have=excluded.must_not_have,
            brand_policy=excluded.brand_policy,
            locked_brand=excluded.locked_brand,
            cadence=excluded.cadence,
            max_stock=excluded.max_stock
    """, (slot_id, display_name, category, unit_of_measure,
          json.dumps(search_terms), json.dumps(must_have or []),
          json.dumps(must_not_have or []), brand_policy, locked_brand,
          cadence, max_stock, now()))
    con.commit()


def get_slot(con, slot_id):
    r = con.execute("SELECT * FROM slots WHERE slot_id=?", (slot_id,)).fetchone()
    return dict(r) if r else None


def list_slots(con, cadence=None, active_only=True):
    q = "SELECT * FROM slots WHERE 1=1"
    args = []
    if active_only:
        q += " AND active=1"
    if cadence:
        q += " AND cadence=?"
        args.append(cadence)
    return [dict(r) for r in con.execute(q + " ORDER BY slot_id", args)]


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------

def record_observation(con, slot_id, retailer, product_key, title, price,
                       pack, unit_price_val, brand=None, regular_price=None,
                       promo_price=None, in_stock=None):
    con.execute("""
        INSERT INTO price_observations
        (slot_id, retailer, product_key, title, brand, price, regular_price,
         promo_price, pack_count, unit_size, unit_of_measure, total_units,
         unit_price, confidence, parse_notes, observed_at, in_stock)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (slot_id, retailer, product_key, title, brand, price, regular_price,
          promo_price, pack.count, pack.unit_size, pack.unit_of_measure,
          pack.total_units, unit_price_val, pack.confidence, pack.notes,
          now(), in_stock))
    con.commit()


def trailing_median(con, slot_id, product_key=None, days=90):
    """
    Median unit price over the window. Uses raw observations plus rollups so a
    90-day raw retention still yields a baseline older than a promo cycle.
    """
    q = ("SELECT unit_price FROM price_observations WHERE slot_id=? "
         "AND unit_price IS NOT NULL AND observed_at >= datetime('now', ?)")
    args = [slot_id, f"-{days} days"]
    if product_key:
        q += " AND product_key=?"
        args.append(product_key)
    vals = sorted(r[0] for r in con.execute(q, args))
    if not vals:
        q2 = ("SELECT med_unit_price FROM price_rollups WHERE slot_id=? "
              "AND med_unit_price IS NOT NULL")
        a2 = [slot_id]
        if product_key:
            q2 += " AND product_key=?"
            a2.append(product_key)
        vals = sorted(r[0] for r in con.execute(q2, a2))
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def observation_count(con, slot_id=None):
    if slot_id:
        return con.execute(
            "SELECT COUNT(*) FROM price_observations WHERE slot_id=?",
            (slot_id,)).fetchone()[0]
    return con.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0]


# ---------------------------------------------------------------------------
# Verdicts  -- the mechanism, not the prose
# ---------------------------------------------------------------------------

def set_verdict(con, slot_id, product_key, verdict, brand=None, title=None,
                reason=None, decided_by="matt"):
    assert verdict in ("keep", "acceptable", "rejected", "untried"), verdict
    con.execute("""
        INSERT INTO slot_verdicts
        (slot_id, product_key, brand, title, verdict, reason, decided_at, decided_by)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(slot_id, product_key) DO UPDATE SET
            verdict=excluded.verdict, reason=excluded.reason,
            decided_at=excluded.decided_at, decided_by=excluded.decided_by,
            brand=COALESCE(excluded.brand, slot_verdicts.brand),
            title=COALESCE(excluded.title, slot_verdicts.title)
    """, (slot_id, product_key, brand, title, verdict, reason, now(), decided_by))
    con.commit()


def get_verdicts(con, slot_id):
    return {r["product_key"]: dict(r) for r in con.execute(
        "SELECT * FROM slot_verdicts WHERE slot_id=?", (slot_id,))}


def _must_have_pattern(term):
    """
    Inflection-tolerant pattern for a must_have term.

    Long stems prefix-match so 'strawberr' covers strawberry and
    strawberries. Short terms stay anchored with an optional plural so
    'egg' cannot match 'eggplant'.
    """
    t = re.escape(term.lower())
    if len(term) >= 5:
        return r"\b" + t
    return r"\b" + t + r"(?:s|es)?\b"


def filter_candidates(con, slot_id, candidates):
    """
    Remove rejected products BEFORE ranking, and before the model sees them.

    This is the enforcement point. A rejected brand is not "discouraged" or
    "deprioritized" -- it is absent. Returns (kept, dropped).
    """
    verdicts = get_verdicts(con, slot_id)
    slot = get_slot(con, slot_id) or {}
    locked = slot.get("locked_brand")
    policy = slot.get("brand_policy", "open")
    must_not = json.loads(slot.get("must_not_have") or "[]")
    must_have = json.loads(slot.get("must_have") or "[]")

    kept, dropped = [], []
    for c in candidates:
        pk = c.get("product_key")
        title = (c.get("title") or "").lower()
        v = verdicts.get(pk, {}).get("verdict")

        if v == "rejected":
            dropped.append((c, "verdict:rejected"))
            continue
        # A definite out-of-stock cannot win on price. Unknown is NOT
        # treated as out: Amazon never reports stock and blanking it
        # would delete a whole rail.
        if c.get("in_stock") is False:
            dropped.append((c, "out_of_stock"))
            continue
        if policy == "locked" and locked:
            if locked.lower() not in title and locked.lower() != (
                    c.get("brand") or "").lower():
                dropped.append((c, f"brand_policy:locked to {locked}"))
                continue
        # Word-boundary match. A substring check makes "scented" match
        # "UNscented" and silently rejects the exact product wanted.
        bad = next((d for d in must_not
                    if re.search(r"\b" + re.escape(d.lower()) + r"\b", title)), None)
        if bad:
            dropped.append((c, f"must_not_have:{bad}"))
            continue
        # must_have is the other half of the fence. Without it a slot can be
        # won by a product that merely fails to contain a banned word.
        missing = next((m for m in must_have
                        if not re.search(_must_have_pattern(m), title)), None)
        if missing:
            dropped.append((c, f"must_have:{missing}"))
            continue
        kept.append(c)
    return kept, dropped


def is_untried(con, slot_id, product_key):
    v = get_verdicts(con, slot_id).get(product_key)
    return v is None or v.get("verdict") == "untried"


# ---------------------------------------------------------------------------
# Pack history / shrinkflation
# ---------------------------------------------------------------------------

def record_pack(con, slot_id, retailer, product_line, product_key, total_units,
                price, unit_price_val):
    row = con.execute("""SELECT first_seen FROM pack_history
                         WHERE slot_id=? AND retailer=? AND product_line=?
                           AND product_key=?""",
                      (slot_id, retailer, product_line, product_key)).fetchone()
    first = row["first_seen"] if row else now()
    con.execute("""
        INSERT INTO pack_history
        (slot_id, retailer, product_line, product_key, total_units, price,
         unit_price, first_seen, last_seen)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(slot_id, retailer, product_line, product_key) DO UPDATE SET
            total_units=excluded.total_units, price=excluded.price,
            unit_price=excluded.unit_price, last_seen=excluded.last_seen
    """, (slot_id, retailer, product_line, product_key, total_units, price,
          unit_price_val, first, now()))
    con.commit()


def pack_variants(con, slot_id, product_line, retailer=None,
                  stale_before=None):
    """
    Pack variants for a product line.

    retailer scopes the comparison: the same line at two retailers is two
    catalogs, not a shrink.

    stale_before returns ONLY variants not seen since that timestamp. This is
    what distinguishes a genuine shrink (old variant disappeared, smaller one
    took its place) from two pack sizes simply being sold side by side.
    """
    q = "SELECT * FROM pack_history WHERE slot_id=? AND product_line=?"
    args = [slot_id, product_line]
    if retailer:
        q += " AND retailer=?"
        args.append(retailer)
    if stale_before:
        q += " AND last_seen < ?"
        args.append(stale_before)
    return [dict(r) for r in con.execute(q + " ORDER BY first_seen, rowid", args)]


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

def raise_alert(con, slot_id, kind, detail, dollar_impact=None):
    cur = con.execute("""
        SELECT alert_id FROM alerts
        WHERE slot_id=? AND kind=? AND detail=? AND resolved_at IS NULL
    """, (slot_id, kind, detail)).fetchone()
    if cur:
        return None  # already open; do not re-raise
    con.execute("""INSERT INTO alerts (slot_id, kind, detail, dollar_impact, raised_at)
                   VALUES (?,?,?,?,?)""",
                (slot_id, kind, detail, dollar_impact, now()))
    con.commit()
    return con.execute("SELECT last_insert_rowid()").fetchone()[0]


def open_alerts(con, limit=None):
    q = ("SELECT * FROM alerts WHERE resolved_at IS NULL "
         "ORDER BY COALESCE(dollar_impact,0) DESC")
    if limit:
        q += f" LIMIT {int(limit)}"
    return [dict(r) for r in con.execute(q)]


def resolve_alert(con, alert_id, resolution):
    con.execute("UPDATE alerts SET resolved_at=?, resolution=? WHERE alert_id=?",
                (now(), resolution, alert_id))
    con.commit()


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def rollup_and_prune(con, raw_days=90):
    """Collapse raw observations older than raw_days into weekly rollups."""
    con.execute("""
        INSERT INTO price_rollups
            (slot_id, retailer, product_key, week_start, min_unit_price,
             med_unit_price, obs_count)
        SELECT slot_id, retailer, product_key,
               date(observed_at, 'weekday 0', '-6 days') AS wk,
               MIN(unit_price), AVG(unit_price), COUNT(*)
        FROM price_observations
        WHERE unit_price IS NOT NULL AND observed_at < datetime('now', ?)
        GROUP BY slot_id, retailer, product_key, wk
        ON CONFLICT(slot_id, retailer, product_key, week_start) DO NOTHING
    """, (f"-{raw_days} days",))
    cur = con.execute("DELETE FROM price_observations WHERE observed_at < datetime('now', ?)",
                      (f"-{raw_days} days",))
    con.commit()
    return cur.rowcount


def export_verdicts(con, path):
    """
    Flat-file export of the ONE table that cannot be regenerated.
    Human-readable so the preference model can be eyeballed against reality.
    """
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM slot_verdicts ORDER BY slot_id, product_key")]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"exported_at": now(), "count": len(rows), "verdicts": rows},
                  f, indent=2)
    return len(rows)
