import os, tempfile
os.environ["HAZEL_DB"] = os.path.join(tempfile.mkdtemp(), "a.db")

import store as S
import alerts as A
from normalize import parse_pack, unit_price
from prices import Candidate

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
    print(("  PASS  " if cond else "  FAIL  ") + msg)


def mk(retailer, pk, title, price, hint="", brand="Bounty"):
    c = Candidate(retailer=retailer, product_key=pk, title=title, price=price,
                  brand=brand, size_hint=hint)
    c.pack = parse_pack(title, hint)
    c.unit_price = unit_price(price, c.pack)
    return c


con = S.connect()
S.upsert_slot(con, "paper_towels", "Paper Towels", "roll_goods", "sheets",
              ["bounty"], cadence="weekly", max_stock=3000)

print("=== product_line collapses pack variants ===")
l1 = A.product_line("Bounty", "Bounty Select-A-Size Paper Towels, 12 Double Rolls, 147 sheets")
l2 = A.product_line("Bounty", "Bounty Select-A-Size Paper Towels, 8 Triple Rolls, 120 sheets")
print("   ", repr(l1))
print("   ", repr(l2))
check(l1 == l2, "different pack variants collapse to the same product line")

print("\n=== SHRINKFLATION across a UPC change ===")
old = mk("kroger", "UPC_OLD", "Bounty Select-A-Size Paper Towels 12 Double Rolls, 147 sheets per roll", 29.99)
line = A.product_line(old.brand, old.title)
S.record_pack(con, "paper_towels", "kroger", line, old.product_key,
              old.pack.total_units, old.price, old.unit_price)
import time as _t; _t.sleep(1.1)
CYCLE_START = S.now()
print(f"    baseline: {old.pack.total_units:.0f} sheets @ ${old.price} "
      f"= {old.unit_price:.4f}/100")

new = mk("kroger", "UPC_NEW", "Bounty Select-A-Size Paper Towels 12 Double Rolls, 120 sheets per roll", 29.99)
print(f"    now:      {new.pack.total_units:.0f} sheets @ ${new.price} "
      f"= {new.unit_price:.4f}/100")
a = A.check_shrinkflation(con, "paper_towels", new, stale_before=CYCLE_START)
check(a is not None, "shrinkflation detected at identical shelf price")
if a:
    print("     ", a["detail"], f"| impact ${a['dollar_impact']}")

print("\n=== suppression: low-confidence parse must NOT alert ===")
vague = mk("walmart", "WMX", "Bounty Select-A-Size 12 Rolls", 29.99)
check(vague.pack.confidence == "low" and vague.unit_price is None,
      "vague title parses LOW and has no unit price")
raised = A.run_alerts(con, "paper_towels", [vague])
check(raised == [], "LOW-confidence candidate raises nothing")

print("\n=== suppression: single observation is not a trend ===")
c1 = mk("kroger", "P1", "Bounty Select-A-Size 12 Double Rolls, 147 sheets per roll", 29.99)
S.record_observation(con, "paper_towels", "kroger", "P1", c1.title, 29.99, c1.pack, c1.unit_price)
spike = mk("kroger", "P1", "Bounty Select-A-Size 12 Double Rolls, 147 sheets per roll", 44.99)
a = A.check_sustained_rise(con, "paper_towels", spike)
check(a is None, "one high reading does not raise sustained_rise")

for _ in range(4):
    S.record_observation(con, "paper_towels", "kroger", "P1", c1.title, 29.99, c1.pack, c1.unit_price)
S.record_observation(con, "paper_towels", "kroger", "P1", spike.title, 44.99, spike.pack, spike.unit_price)
a = A.check_sustained_rise(con, "paper_towels", spike)
check(a is not None, "two consecutive high readings DO raise sustained_rise")
if a:
    print("     ", a["detail"], f"| impact ${a['dollar_impact']}")

print("\n=== suppression: not purchasing this cycle ===")
raised = A.run_alerts(con, "paper_towels", [spike], purchasing_this_cycle=False)
check(raised == [], "no alerts for a slot not being bought this cycle")

print("\n=== untried brand needs a HIGHER bar ===")
current = mk("walmart", "WM_CUR", "Bounty Select-A-Size 12 Double Rolls, 147 sheets per roll", 19.96)
small_win = mk("amazon", "AZ_NEW", "Amazon Basics Select-A-Size 12 Double Rolls, 147 sheets per roll", 17.96, brand="Amazon Basics")
a = A.check_better_alternative(con, "paper_towels", current, small_win)
check(a is None, "untried brand at 10% saving does not clear the 15% bar")

big_win = mk("amazon", "AZ_NEW", "Amazon Basics Select-A-Size 12 Double Rolls, 147 sheets per roll", 15.50, brand="Amazon Basics")
a = A.check_better_alternative(con, "paper_towels", current, big_win)
check(a is not None and a.get("untried"), "untried brand at 22% saving DOES propose")
if a:
    print("     ", a["detail"])

S.set_verdict(con, "paper_towels", "AZ_NEW", "keep", reason="fine")
a = A.check_better_alternative(con, "paper_towels", current, small_win)
check(a is not None and not a.get("untried"),
      "once tried and kept, the 10% bar applies and it proposes")

print("\n=== buy-ahead suppressed when storage is full ===")
for _ in range(3):
    S.record_observation(con, "paper_towels", "walmart", "LOWP", current.title,
                         19.96, current.pack, current.unit_price)
cheap = mk("walmart", "LOWP", "Bounty Select-A-Size 12 Double Rolls, 147 sheets per roll", 12.99)
a = A.check_unusually_low(con, "paper_towels", cheap, on_hand=0)
check(a is not None, "deep discount raises buy-ahead when storage is free")
a = A.check_unusually_low(con, "paper_towels", cheap, on_hand=2900)
check(a is None, "same discount suppressed when max_stock would be exceeded")

print("\n=== alert cap and ranking ===")
S.raise_alert(con, "x", "sustained_rise", "small", 1.20)
S.raise_alert(con, "x", "shrinkflation", "big", 22.00)
S.raise_alert(con, "x", "better_alt", "mid", 8.00)
op = S.open_alerts(con, limit=3)
check(op[0]["dollar_impact"] == 22.00, "alerts ranked by dollar impact")

print("\n=== dollar floor ===")
A.MIN_DOLLAR = 100.0
a = A.check_shrinkflation(con, "paper_towels", new, stale_before=CYCLE_START)
check(a is None, "sub-floor impact is suppressed")
A.MIN_DOLLAR = 1.00

print("\nFAILURES:", len(fails))
for f in fails:
    print("  -", f)
