"""
Hazel: pantry state and photo intake.

HARD RULE: vision output is a PROPOSAL, never a fact.

Sonnet is better at counting cans than a cheap model. It is not reliable at
it, and it will not tell you when it guessed. This is the same class of
failure as the faded receipt on Beth's Bot: perceptual failures cannot be
fixed by instruction, only by mechanism. The mechanism here is that nothing
from a photo is written to confirmed pantry state until a human confirms it.

Preference order for establishing quantity:
  1. barcode scan        deterministic
  2. explicit statement  "we have three"
  3. receipt / order     what was actually delivered
  4. vision estimate     PROPOSAL ONLY

The weekly cadence does real work here: pantry state only has to be correct
once a week at a known time, which turns continuous tracking into a single
reconciliation.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import store as S

PANTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS pantry (
    slot_id        TEXT PRIMARY KEY,
    on_hand_units  REAL NOT NULL DEFAULT 0,
    unit_of_measure TEXT,
    source         TEXT NOT NULL,   -- barcode | stated | receipt | vision | decay
    confirmed      INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT NOT NULL,
    note           TEXT
);

-- Vision proposals live HERE, not in pantry, until confirmed.
CREATE TABLE IF NOT EXISTS pantry_proposals (
    proposal_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id       TEXT NOT NULL,
    proposed_units REAL,
    unit_of_measure TEXT,
    source        TEXT NOT NULL,
    model_note    TEXT,
    image_ref     TEXT,
    created_at    TEXT NOT NULL,
    resolved_at   TEXT,
    resolution    TEXT,             -- confirmed | corrected | rejected
    final_units   REAL
);

-- Observed consumption, used to build a per-slot depletion model.
-- No receipt tells you what you USED, only what you bought, so this is
-- derived from confirmed count deltas over time.
CREATE TABLE IF NOT EXISTS depletion (
    slot_id     TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    units_used   REAL,
    PRIMARY KEY (slot_id, period_start)
);
"""

