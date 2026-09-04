"""Tests using REAL strings returned by the live APIs on 2026-08-08."""
from normalize import parse_pack, unit_price, describe, HIGH, MEDIUM, LOW, NONE

# (label, title, size_hint, price)
KROGER = [
    ("KR 6 Double",  "Bounty Paper Towels Select-A-Size White 6 Double Rolls", "6 rolls", 14.99),
    ("KR 8 Triple",  "Bounty Paper Towels Select-A-Size White 8 Triple Rolls", "8 rolls", 23.99),
    ("KR 12 Double", "Bounty Paper Towels Select-A-Size White 12 Double Rolls", "12 rolls", 29.99),
    ("KR 2 Triple",  "Bounty Paper Towels Select-A-Size White 2 Triple Rolls", "2 rolls", 9.49),
]

WALMART = [
    ("WM 12 Double", "Bounty Select-a-Size Paper Towels, 12 Double Rolls, White", "", 38.99),
    ("WM 12 Double b", "Bounty Paper Towels Select-A-Size White, 12 Double Rolls", "", 19.96),
    ("WM 12 Triple", "Bounty Paper Towels Select-A-Size White, 12 Triple Rolls", "", 33.18),
]

AMAZON = [
    ("AZ 16 Family", "Paper Towels Quick Size, White, 16 Family Rolls = 40 Regular Rolls", "", 43.49),
    ("AZ 6 Triple",  "Select-A-Size Paper Towels, White, 6 Triple Rolls = 18 Regular Rolls", "", 16.94),
    ("AZ 6 Double",  "Essentials Select-A-Size Paper Towels, 6 Double Rolls", "", 7.97),
]

EXPLICIT = [
    ("Explicit sheets", "Bounty Select-A-Size, 12 rolls, 147 sheets per roll", "", 29.99),
]

OTHER = [
    ("Detergent", "Tide Liquid Laundry Detergent, 92 fl oz, 64 loads", "", 12.97),
    ("Pods", "Tide PODS Laundry Detergent Pacs, 81 count", "", 21.94),
    ("Hand soap ml", "Method Gel Hand Wash, 354 ml", "", 4.29),
    ("Trash bags", "Hefty Ultra Strong Tall Kitchen Bags, 80 count", "", 19.98),
    ("Shrinkflation", "Bounty Select-A-Size, New Look Same Great Quality, 12 Double Rolls", "12 rolls", 29.99),
    ("Junk", "Bounty Paper Towels", "", 9.99),
]


def show(rows):
    for label, title, hint, price in rows:
        p = parse_pack(title, hint)
        up = unit_price(price, p)
        ups = f"{up:.4f}" if up is not None else "  --  "
        print(f"  {label:<17} ${price:>6.2f}  n={str(p.count):>4} "
              f"unit={str(p.unit_size):>6} tot={str(p.total_units):>7} "
              f"{p.unit_of_measure:<7} {p.confidence:<6} up/100={ups}")
        if p.notes:
            print(f"                     note: {p.notes}")


print("=== KROGER (store 62000086) ===")
show(KROGER)
print("\n=== WALMART ===")
show(WALMART)
print("\n=== AMAZON ===")
show(AMAZON)
print("\n=== EXPLICIT SHEET COUNT ===")
show(EXPLICIT)
print("\n=== OTHER CATEGORIES ===")
show(OTHER)

print("\n=== ASSERTIONS ===")
fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
    print(("  PASS  " if cond else "  FAIL  ") + msg)


p = parse_pack("Bounty Select-A-Size, 12 rolls, 147 sheets per roll")
check(p.confidence == HIGH and p.total_units == 1764,
      "explicit sheets/roll -> HIGH, 12*147=1764")

p = parse_pack("Bounty Paper Towels Select-A-Size White 12 Double Rolls", "12 rolls")
check(p.confidence == MEDIUM, "roll type only -> MEDIUM, never HIGH")

p = parse_pack("Bounty Paper Towels")
check(p.confidence == NONE and unit_price(9.99, p) is None,
      "no pack info -> NONE and unit_price returns None")

p = parse_pack("Bounty Select-A-Size 12 Rolls")
check(p.confidence == LOW and unit_price(29.99, p) is None,
      "count without roll type -> LOW and NOT comparable")

p = parse_pack("Tide Liquid Laundry Detergent, 92 fl oz, 64 loads")
check(p.unit_of_measure == "fl_oz" and "loads" in p.notes,
      "detergent normalizes to fl_oz and ignores 'loads' claim")

p = parse_pack("Method Gel Hand Wash, 354 ml")
check(p.unit_of_measure == "fl_oz" and abs(p.total_units - 11.97) < 0.1,
      "ml converts to fl_oz")

p = parse_pack("Paper Towels Quick Size, White, 16 Family Rolls = 40 Regular Rolls")
check("not trusted" in p.notes, "roll-equivalence claim flagged as untrusted")

p = parse_pack("Bounty, New Look Same Great Quality, 12 Double Rolls", "12 rolls")
check("RELAUNCH" in p.notes, "relaunch copy raises shrinkflation flag")

print()
print("FAILURES:", len(fails))
for f in fails:
    print("  -", f)

print("\n=== REGRESSIONS from the 2026-08-08 live run ===")
reg = []


def rcheck(cond, msg):
    if not cond:
        reg.append(msg)
    print(("  PASS  " if cond else "  FAIL  ") + msg)


# Bug B: "3 Sheet Sizes (Quarter, Half, Full)" read as 3 sheets/roll at HIGH.
p = parse_pack("Tear-A-Square 3-Ply Paper Towels, 12 XL Family Rolls = 30 "
               "Regular Rolls, with 3 Sheet Sizes (Quarter, Half, Full)")
rcheck(p.confidence != HIGH and "outside plausible range" in p.notes,
       "implausible sheets/roll is rejected, not trusted at HIGH")
rcheck(unit_price(31.49, p) < 5.0,
       "no 20x-wrong unit price from a misread sheet count")

# Bug: count taken from the RIGHT of a roll-equivalence claim.
for title, expect in [
    ("Quick-Size Paper Towels, White, 12 Family Triple = 40 Regular Rolls", 12),
    ("Tear-A-Square, 12 XL Family Rolls = 30 Regular Rolls", 12),
    ("Select-A-Size Paper Towels, White, 8 Triple Rolls = 24 Regular Roll", 8),
    ("Select-A-Size Paper Towels, White, 6 Triple Rolls = 18 Regular Rolls", 6),
]:
    p = parse_pack(title)
    rcheck(p.count == expect,
           f"equivalence claim -> count {expect}, not the inflated right side "
           f"(got {p.count})")

# Bug A: calibrated roll estimates must not exceed real published counts.
from normalize import ROLL_TYPE_SHEETS
rcheck(ROLL_TYPE_SHEETS["triple"] == 123,
       "triple calibrated to Walmart's published 123 sheets")
rcheck(ROLL_TYPE_SHEETS["double"] < 120,
       "double no longer overstated at 120")

# Walmart publishes real sheet counts -> must reach HIGH.
p = parse_pack("Bounty Paper Towels Select-A-Size White, 12 Double Rolls, "
               "82 Sheets per Roll")
rcheck(p.confidence == HIGH and p.total_units == 984,
       "published sheet count yields HIGH and 12*82=984")

print("\nREGRESSION FAILURES:", len(reg))
for f in reg:
    print("  -", f)
