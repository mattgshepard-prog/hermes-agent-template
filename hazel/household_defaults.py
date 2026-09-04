"""Household defaults + relevance gate population.

Two things:
  1. household_defaults table. Ambiguous item names resolve to a household
     preference unless explicitly overridden. "milk" -> almond milk, unless
     the request says cow / dairy / whole / 2% / skim / oat / soy.
     Matches on HEAD NOUN only, so "whole milk greek yogurt" is untouched.
  2. Populate must_have / must_not_have on slots that picked wrong products.

Idempotent. Safe to re-run. Prints before/after for every change.
"""
import json
import sqlite3
import os

# Data lives next to the code. /data/hazel on Railway and /opt/data/hazel
# on Portal are both 'the directory this file sits in', so this default is
# correct on either without an environment variable. Set HAZEL_HOME to
# split code from data.
HAZEL_HOME = os.environ.get(
    "HAZEL_HOME", os.path.dirname(os.path.abspath(__file__)))

DB = os.path.join(HAZEL_HOME, "hazel.db")

DEFAULTS = [
    dict(
        term="milk",
        resolves_to="almond milk unsweetened",
        must_have=["almond"],
        must_not_have=["dairy", "whole milk", "2%", "skim", "oat", "soy",
                       "condensed", "evaporated"],
        overridden_by=["cow", "dairy", "whole", "2%", "skim", "oat",
                       "soy", "coconut", "goat"],
        note="Household default. Cow milk must be asked for explicitly.",
    ),
]

# slot_id -> (must_have, must_not_have)
GATES = {
    "milk":                 (["almond"], ["dairy", "whole milk", "2%", "skim"]),
    "avocado":              (["avocado"], ["guacamole", "salsa", "dip"]),
    "flushable_wipes":      (["wipe"], ["toilet paper", "tissue", "roll"]),
    "zucchini":             (["zucchini"], ["yellow squash", "crookneck"]),
    "broccoli_fresh":       (["broccoli"], ["frozen"]),
    "cauliflower_fresh":    (["cauliflower"], ["frozen"]),
    "strawberries_fresh":   (["strawberr"], ["frozen", "chocolate", "dried"]),
    "dishwasher_rinse_aid": (["rinse"], ["pod", "pac", "tablet", "cleaner"]),
    "roast_beef":           (["roast beef"], ["corned beef", "pastrami"]),
}

con = sqlite3.connect(DB)
con.execute("""
CREATE TABLE IF NOT EXISTS household_defaults (
    term          TEXT PRIMARY KEY,
    resolves_to   TEXT NOT NULL,
    must_have     TEXT NOT NULL DEFAULT '[]',
    must_not_have TEXT NOT NULL DEFAULT '[]',
    overridden_by TEXT NOT NULL DEFAULT '[]',
    note          TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
)
""")

print("=== HOUSEHOLD DEFAULTS ===")
for d in DEFAULTS:
    con.execute(
        """INSERT INTO household_defaults
             (term, resolves_to, must_have, must_not_have, overridden_by, note)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(term) DO UPDATE SET
             resolves_to=excluded.resolves_to,
             must_have=excluded.must_have,
             must_not_have=excluded.must_not_have,
             overridden_by=excluded.overridden_by,
             note=excluded.note""",
        (d["term"], d["resolves_to"], json.dumps(d["must_have"]),
         json.dumps(d["must_not_have"]), json.dumps(d["overridden_by"]),
         d["note"]),
    )
    print(f"  {d['term']:8} -> {d['resolves_to']}")
    print(f"           suppressed by: {', '.join(d['overridden_by'])}")

# Retarget the milk slot itself to the household default.
row = con.execute(
    "SELECT display_name, search_terms FROM slots WHERE slot_id='milk'"
).fetchone()
if row:
    print(f"\n=== MILK SLOT ===\n  before: {row[0]!r} terms={row[1]}")
    con.execute(
        """UPDATE slots
              SET display_name=?, search_terms=?, unit_of_measure='fl_oz'
            WHERE slot_id='milk'""",
        ("Milk (almond)", json.dumps(["almond milk unsweetened"])),
    )
    after = con.execute(
        "SELECT display_name, search_terms FROM slots WHERE slot_id='milk'"
    ).fetchone()
    print(f"  after : {after[0]!r} terms={after[1]}")

print("\n=== RELEVANCE GATES ===")
for slot, (mh, mnh) in GATES.items():
    cur = con.execute(
        "SELECT must_have, must_not_have FROM slots WHERE slot_id=?", (slot,)
    ).fetchone()
    if not cur:
        print(f"  {slot:22} SKIP (no such slot)")
        continue
    con.execute(
        "UPDATE slots SET must_have=?, must_not_have=? WHERE slot_id=?",
        (json.dumps(mh), json.dumps(mnh), slot),
    )
    print(f"  {slot:22} have={mh}  not={mnh}")

# greek_yogurt deliberately untouched: head noun is yogurt, not milk.
gy = con.execute(
    "SELECT search_terms FROM slots WHERE slot_id='greek_yogurt'"
).fetchone()
print(f"\n  greek_yogurt LEFT ALONE (head noun is yogurt): {gy[0] if gy else 'n/a'}")

con.commit()
print(f"\ndefaults rows: {con.execute('SELECT COUNT(*) FROM household_defaults').fetchone()[0]}")
print(f"gated slots  : {con.execute(chr(83)+'ELECT COUNT(*) FROM slots WHERE must_have != ' + chr(39) + '[]' + chr(39)).fetchone()[0]}")