DEFAULT_WEEKLY_USE = {          # bootstrap only; replaced by observed data
    "roll_goods": 300.0,        # sheets/week
    "laundry_detergent": 12.0,  # fl oz/week
    "laundry_pods": 5.0,
    "dish_soap": 3.0,
    "hand_soap": 4.0,
    "trash_bags": 5.0,
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init(con):
    con.executescript(PANTRY_SCHEMA)
    con.commit()


# ---------------------------------------------------------------------------
# Confirmed state
# ---------------------------------------------------------------------------

def set_on_hand(con, slot_id, units, source, uom=None, note=None):
    """
    Write CONFIRMED pantry state. Vision may not call this directly -- it must
    go through propose() and confirm_proposal().
    """
    assert source in ("barcode", "stated", "receipt", "decay"), (
        f"source {source!r} cannot write confirmed state; use propose()")
    con.execute("""
        INSERT INTO pantry (slot_id, on_hand_units, unit_of_measure, source,
                            confirmed, updated_at, note)
        VALUES (?,?,?,?,1,?,?)
        ON CONFLICT(slot_id) DO UPDATE SET
            on_hand_units=excluded.on_hand_units,
            unit_of_measure=COALESCE(excluded.unit_of_measure, pantry.unit_of_measure),
            source=excluded.source, confirmed=1,
            updated_at=excluded.updated_at, note=excluded.note
    """, (slot_id, float(units), uom, source, now(), note))
    con.commit()


def get_on_hand(con, slot_id):
    r = con.execute("SELECT * FROM pantry WHERE slot_id=?", (slot_id,)).fetchone()
    return dict(r) if r else None


def add_delivered(con, slot_id, units, uom=None):
    """Receipt-driven increment. What was delivered is a fact."""
    cur = get_on_hand(con, slot_id)
    base = cur["on_hand_units"] if cur else 0.0
    set_on_hand(con, slot_id, base + float(units), "receipt", uom=uom,
                note="delivery recorded")


# ---------------------------------------------------------------------------
# Vision proposals
# ---------------------------------------------------------------------------

def propose(con, slot_id, units, source="vision", uom=None, model_note=None,
            image_ref=None):
    """
    Record a proposal. This does NOT change pantry state.

    A vision count that silently became state would corrupt everything
    downstream: the plan, the list, and the cart.
    """
    con.execute("""
        INSERT INTO pantry_proposals
        (slot_id, proposed_units, unit_of_measure, source, model_note,
         image_ref, created_at)
        VALUES (?,?,?,?,?,?,?)
    """, (slot_id, None if units is None else float(units), uom, source,
          model_note, image_ref, now()))
    con.commit()
    return con.execute("SELECT last_insert_rowid()").fetchone()[0]


def open_proposals(con):
    return [dict(r) for r in con.execute(
        "SELECT * FROM pantry_proposals WHERE resolved_at IS NULL "
        "ORDER BY created_at")]


def confirm_proposal(con, proposal_id, final_units=None):
    """
    Human confirmation. final_units overrides the model's count when the human
    corrects it, and a correction is recorded as such so accuracy is auditable.
    """
    r = con.execute("SELECT * FROM pantry_proposals WHERE proposal_id=?",
                    (proposal_id,)).fetchone()
    if not r:
        return None
    proposed = r["proposed_units"]
    units = proposed if final_units is None else float(final_units)
    resolution = "confirmed" if (final_units is None or
                                 (proposed is not None and
                                  abs(units - proposed) < 1e-9)) else "corrected"
    con.execute("""UPDATE pantry_proposals
                   SET resolved_at=?, resolution=?, final_units=?
                   WHERE proposal_id=?""",
                (now(), resolution, units, proposal_id))
    # Confirmed by a human, so it is now a stated fact, not a vision guess.
    set_on_hand(con, r["slot_id"], units, "stated",
                uom=r["unit_of_measure"],
                note=f"from {r['source']} proposal #{proposal_id} ({resolution})")
    con.commit()
    return {"slot_id": r["slot_id"], "units": units, "resolution": resolution}


def reject_proposal(con, proposal_id, note=None):
    con.execute("""UPDATE pantry_proposals SET resolved_at=?, resolution='rejected'
                   WHERE proposal_id=?""", (now(), proposal_id))
    con.commit()


def vision_accuracy(con, slot_id=None):
    """
    How often the model's counts survive human review. Worth watching: if this
    drifts low, stop proposing counts and ask for a barcode instead.
    """
    q = ("SELECT resolution, COUNT(*) FROM pantry_proposals "
         "WHERE resolved_at IS NOT NULL AND source='vision'")
    args = []
    if slot_id:
        q += " AND slot_id=?"
        args.append(slot_id)
    rows = dict(con.execute(q + " GROUP BY resolution", args).fetchall())
    total = sum(rows.values())
    return {"total": total, "confirmed": rows.get("confirmed", 0),
            "corrected": rows.get("corrected", 0),
            "rejected": rows.get("rejected", 0),
            "accuracy": round(rows.get("confirmed", 0) / total, 3) if total else None}


# ---------------------------------------------------------------------------
# Depletion
# ---------------------------------------------------------------------------

def weekly_use(con, slot_id, category=None, default=None):
    """
    Observed weekly consumption, falling back to a category default.
    Real usage replaces the bootstrap estimate as confirmed counts accumulate.
    """
    rows = con.execute(
        "SELECT units_used FROM depletion WHERE slot_id=? ORDER BY period_start DESC LIMIT 8",
        (slot_id,)).fetchall()
    vals = [r[0] for r in rows if r[0] is not None]
    if vals:
        return round(sum(vals) / len(vals), 2)
    return default or DEFAULT_WEEKLY_USE.get(category or "", 0.0)


def record_depletion(con, slot_id, period_start, period_end, units_used):
    con.execute("""INSERT INTO depletion (slot_id, period_start, period_end, units_used)
                   VALUES (?,?,?,?)
                   ON CONFLICT(slot_id, period_start) DO UPDATE SET
                     period_end=excluded.period_end,
                     units_used=excluded.units_used""",
                (slot_id, period_start, period_end, float(units_used)))
    con.commit()


def weeks_remaining(con, slot_id, category=None):
    cur = get_on_hand(con, slot_id)
    if not cur:
        return None
    use = weekly_use(con, slot_id, category)
    if not use:
        return None
    return round(cur["on_hand_units"] / use, 1)


def needs_restock(con, slot_id, category=None, lead_weeks=1.5):
    """
    True when stock will run out before the next delivery lands. Deliberately
    conservative: running out is worse than buying a week early.
    """
    wr = weeks_remaining(con, slot_id, category)
    if wr is None:
        return True  # unknown state -> ask rather than assume stocked
    return wr <= lead_weeks


def stale_slots(con, days=10):
    """Slots whose count is old enough to be untrustworthy."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
        timespec="seconds")
    return [r[0] for r in con.execute(
        "SELECT slot_id FROM pantry WHERE updated_at < ?", (cutoff,))]


VISION_PROMPT = """You are counting household inventory from a photograph.

Report ONLY what is clearly visible. For each product you can identify:
  - product name as printed on the packaging
  - number of units you can actually SEE
  - your confidence: high, medium, or low

Rules you must follow:
- Do NOT infer items hidden behind or beneath others. Count what is visible.
- If packaging is turned away, obscured, or unreadable, say so and mark low.
- If you are unsure whether two items are the same product, list them separately.
- Never estimate a total for the shelf. Partial counts are useful; invented
  totals are not.
- Report remaining fill level for partially used containers as a fraction,
  and mark it low confidence unless a fill line is clearly visible.

Return JSON only:
{"items":[{"name":"...","count":N,"confidence":"high|medium|low","note":"..."}],
 "obscured": true|false,
 "note":"anything that would make these counts unreliable"}
"""


def parse_vision_response(raw):
    """Parse the model's JSON. A malformed response yields nothing, not a guess."""
    if not raw:
        return []
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("```")[1]
        if txt.startswith("json"):
            txt = txt[4:]
    try:
        data = json.loads(txt)
    except Exception:
        return []
    items = data.get("items") or []
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("name"):
            continue
        out.append({
            "name": it.get("name"),
            "count": it.get("count"),
            "confidence": (it.get("confidence") or "low").lower(),
            "note": it.get("note"),
            "obscured": bool(data.get("obscured")),
        })
    return out


def ingest_photo_result(con, items, slot_map, image_ref=None):
    """
    Turn parsed vision output into PROPOSALS.

    slot_map maps a recognized product name to a slot_id. Unmapped items are
    returned for the human to map -- they are never guessed into a slot.
    """
    created, unmapped = [], []
    for it in items:
        slot_id = slot_map.get((it["name"] or "").lower())
        if not slot_id:
            unmapped.append(it)
            continue
        note = f"confidence={it['confidence']}"
        if it.get("obscured"):
            note += "; view obscured"
        if it.get("note"):
            note += f"; {it['note']}"
        pid = propose(con, slot_id, it.get("count"), source="vision",
                      model_note=note, image_ref=image_ref)
        created.append({"proposal_id": pid, "slot_id": slot_id,
                        "count": it.get("count"),
                        "confidence": it["confidence"]})
    return {"proposals": created, "unmapped": unmapped}
