from optimizer import (RetailerTerms, LineOption, optimize, landed_cost,
                       explain, top_up_suggestion)

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
    print(("  PASS  " if cond else "  FAIL  ") + msg)


TERMS = {
    "kroger":  RetailerTerms("kroger",  free_ship_threshold=35.0,
                             delivery_fee=9.95, order_minimum=35.0, tax_rate=0.0),
    "walmart": RetailerTerms("walmart", free_ship_threshold=35.0,
                             delivery_fee=6.99, tax_rate=0.081),
    "amazon":  RetailerTerms("amazon",  free_ship_threshold=35.0,
                             delivery_fee=6.99, membership=True, tax_rate=0.081),
}

print("=== TRAP: cheapest-per-item opens a third order for pennies ===")
opts = {
    "paper_towels": [
        LineOption("paper_towels", "walmart", "WM1", "Bounty 12 Double", 19.96),
        LineOption("paper_towels", "kroger", "KR1", "Bounty 12 Double", 29.99),
    ],
    "toilet_paper": [
        LineOption("toilet_paper", "walmart", "WM2", "Charmin 12 Mega", 24.94),
        LineOption("toilet_paper", "amazon", "AZ2", "Charmin 12 Mega", 24.49),
    ],
}
naive = landed_cost({s: min(o, key=lambda x: x.price) for s, o in opts.items()}, TERMS)
best = optimize(opts, TERMS, min_savings=1.50)
print("  naive cheapest-per-item:")
print("   ", explain(naive, TERMS).replace("\n", "\n    "))
print("  optimized:")
print("   ", explain(best, TERMS).replace("\n", "\n    "))
check(len(best.retailers_used) < len(naive.retailers_used) or best.total <= naive.total,
      "optimizer never loses to naive cheapest-per-item")
check(best.total <= naive.total, f"landed cost {best.total} <= naive {naive.total}")

print("\n=== free-shipping threshold is respected ===")
opts2 = {
    "paper_towels": [LineOption("paper_towels", "walmart", "WM1", "Bounty", 19.96)],
    "toilet_paper": [LineOption("toilet_paper", "walmart", "WM2", "Charmin", 24.94)],
}
a = optimize(opts2, TERMS)
check(a.per_retailer["walmart"] >= 35.0 and a.shipping["walmart"] == 0.0,
      f"basket ${a.per_retailer['walmart']:.2f} clears $35 -> free shipping")

print("\n=== order minimum blocks an infeasible split ===")
opts3 = {
    "milk": [LineOption("milk", "kroger", "K1", "Milk", 4.29),
             LineOption("milk", "walmart", "W1", "Milk", 4.48)],
}
a3 = optimize(opts3, TERMS)
check(a3.picks["milk"].retailer == "walmart",
      "kroger blocked by $35 order minimum, falls to walmart despite higher price")

print("\n=== top-up suggestion ===")
opts4 = {
    "a": [LineOption("a", "walmart", "W1", "Item A", 18.00)],
    "b": [LineOption("b", "walmart", "W2", "Item B", 14.00)],
}
a4 = optimize(opts4, TERMS)
tips = top_up_suggestion(a4, TERMS)
check(len(tips) == 1 and tips[0][0] == "walmart" and abs(tips[0][1] - 3.0) < 0.01,
      f"suggests $3.00 top-up to save the $6.99 fee")
print("   ", explain(a4, TERMS).replace("\n", "\n    "))

print("\n=== min_savings floor prevents splitting for trivial gain ===")
opts5 = {
    "x": [LineOption("x", "walmart", "W1", "X", 20.00),
          LineOption("x", "amazon", "A1", "X", 19.60)],
    "y": [LineOption("y", "walmart", "W2", "Y", 20.00)],
}
a5 = optimize(opts5, TERMS, min_savings=1.50)
check(len(a5.retailers_used) == 1,
      "40c saving does not justify opening a second retailer")

print("\n=== realistic weekly basket, 6 slots ===")
weekly = {
    "paper_towels": [LineOption("paper_towels", "walmart", "W1", "Bounty 12D", 19.96),
                     LineOption("paper_towels", "kroger", "K1", "Bounty 12D", 29.99),
                     LineOption("paper_towels", "amazon", "A1", "Bounty 6D", 7.97)],
    "toilet_paper": [LineOption("toilet_paper", "walmart", "W2", "Charmin", 24.94),
                     LineOption("toilet_paper", "amazon", "A2", "Charmin", 24.49)],
    "detergent":    [LineOption("detergent", "walmart", "W3", "Tide 92oz", 12.97),
                     LineOption("detergent", "kroger", "K3", "Tide 92oz", 14.49)],
    "dish_soap":    [LineOption("dish_soap", "walmart", "W4", "Dawn", 4.97),
                     LineOption("dish_soap", "kroger", "K4", "Dawn", 5.49)],
    "hand_soap":    [LineOption("hand_soap", "amazon", "A5", "Method", 4.29),
                     LineOption("hand_soap", "walmart", "W5", "Method", 4.87)],
    "trash_bags":   [LineOption("trash_bags", "walmart", "W6", "Hefty 80ct", 19.98),
                     LineOption("trash_bags", "amazon", "A6", "Hefty 80ct", 21.94)],
}
aw = optimize(weekly, TERMS, min_savings=1.50)
print("   ", explain(aw, TERMS).replace("\n", "\n    "))
naive_w = landed_cost({s: min(o, key=lambda x: x.price) for s, o in weekly.items()}, TERMS)
print(f"    naive landed: ${naive_w.total:.2f}  |  optimized: ${aw.total:.2f}"
      f"  |  saved: ${naive_w.total - aw.total:.2f}")
check(aw.total <= naive_w.total, "optimizer beats or matches naive on a real basket")
check(not aw.blocked, "no retailer left below its order minimum")

print("\nFAILURES:", len(fails))
for f in fails:
    print("  -", f)
