"""
Audit (and optionally repair) brand locks that contradict their own slot.

THE RULE, and why it is mechanical rather than a judgement call
    Hazel's design: pick the cheapest brand UNLESS a brand is specified.

    A slot that specifies a brand names it in the search term. Every genuine
    lock does: "tide pods 3-in-1", "colgate total whitening", "icepure ukf8001",
    "starbucks sumatra dark whole bean".

    A lock whose brand does NOT appear in its own search term is self-
    contradictory. The term says "any bread flour"; the lock says "Walmart
    only". Those cannot both be the intent, and the term is the one that
    matches the design. Such a lock is a brand LEARNED from a winning result
    and written back as a constraint, not a preference anyone stated.

    Consequence: a slot locked to a retailer-exclusive private label
    (Great Value = Walmart, FOVURTE = Amazon marketplace) is not running a
    price comparison at all. It is a standing order at one retailer wearing a
    comparison's clothes.

DEFAULT IS AUDIT ONLY. Nothing is written without --apply.

USAGE
    python3 fix_brand_locks.py                     # audit
    python3 fix_brand_locks.py --apply             # unlock all suspects
    python3 fix_brand_locks.py --apply --only pickles canned_tomatoes
    python3 fix_brand_locks.py --apply --exclude cereal

A JSON backup of every affected row is written before any change.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time

# Data lives next to the code. /data/hazel on Railway and /opt/data/hazel
# on Portal are both 'the directory this file sits in', so this default is
# correct on either without an environment variable. Set HAZEL_HOME to
# split code from data.
HAZEL_HOME = os.environ.get(
    "HAZEL_HOME", os.path.dirname(os.path.abspath(__file__)))

DB = os.environ.get("HAZEL_DB", os.path.join(HAZEL_HOME, "hazel.db"))
BACKUP_DIR = os.environ.get("HAZEL_BACKUP", os.path.join(HAZEL_HOME, "backup"))


def norm(s):
    """Lowercase, strip everything that is not a letter or digit.

    Handles "Patak's" -> "pataks" matching term "pataks butter chicken", and
    "Malt-O-Meal" -> "maltomeal". Without this, punctuation alone would flag
    genuine locks as suspect.
    """
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def terms_text(raw):
    """search_terms is stored as a JSON list."""
    if not raw:
        return ""
    try:
        v = json.loads(raw)
        if isinstance(v, list):
            return " ".join(str(x) for x in v)
        return str(v)
    except Exception:
        return str(raw)


def classify(locked_brand, search_terms):
    """
    Returns (verdict, reason).

    A brand is considered named if its normalized form appears in the
    normalized term text, OR if any single word of the brand longer than 3
    chars does. The second clause keeps multi-word brands like "Monster
    Energy" (term: "monster zero ultra") on the KEEP side.
    """
    b = norm(locked_brand)
    t = norm(terms_text(search_terms))
    if not b:
        return "SUSPECT", "locked policy with no brand set"
    if b in t:
        return "KEEP", "brand named in search term"
    for word in re.split(r"[^A-Za-z0-9]+", locked_brand or ""):
        if len(word) > 3 and norm(word) in t:
            return "KEEP", f"brand word {word!r} named in search term"
    return "SUSPECT", "brand NOT named in its own search term"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the unlock; default is audit only")
    ap.add_argument("--only", nargs="*", default=None,
                    help="restrict to these slot_ids")
    ap.add_argument("--exclude", nargs="*", default=[],
                    help="leave these slot_ids locked")
    a = ap.parse_args()

    mode = "rw" if a.apply else "ro"
    con = sqlite3.connect(f"file:{DB}?mode={mode}", uri=True)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "select slot_id, brand_policy, locked_brand, search_terms "
        "from slots where brand_policy=? order by locked_brand, slot_id",
        ("locked",)).fetchall()

    keep, suspect = [], []
    for r in rows:
        verdict, reason = classify(r["locked_brand"], r["search_terms"])
        (keep if verdict == "KEEP" else suspect).append((r, reason))

    print(f"{len(rows)} locked slots: {len(keep)} genuine, {len(suspect)} suspect\n")

    print("--- KEEP (brand named in the term, so the lock was asked for) ---")
    for r, why in keep:
        print(f"  {r['slot_id']:<22} {str(r['locked_brand']):<16} {terms_text(r['search_terms'])[:46]}")

    print("\n--- SUSPECT (lock contradicts its own term) ---")
    for r, why in suspect:
        mark = "  [EXCLUDED]" if r["slot_id"] in a.exclude else ""
        print(f"  {r['slot_id']:<22} {str(r['locked_brand']):<16} "
              f"{terms_text(r['search_terms'])[:46]}{mark}")

    targets = [r for r, _ in suspect
               if r["slot_id"] not in a.exclude
               and (a.only is None or r["slot_id"] in a.only)]

    if not a.apply:
        print(f"\nAUDIT ONLY. {len(targets)} slot(s) would be unlocked.")
        print("Re-run with --apply to write. Nothing has been changed.")
        return 0

    if not targets:
        print("\nNothing to do.")
        return 0

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bpath = os.path.join(BACKUP_DIR, f"brand_locks_{stamp}.json")
    with open(bpath, "w") as f:
        json.dump([dict(r) for r in targets], f, indent=2)
    print(f"\nbackup written: {bpath}")

    for r in targets:
        con.execute("update slots set brand_policy='open', locked_brand=NULL "
                    "where slot_id=?", (r["slot_id"],))
    con.commit()

    # Verify by re-reading, not by trusting the update's return.
    bad = []
    for r in targets:
        chk = con.execute("select brand_policy, locked_brand from slots "
                          "where slot_id=?", (r["slot_id"],)).fetchone()
        if chk["brand_policy"] != "open" or chk["locked_brand"] is not None:
            bad.append(r["slot_id"])

    if bad:
        print(f"FAIL  did not unlock: {bad}")
        return 1
    print(f"OK    unlocked {len(targets)} slots, verified by re-read:")
    for r in targets:
        print(f"        {r['slot_id']}")
    print("\nThese slots now compare across all three retailers. Their stored "
          "observations are still one-sided, so run a cycle before reading "
          "anything into the new split.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
