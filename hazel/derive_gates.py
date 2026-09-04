"""Derive relevance gates for every slot from its own identifier.

Most slot_ids state their own constraint. `broccoli_fresh` means broccoli and
not frozen. `ground_beef` means ground beef. The gates were empty on 67 of 76
slots purely because nobody typed them in, which is how guacamole won the
avocado slot.

DESIGN RULES

1. Derive, never guess. A slot whose head noun cannot be resolved confidently
   is REPORTED, not gated. A wrong gate silently empties a slot, which is a
   worse failure than no gate because it looks like "no results" rather than
   an error.

2. Never overwrite an existing gate. The nine hand-written ones were set
   against observed misfires and encode more than a name does.

3. Stem for prefix matching. store._must_have_pattern prefix-matches terms of
   5+ chars, so stems must be the common prefix of singular and plural:
   strawberries -> strawberr, covering strawberry and strawberries.

4. Exclusions come from explicit modifiers only. `_fresh` excludes frozen.
   Nothing is excluded on a hunch.

Default is --dry-run. Pass --apply to write.
"""
import json
import re
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

# Suffix modifiers: (must_have additions, must_not_have additions)
MODIFIERS = {
    "fresh":   ([], ["frozen", "canned", "dried", "freeze dried"]),
    "frozen":  (["frozen"], ["fresh", "canned"]),
    "canned":  (["can"], ["fresh", "frozen"]),
    "sliced":  (["slice"], ["shredded", "block"]),
    "shredded": (["shred"], ["sliced", "block"]),
    "ground":  (["ground"], ["whole bean", "wholebean"]),
    "whole":   ([], ["ground", "sliced"]),
    "zero":    ([], ["sugar"]),
    "variety": ([], []),
}

# Words that are never the head noun of a product.
STOPWORDS = {"fresh", "frozen", "canned", "sliced", "shredded", "ground",
             "whole", "zero", "variety", "deli", "bag", "box", "pack",
             "liquid", "powder", "pods", "sticks", "kernels"}

# Head nouns too generic to gate on safely. Reported, not gated.
TOO_GENERIC = {"cereal", "bread", "salsa", "buns", "cheese", "sauce",
               "vitamins", "multivitamin", "wipes", "soup", "juice",
               "detergent", "cleaner", "vegetables", "chips", "flour"}


def stem(word):
    """Common prefix of singular and plural, for prefix matching."""
    w = word.lower()
    if w.endswith("ies"):
        return w[:-3]          # strawberries -> strawberr
    if w.endswith("ses") or w.endswith("xes") or w.endswith("ches"):
        return w[:-2]          # boxes -> box
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]          # bananas -> banana
    return w


def derive(slot_id, display_name):
    """Return (must_have, must_not_have, note). Empty lists mean 'skip'."""
    parts = [p for p in slot_id.split("_") if p]
    must, never = [], []

    tail = parts[-1] if parts else ""
    if tail in MODIFIERS:
        add_h, add_n = MODIFIERS[tail]
        must += add_h
        never += add_n
        parts = parts[:-1]

    core = [p for p in parts if p not in STOPWORDS]
    if not core:
        return [], [], "no head noun after modifiers"

    head = " ".join(core)
    if head in TOO_GENERIC or core[-1] in TOO_GENERIC:
        return [], [], f"head noun {head!r} too generic to gate safely"

    if len(core) == 1:
        must.append(stem(core[0]))
    else:
        # Multi-word ids usually name the product exactly: ground_beef,
        # roast_beef, toilet_paper. Gate on the last word's stem plus the
        # first, which is the distinguishing modifier.
        must.append(stem(core[-1]))
        if len(core[0]) >= 4:
            must.append(stem(core[0]))

    must = [m for m in dict.fromkeys(must) if len(m) >= 3]
    never = list(dict.fromkeys(never))
    return must, never, ""


con = sqlite3.connect(DB)
rows = con.execute(
    "SELECT slot_id, display_name, must_have, must_not_have, active "
    "FROM slots ORDER BY slot_id").fetchall()

gated, derived, skipped = [], [], []
for slot_id, display_name, mh, mnh, active in rows:
    existing_h = json.loads(mh or "[]")
    existing_n = json.loads(mnh or "[]")
    if existing_h or existing_n:
        gated.append(slot_id)
        continue
    must, never, note = derive(slot_id, display_name or slot_id)
    if not must and not never:
        skipped.append((slot_id, note))
        continue
    derived.append((slot_id, must, never))

print(f"already gated : {len(gated)}")
print(f"derivable     : {len(derived)}")
print(f"needs a human : {len(skipped)}")

print("\n--- DERIVED ---")
for slot_id, must, never in derived:
    n = f"  not={never}" if never else ""
    print(f"  {slot_id:24} have={must}{n}")

print("\n--- SKIPPED, gate these by hand if they misfire ---")
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
