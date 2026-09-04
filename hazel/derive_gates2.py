"""Derive relevance gates for every slot from its own identifier. v2.

Fixes over v1, all found by reading v1's dry run:

  1. stem("tomatoes") returned "tomatoe", a misspelling that missed the
     singular. "oes" now drops only the "es".
  2. stem("fries") returned "fr", below the length floor, so the gate fell
     back to the wrong head noun and sweet_potato_fries gated on "sweet".
     An "ies" stem shorter than 4 chars now keeps the original word.
  3. STOPWORDS stripped "ground" out of ground_beef and "deli" out of
     turkey_deli, deleting the one word that distinguished the product.
     Leading modifiers are now kept. Only true form words (liquid, whole,
     bag) are dropped, and only when they lead.
  4. A generic tail like "cheese" or "sauce" no longer forces a skip when a
     distinctive modifier is present: parmesan_cheese gates on "parmesan".

Design rules unchanged: derive rather than guess, never overwrite an existing
gate, stem for prefix matching, exclusions only from explicit modifiers.

Default is dry run. Pass --apply to write.
"""
import json
import sqlite3
import sys
import os

# Data lives next to the code. /data/hazel on Railway and /opt/data/hazel
# on Portal are both 'the directory this file sits in', so this default is
# correct on either without an environment variable. Set HAZEL_HOME to
# split code from data.
HAZEL_HOME = os.environ.get(
    "HAZEL_HOME", os.path.dirname(os.path.abspath(__file__)))

DB = os.path.join(HAZEL_HOME, "hazel.db")
APPLY = "--apply" in sys.argv

MODIFIERS = {
    "fresh":    ([], ["frozen", "canned", "dried", "freeze dried"]),
    "frozen":   (["frozen"], ["fresh", "canned"]),
    "canned":   (["can"], ["fresh", "frozen"]),
    "sliced":   (["slice"], ["shredded", "block"]),
    "shredded": (["shred"], ["sliced", "block"]),
    "zero":     ([], ["sugar"]),
    "variety":  ([], []),
}

# Dropped only when they LEAD. They describe the format, not the product.
FORM_WORDS = {"liquid", "whole", "bag", "box", "pack", "bottle", "and"}

# Nouns so broad that requiring them proves nothing. If the WHOLE core is
# generic the slot is reported instead of gated.
TOO_GENERIC = {"cheese", "sauce", "juice", "soup", "bread", "flour", "cereal",
               "salsa", "buns", "vitamins", "multivitamin", "wipes", "chips",
               "detergent", "cleaner", "vegetables", "sticks", "drink"}


def stem(word):
    """Common prefix of singular and plural, for prefix matching."""
    w = word.lower()
    if w.endswith("oes"):
        return w[:-2]                      # tomatoes -> tomato
    if w.endswith("ies"):
        cut = w[:-3]
        return cut if len(cut) >= 4 else w  # strawberries -> strawberr; fries stays
    if w.endswith("ses") or w.endswith("xes") or w.endswith("ches"):
        return w[:-2]                      # boxes -> box
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]                      # bananas -> banana
    return w


def derive(slot_id, display_name):
    """Return (must_have, must_not_have, note). Empty lists mean skip."""
    parts = [p for p in slot_id.split("_") if p]
    must, never = [], []

    if parts and parts[-1] in MODIFIERS:
        add_h, add_n = MODIFIERS[parts[-1]]
        must += add_h
        never += add_n
        parts = parts[:-1]

    while parts and parts[0] in FORM_WORDS:
        parts = parts[1:]
    core = [p for p in parts if p not in FORM_WORDS]
    if not core:
        return [], [], "no head noun after modifiers"

    generic = [w for w in core if w in TOO_GENERIC]
    distinctive = [w for w in core if w not in TOO_GENERIC]

    if not distinctive:
        return [], [], f"all of {' '.join(core)!r} is generic"

    if generic:
        # Gate on what distinguishes it, not the generic noun.
        picks = [w for w in distinctive if len(w) >= 5]
        if not picks:
            return [], [], (f"{' '.join(core)!r}: distinguishing word "
                            f"{distinctive[0]!r} too short to gate on")
        must += [stem(w) for w in picks[:2]]
    elif len(core) == 1:
        must.append(stem(core[0]))
    else:
        must.append(stem(core[-1]))
        others = [w for w in core[:-1] if len(w) >= 4]
        if others:
            must.append(stem(others[0]))

    must = [m for m in dict.fromkeys(must) if len(m) >= 3]
    never = list(dict.fromkeys(never))
    if not must:
        return [], [], "no term survived the length floor"
    return must, never, ""


con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT slot_id, display_name, must_have, must_not_have "
    "FROM slots ORDER BY slot_id").fetchall()

gated, derived, skipped = [], [], []
for slot_id, display_name, mh, mnh in rows:
    if json.loads(mh or "[]") or json.loads(mnh or "[]"):
        gated.append(slot_id)
        continue
    must, never, note = derive(slot_id, display_name or slot_id)
    if not must and not never:
        skipped.append((slot_id, note))
    else:
        derived.append((slot_id, must, never))

print(f"already gated : {len(gated)}")
print(f"derivable     : {len(derived)}")
print(f"needs a human : {len(skipped)}")

print("\n--- DERIVED ---")
for slot_id, must, never in derived:
    n = f"  not={never}" if never else ""
    print(f"  {slot_id:24} have={must}{n}")

print("\n--- SKIPPED ---")
for slot_id, note in skipped:
    print(f"  {slot_id:24} {note}")

if APPLY:
    for slot_id, must, never in derived:
        con.execute(
            "UPDATE slots SET must_have=?, must_not_have=? WHERE slot_id=?",
            (json.dumps(must), json.dumps(never), slot_id))
    con.commit()
    total = con.execute(
        "SELECT COUNT(*) FROM slots WHERE must_have != '[]'").fetchone()[0]
    print(f"\nAPPLIED. slots with a must_have gate: {total}")
else:
    print("\nDRY RUN. Nothing written. Re-run with --apply.")
