"""
Why did Kroger get dropped for these slots?

The 2026-08-08 basket routed 17/17 lines away from Kroger, and 8 slots have
ZERO Kroger observations in 879 rows. Kroger returns eggs fine and the pack
parser handles "12 ct" at HIGH confidence, so neither the API nor the
normalizer is the obvious culprit. This walks the REAL pipeline
(prices.search -> parse_pack -> unit_price -> rank) for one slot at a time and
prints where each candidate falls out.

COSTS NOTHING AGAINST SERPAPI. Kroger only, and the Kroger Products API is
free. Walmart and Amazon are deliberately not queried.

USAGE
    python3 why_no_kroger.py                 # the 8 known-missing slots
    python3 why_no_kroger.py eggs pickles    # specific slots
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import prices  # noqa: E402

# Data lives next to the code. /data/hazel on Railway and /opt/data/hazel
# on Portal are both 'the directory this file sits in', so this default is
# correct on either without an environment variable. Set HAZEL_HOME to
# split code from data.
HAZEL_HOME = os.environ.get(
    "HAZEL_HOME", os.path.dirname(os.path.abspath(__file__)))

DB = os.environ.get("HAZEL_DB", os.path.join(HAZEL_HOME, "hazel.db"))

MISSING = ["eggs", "pickles", "broccoli_fresh", "cauliflower_fresh",
           "canned_tomatoes", "bread_flour", "sesame_oil", "bleach_powder"]


def slot_rows(con, slot_ids):
    cols = [r[1] for r in con.execute("pragma table_info(slots)")]
    out = []
    for sid in slot_ids:
        r = con.execute("select * from slots where slot_id=?", (sid,)).fetchone()
        out.append((sid, dict(zip(cols, r)) if r else None))
    return cols, out


def terms_of(slot):
    """The slot's search terms, whatever the column is called."""
    for k in ("search_terms", "terms", "term", "query_terms"):
        v = slot.get(k)
        if v:
            if isinstance(v, str):
                s = v.strip()
                if s.startswith("["):
                    import json
                    try:
                        return json.loads(s)
                    except Exception:
                        pass
                return [s]
            return list(v)
    return []


def main():
    targets = sys.argv[1:] or MISSING
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    cols, rows = slot_rows(con, targets)
    print("slots table columns:", ", ".join(cols))

    for sid, slot in rows:
        print("\n" + "=" * 68)
        if not slot:
            print(f"{sid}: NO SUCH SLOT (basket referenced a slot that "
                  f"does not exist)")
            continue

        cat = slot.get("category")
        terms = terms_of(slot)
        print(f"{sid}   category={cat!r}  uom={slot.get('unit_of_measure')!r}  "
              f"cadence={slot.get('cadence')!r}")
        print(f"  terms: {terms}")
        for k in ("brand_policy", "locked_brand", "must_not_have", "max_stock"):
            if slot.get(k):
                print(f"  {k}: {slot[k]}")

        counts = con.execute(
            "select retailer, count(*) from price_observations "
            "where slot_id=? group by retailer", (sid,)).fetchall()
        print(f"  stored observations: {dict(counts) or 'NONE'}")

        if not terms:
            print("  => NO SEARCH TERM. Nothing can ever be priced for this "
                  "slot on any retailer.")
            continue

        term = terms[0]
        try:
            cands = prices.search(term, "kroger", limit=8, category=cat)
        except Exception as e:
            print(f"  => KROGER SEARCH FAILED: {str(e)[:200]}")
            continue

        if not cands:
            print(f"  => Kroger returned 0 results for {term!r}. "
                  f"Term mismatch, not a pricing result.")
            continue

        ok, skipped = prices.rank(cands)
        print(f"  Kroger returned {len(cands)}: "
              f"{len(ok)} comparable, {len(skipped)} dropped")

        for c in ok[:4]:
            p = c.pack
            print(f"    KEEP  unit={c.unit_price:>9.3f}  ${c.price:>6.2f}  "
                  f"conf={p.confidence if p else '?'}  {c.title[:44]}")
        for c in skipped[:6]:
            p = c.pack
            why = (p.notes if p and p.notes else
                   f"no unit price (count={getattr(p, 'count', None)}, "
                   f"total_units={getattr(p, 'total_units', None)}, "
                   f"uom={getattr(p, 'unit_of_measure', None)})")
            print(f"    DROP  ${c.price:>6.2f}  size={c.size_hint!r:<12} "
                  f"{c.title[:38]}")
            print(f"          why: {why[:150]}")

        if ok and not counts:
            print("  => Kroger IS comparable NOW but has no stored "
                  "observations. The slot was never priced, rather than "
                  "priced and beaten.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
