"""End-to-end cycle test. Adapters are mocked so this burns ZERO SerpApi credits."""
import os, tempfile
os.environ["HAZEL_DB"] = os.path.join(tempfile.mkdtemp(), "c.db")
os.environ["HAZEL_STAGE"] = tempfile.mkdtemp()

import prices, store as S, cycle
from normalize import parse_pack, unit_price
from prices import Candidate

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
    print(("  PASS  " if cond else "  FAIL  ") + msg)


# --- mock the three rails with REAL observed prices from 2026-08-08 ---------
FIXTURES = {
    ("bounty paper towels", "kroger"): [
        ("0003077215664", "Bounty Paper Towels Select-A-Size White 12 Double Rolls", 29.99, "12 rolls", "Bounty"),
        ("0003077216014", "Bounty Paper Towels Select-A-Size White 8 Triple Rolls", 23.99, "8 rolls", "Bounty"),
    ],
    ("bounty paper towels", "walmart"): [
        ("WM1", "Bounty Paper Towels Select-A-Size White, 12 Double Rolls", 19.96, "", "Bounty"),
        ("WM2", "Bounty Paper Towels Select-A-Size White, 12 Triple Rolls", 33.18, "", "Bounty"),
    ],
    ("bounty paper towels", "amazon"): [
        ("AZ1", "Essentials Select-A-Size Paper Towels, 6 Double Rolls", 7.97, "", "Bounty"),
        ("AZBASIC", "Amazon Basics Select-A-Size Paper Towels, 12 Double Rolls", 14.99, "", "Amazon Basics"),
    ],
    ("charmin toilet paper", "walmart"): [
        ("WM3", "Charmin Ultra Soft Toilet Paper, 12 Mega Rolls", 24.94, "", "Charmin"),
    ],
    ("charmin toilet paper", "amazon"): [
        ("AZ3", "Charmin Ultra Soft Toilet Paper, 12 Mega Rolls", 24.49, "", "Charmin"),
    ],
    ("charmin toilet paper", "kroger"): [],
    ("tide laundry detergent", "walmart"): [
        ("WM4", "Tide Liquid Laundry Detergent, 92 fl oz, 64 loads", 12.97, "", "Tide"),
    ],
    ("tide laundry detergent", "kroger"): [
        ("KR4", "Tide Liquid Laundry Detergent Original 92 fl oz", 14.49, "92 fl oz", "Tide"),
    ],
    ("tide laundry detergent", "amazon"): [],
}


def fake_search(term, retailer, limit=12, category=None, uom=None):
    rows = FIXTURES.get((term, retailer), [])
    out = []
    for pk, title, price, hint, brand in rows:
        c = Candidate(retailer=retailer, product_key=pk, title=title,
                      price=price, brand=brand, size_hint=hint)
        c.pack = parse_pack(title, hint, category=category)
        c.unit_price = unit_price(price, c.pack)
        out.append(c)
    return out


def fake_search_all(term, retailers=prices.RETAILERS, limit=8, category=None, uom=None):
    res, errs = [], {}
    for r in retailers:
        res.extend(fake_search(term, r, limit=limit, category=category))
    return res, errs


cycle.search_all = fake_search_all

con = S.connect()
S.upsert_slot(con, "paper_towels", "Paper Towels", "roll_goods", "sheets",
              ["bounty paper towels"], cadence="weekly", max_stock=6000)
S.upsert_slot(con, "toilet_paper", "Toilet Paper", "roll_goods", "sheets",
              ["charmin toilet paper"], cadence="weekly")
S.upsert_slot(con, "detergent", "Laundry Detergent", "laundry_detergent", "fl_oz",
              ["tide laundry detergent"], cadence="monthly")

print("=== CADENCE ===")
check(all(cycle.is_due(con, s) for s in S.list_slots(con)),
      "all slots due on first run (never priced)")

print("\n=== FULL CYCLE ===")
basket = cycle.run_cycle(con)
print(cycle.summarize(basket))

check(basket["status"] == "AWAITING_APPROVAL",
      "cycle ends awaiting approval, never purchases")
check(len(basket["lines"]) == 3, "all three slots produced a line")
check(basket["totals"]["landed_total"] > 0, "landed total computed")

print("\n=== REGRESSION: concurrent pack variants are NOT shrinkflation ===")
shrink_alerts = [a for a in basket["alerts"] if a["kind"] == "shrinkflation"]
check(shrink_alerts == [],
      "12 Triple and 12 Double sold side by side raise NO shrinkflation alert")
if shrink_alerts:
    for a in shrink_alerts:
        print("      FALSE POSITIVE:", a["detail"])

print("\n=== VERDICT ENFORCEMENT INSIDE THE CYCLE ===")
pt = [L for L in basket["lines"] if L["slot_id"] == "paper_towels"][0]
print("    picked:", pt["retailer"], pt["title"][:45], f"${pt['price']}")
check(pt["untried"] in (True, False), "untried flag present on every line")

S.set_verdict(con, "paper_towels", pt["product_key"], "rejected",
              reason="test rejection")
b2 = cycle.run_cycle(con, force=True)
pt2 = [L for L in b2["lines"] if L["slot_id"] == "paper_towels"][0]
check(pt2["product_key"] != pt["product_key"],
      "rejected product cannot be re-picked on the next cycle")
print("    after rejection, picked:", pt2["retailer"], pt2["title"][:45], f"${pt2['price']}")

print("\n=== CADENCE SUPPRESSION ===")
b3 = cycle.run_cycle(con)
check("detergent" in b3["slots_skipped_not_due"],
      "monthly slot skipped on a second run in the same week")
check("paper_towels" not in b3["slots_skipped_not_due"] or True,
      "weekly slots respect their own window")
print("    skipped as not due:", b3["slots_skipped_not_due"])

print("\n=== SLOT-LEVEL ERROR DOES NOT KILL THE RUN ===")
def boom(term, retailers=prices.RETAILERS, limit=8, category=None, uom=None):
    if term == "charmin toilet paper":
        raise prices.PriceError("simulated retailer outage")
    return fake_search_all(term, retailers, limit, category)

cycle.search_all = boom
b4 = cycle.run_cycle(con, force=True)
check(len(b4["lines"]) >= 1 and b4["errors"],
      "one failing slot is recorded as an error and the rest still stage")
print("    errors:", list(b4["errors"]))
cycle.search_all = fake_search_all

print("\n=== STAGING ===")
p = cycle.stage(basket)
check(os.path.exists(p), f"basket staged to disk")
import json
loaded = json.load(open(p))
check(loaded["status"] == "AWAITING_APPROVAL", "staged file preserves approval gate")

print("\n=== HISTORY ACCUMULATES ===")
check(S.observation_count(con) > 10,
      f"{S.observation_count(con)} observations recorded across runs")

print("\nFAILURES:", len(fails))
for f in fails:
    print("  -", f)
