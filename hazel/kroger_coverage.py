"""
Read-only. Answers one question: is Kroger competitive, or absent?

The 2026-08-08 basket routed 17/17 lines to Amazon and Walmart and zero to
Kroger. That is either
    (a) Kroger being priced and losing on unit price, which is a real result
        and means the sanctioned cart leg has little to do, or
    (b) Kroger not being priced at all -- a search/matching defect, in which
        case the comparison has been running two-legged for weeks and the
        savings numbers are understated.

Those have opposite responses, so guessing between them is not acceptable.

Writes nothing. Opens the DB read-only. Makes no network calls, so it costs
no SerpApi quota.
"""

import os
import sqlite3
import sys
from collections import Counter, defaultdict

# Data lives next to the code. /data/hazel on Railway and /opt/data/hazel
# on Portal are both 'the directory this file sits in', so this default is
# correct on either without an environment variable. Set HAZEL_HOME to
# split code from data.
HAZEL_HOME = os.environ.get(
    "HAZEL_HOME", os.path.dirname(os.path.abspath(__file__)))

DB = os.environ.get("HAZEL_DB", os.path.join(HAZEL_HOME, "hazel.db"))


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    tables = [r[0] for r in con.execute(
        "select name from sqlite_master where type='table'")]
    print("tables:", ", ".join(sorted(tables)))

    # Find the observations table without assuming its name.
    obs_table = None
    for t in tables:
        cols = {c[1] for c in con.execute(f"pragma table_info({t})")}
        if "retailer" in cols and ("unit_price" in cols or "price" in cols):
            obs_table = t
            obs_cols = cols
            break
    if not obs_table:
        print("\nNo table with a retailer column. Cannot answer from the DB.")
        return 1

    print(f"\nusing table: {obs_table}")
    print(f"columns: {', '.join(sorted(obs_cols))}")

    total = con.execute(f"select count(*) from {obs_table}").fetchone()[0]
    print(f"\ntotal rows: {total}")

    print("\n--- ROWS BY RETAILER ---")
    by_ret = Counter()
    for r in con.execute(f"select retailer, count(*) n from {obs_table} "
                         f"group by retailer order by n desc"):
        by_ret[r["retailer"]] = r["n"]
        print(f"  {r['retailer']:<10} {r['n']}")

    if not by_ret.get("kroger"):
        print("\n=> KROGER IS ABSENT FROM THE PRICE HISTORY ENTIRELY.")
        print("   This is a search/matching defect, not a pricing result.")
        print("   The comparison has been running on two legs.")
        return 0

    # Per-slot: who has the best unit price, counting only slots where all
    # present retailers are comparable.
    slot_col = "slot_id" if "slot_id" in obs_cols else None
    price_col = "unit_price" if "unit_price" in obs_cols else "price"
    ts_col = next((c for c in ("observed_at", "created_at", "ts", "date")
                   if c in obs_cols), None)

    if not slot_col:
        print("\nNo slot column; cannot compute per-slot winners.")
        return 0

    print(f"\n--- SLOT COVERAGE (which retailers priced each slot) ---")
    cover = defaultdict(set)
    for r in con.execute(f"select {slot_col} s, retailer r from {obs_table}"):
        cover[r["s"]].add(r["r"])
    kroger_slots = [s for s, rs in cover.items() if "kroger" in rs]
    print(f"  slots with any observation : {len(cover)}")
    print(f"  slots Kroger was priced for: {len(kroger_slots)}")
    missing = sorted(s for s, rs in cover.items() if "kroger" not in rs)
    if missing:
        print(f"  slots Kroger NEVER priced  : {len(missing)}")
        print("    " + ", ".join(missing[:20]) + ("..." if len(missing) > 20 else ""))

    # Latest observation per (slot, retailer), then pick the winner per slot.
    order = f"order by {ts_col} desc" if ts_col else ""
    latest = {}
    for r in con.execute(f"select * from {obs_table} {order}"):
        key = (r[slot_col], r["retailer"])
        if key not in latest and r[price_col] is not None:
            latest[key] = float(r[price_col])

    wins = Counter()
    margins = []
    for s in cover:
        offers = {ret: p for (sl, ret), p in latest.items() if sl == s}
        if len(offers) < 2:
            continue
        best = min(offers, key=offers.get)
        wins[best] += 1
        if "kroger" in offers and best != "kroger":
            ordered = sorted(offers.values())
            margins.append((s, offers["kroger"], ordered[0], best))

    print(f"\n--- BEST UNIT PRICE BY RETAILER (latest observation) ---")
    for ret, n in wins.most_common():
        print(f"  {ret:<10} wins {n}")

    if margins:
        margins.sort(key=lambda m: (m[1] - m[2]) / m[2] if m[2] else 0)
        print(f"\n--- CLOSEST KROGER LOSSES (smallest gap first) ---")
        for s, k, b, who in margins[:10]:
            gap = ((k - b) / b * 100) if b else 0
            print(f"  {s:<22} kroger {k:>9.3f}  vs {who} {b:>9.3f}  (+{gap:.0f}%)")

    print("\nNOTE: unit prices are per the normalizer's unit, not per package. "
          "A large gap can be a pack-size parsing artefact rather than a real "
          "price difference. Spot-check before concluding Kroger is expensive.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
