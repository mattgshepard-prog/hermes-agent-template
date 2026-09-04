import os, tempfile
os.environ["HAZEL_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")

import store as S
from normalize import parse_pack, unit_price

con = S.connect()
fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
    print(("  PASS  " if cond else "  FAIL  ") + msg)


print("=== SLOTS ===")
S.upsert_slot(con, "paper_towels", "Paper Towels", "roll_goods", "sheets",
              ["bounty paper towels", "paper towels select a size"],
              must_not_have=["scented"], cadence="monthly", max_stock=5000)
S.upsert_slot(con, "coffee", "Coffee", "food_weight", "oz",
              ["peets major dickasons"], brand_policy="locked",
              locked_brand="Peet's", cadence="weekly")
check(len(S.list_slots(con)) == 2, "two slots created")
check(S.get_slot(con, "coffee")["brand_policy"] == "locked", "coffee is brand-locked")
S.upsert_slot(con, "paper_towels", "Paper Towels", "roll_goods", "sheets",
              ["bounty"], cadence="weekly")
check(len(S.list_slots(con)) == 2 and
      S.get_slot(con, "paper_towels")["cadence"] == "weekly",
      "upsert updates in place, no duplicate row")

print("\n=== OBSERVATIONS (real prices from today) ===")
REAL = [
    ("kroger", "0003077215664", "Bounty Paper Towels Select-A-Size White 12 Double Rolls", "12 rolls", 29.99),
    ("walmart", "WM123", "Bounty Paper Towels Select-A-Size White, 12 Double Rolls", "", 19.96),
    ("amazon", "B00ASIN1", "Essentials Select-A-Size Paper Towels, 6 Double Rolls", "", 7.97),
    ("amazon", "B00ASIN2", "Select-A-Size Paper Towels, White, 6 Triple Rolls = 18 Regular Rolls", "", 16.94),
]
for retailer, pk, title, hint, price in REAL:
    pack = parse_pack(title, hint)
    up = unit_price(price, pack)
    S.record_observation(con, "paper_towels", retailer, pk, title, price, pack, up)
    print(f"  {retailer:<8} {pk:<15} ${price:>6.2f}  up/100={up}")

check(S.observation_count(con, "paper_towels") == 4, "4 observations stored")
med = S.trailing_median(con, "paper_towels")
check(med is not None, f"trailing median computed: {med}")

print("\n=== VERDICT FILTER (the mechanism) ===")
CANDS = [{"product_key": pk, "title": t, "brand": "Bounty"}
         for _, pk, t, _, _ in REAL]

kept, dropped = S.filter_candidates(con, "paper_towels", CANDS)
check(len(kept) == 4 and not dropped, "no verdicts yet -> nothing filtered")

S.set_verdict(con, "paper_towels", "B00ASIN1", "rejected",
              brand="Amazon Basics", reason="too thin, fell apart")
kept, dropped = S.filter_candidates(con, "paper_towels", CANDS)
kept_keys = [c["product_key"] for c in kept]
check("B00ASIN1" not in kept_keys, "rejected product is ABSENT, not deprioritized")
check(len(dropped) == 1 and dropped[0][1] == "verdict:rejected",
      "drop reason recorded")

# Cheapest-wins must not resurrect a rejected item.
best = min(kept, key=lambda c: next(
    p for r, pk, t, h, p in REAL if pk == c["product_key"]))
check(best["product_key"] != "B00ASIN1",
      "cheapest item cannot win if rejected, even at half the price")

S.set_verdict(con, "paper_towels", "WM123", "keep", reason="works fine")
check(S.get_verdicts(con, "paper_towels")["WM123"]["verdict"] == "keep",
      "keep verdict stored")
check(S.is_untried(con, "paper_towels", "0003077215664"),
      "unrecorded product reads as untried")
check(not S.is_untried(con, "paper_towels", "WM123"), "kept product is not untried")

print("\n=== must_not_have ===")
S.upsert_slot(con, "hand_soap", "Hand Soap", "hand_soap", "fl_oz",
              ["hand soap"], must_not_have=["scented", "antibacterial"])
hs = [{"product_key": "A", "title": "Method Gel Hand Wash Unscented"},
      {"product_key": "B", "title": "Dial Scented Antibacterial Hand Soap"}]
kept, dropped = S.filter_candidates(con, "hand_soap", hs)
check(len(kept) == 1 and kept[0]["product_key"] == "A",
      "must_not_have removes disqualified candidate")

print("\n=== brand lock ===")
cf = [{"product_key": "C1", "title": "Peet's Major Dickason's Blend, 32 oz"},
      {"product_key": "C2", "title": "Great Value Classic Roast, 48 oz"}]
kept, dropped = S.filter_candidates(con, "coffee", cf)
check(len(kept) == 1 and kept[0]["product_key"] == "C1",
      "brand-locked slot rejects the cheaper off-brand")

print("\n=== SHRINKFLATION: pack history across a UPC change ===")
# Old variant, then manufacturer shrinks it and issues a NEW UPC at same price.
S.record_pack(con, "paper_towels", "kroger", "bounty select-a-size", "UPC_OLD", 1764, 29.99, 1.70)
S.record_pack(con, "paper_towels", "kroger", "bounty select-a-size", "UPC_NEW", 1440, 29.99, 2.08)
vs = S.pack_variants(con, "paper_towels", "bounty select-a-size", retailer="kroger")
check(len(vs) == 2, "both pack variants tracked under one product line")
shrank = vs[1]["total_units"] < vs[0]["total_units"]
same_price = abs(vs[1]["price"] - vs[0]["price"]) < 0.01
check(shrank and same_price,
      "detects smaller pack at identical shelf price across a UPC change")
pct = (vs[1]["unit_price"] - vs[0]["unit_price"]) / vs[0]["unit_price"] * 100
print(f"        unit price {vs[0]['unit_price']} -> {vs[1]['unit_price']} "
      f"(+{pct:.1f}%) at an unchanged ${vs[1]['price']:.2f}")

print("\n=== ALERTS ===")
a1 = S.raise_alert(con, "paper_towels", "shrinkflation", "bounty 1764->1440 sheets", 4.60)
a2 = S.raise_alert(con, "paper_towels", "shrinkflation", "bounty 1764->1440 sheets", 4.60)
check(a1 and a2 is None, "duplicate open alert is not re-raised")
S.raise_alert(con, "coffee", "sustained_rise", "up 12% two cycles", 1.10)
op = S.open_alerts(con)
check(len(op) == 2 and op[0]["dollar_impact"] == 4.60,
      "open alerts ranked by dollar impact")
S.resolve_alert(con, a1, "rebaselined")
check(len(S.open_alerts(con)) == 1, "resolved alert leaves the queue")

print("\n=== VERDICT EXPORT (unregenerable table) ===")
p = os.path.join(tempfile.mkdtemp(), "verdicts.json")
n = S.export_verdicts(con, p)
check(n == 2 and os.path.exists(p), f"exported {n} verdicts to flat file")

print("\nFAILURES:", len(fails))
for f in fails:
    print("  -", f)
