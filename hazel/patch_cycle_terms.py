"""
Patch cycle.py: correct retailer terms, and tax the ITEM rather than the store.

TWO DEFECTS, opposite directions, both distorting the split.

DEFECT 1 -- KROGER CHARGED A DELIVERY FEE ON A PICKUP ORDER
    Was:  kroger free_ship_threshold=35, delivery_fee=9.95, order_minimum=35
    King Soopers pickup is FREE on orders over $35, and $4.95 below it against
    a $35 minimum. Delivery is the expensive path and is not what Hazel does:
    cart.py submits with modality=PICKUP by default.

    So every comparison since day one has charged Kroger a $9.95 fee it would
    never incur, applied against the whole Kroger subtotal. That is enough to
    lose a slot on its own.

DEFECT 2 -- TAX MODELLED PER RETAILER INSTEAD OF PER ITEM
    Was:  kroger 0.0, walmart 0.081, amazon 0.081

    Tax does not vary by store. It varies by GOODS. Colorado exempts food for
    home consumption (C.R.S. 39-26-707), and both Broomfield and Westminster
    exempt it too, so eggs are untaxed at King Soopers AND at Walmart. Paper
    towels are taxed at both. The old model taxed Walmart's eggs and exempted
    Kroger's paper towels -- wrong in both directions at once.

    LineOption already carries a per-line `taxable` flag and landed_cost
    already honours it. Nothing was ever setting it, so it defaulted True and
    taxability got approximated at the retailer level. This patch sets it from
    the slot category and applies ONE rate everywhere.

RATE
    HOUSEHOLD_TAX_RATE, default 0.0815 (Broomfield combined, ZIP 80020).
    Westminster is 9.0% after the city rate rose to 4.25% on 2026-01-01. Both
    exempt groceries, so this only affects non-food lines. Confirm against a
    receipt for a TAXABLE item and set the env var if it differs -- no code
    change required.

BEVERAGES ARE SPLIT, so category alone is not enough
    Colorado taxes carbonated soft drinks but not juice. The beverages
    category holds apple_juice (exempt) and two Monster slots (taxable), so
    those two are named explicitly rather than sweeping the category.

Idempotent: refuses to double-apply. Backs up before writing. Verifies by
compiling and re-reading, not by trusting the write.
"""

import os
import shutil
import sys
import time

# Data lives next to the code. /data/hazel on Railway and /opt/data/hazel
# on Portal are both 'the directory this file sits in', so this default is
# correct on either without an environment variable. Set HAZEL_HOME to
# split code from data.
HAZEL_HOME = os.environ.get(
    "HAZEL_HOME", os.path.dirname(os.path.abspath(__file__)))

TARGET = os.environ.get("HAZEL_CYCLE", os.path.join(HAZEL_HOME, "cycle.py"))

OLD_TERMS = '''DEFAULT_TERMS = {
    "kroger":  RetailerTerms("kroger",  free_ship_threshold=35.0,
                             delivery_fee=9.95, order_minimum=35.0, tax_rate=0.0),
    "walmart": RetailerTerms("walmart", free_ship_threshold=35.0,
                             delivery_fee=6.99, tax_rate=0.081),
    "amazon":  RetailerTerms("amazon",  free_ship_threshold=35.0,
                             delivery_fee=6.99, membership=True, tax_rate=0.081),
}'''

NEW_TERMS = '''# Local combined rate for TAXABLE goods. Groceries are exempt everywhere
# relevant, so this only ever touches non-food lines. Broomfield (80020) is
# 8.15%; Westminster is 9.0% since 2026-01-01. Override without a code change.
TAX_RATE = float(os.environ.get("HOUSEHOLD_TAX_RATE", "0.0815"))

# Food for home consumption is exempt (C.R.S. 39-26-707), and both Broomfield
# and Westminster follow the state exemption. Everything here is NOT food.
TAXABLE_CATEGORIES = {
    "household",          # cleaning, personal care, filters, wipes
    "roll_goods",         # paper towels, toilet paper
    "laundry_detergent",
    "supplements",        # dietary supplements are outside the SNAP definition
}

# Colorado taxes carbonated soft drinks but not juice, so the beverages
# category splits. Named explicitly rather than sweeping the category, which
# would wrongly tax apple_juice.
TAXABLE_SLOTS = {
    "energy_drinks_variety",
    "energy_drinks_zero",
}

EXEMPT_SLOTS = set()   # escape hatch: force-exempt a slot in a taxable category


def is_taxable(category, slot_id=None):
    """Taxability is a property of the GOODS, not of the store selling them."""
    if slot_id and slot_id in EXEMPT_SLOTS:
        return False
    if slot_id and slot_id in TAXABLE_SLOTS:
        return True
    return category in TAXABLE_CATEGORIES


# Kroger figure is PICKUP, which is what cart.py submits. Free over $35,
# $4.95 below it, $35 minimum. Delivery would be ~$9.95 and is not used.
# One tax rate everywhere: the store does not change what a good is.
DEFAULT_TERMS = {
    "kroger":  RetailerTerms("kroger",  free_ship_threshold=35.0,
                             delivery_fee=4.95, order_minimum=35.0,
                             tax_rate=TAX_RATE),
    "walmart": RetailerTerms("walmart", free_ship_threshold=35.0,
                             delivery_fee=6.99, tax_rate=TAX_RATE),
    "amazon":  RetailerTerms("amazon",  free_ship_threshold=35.0,
                             delivery_fee=6.99, membership=True,
                             tax_rate=TAX_RATE),
}'''

OLD_BEST = '''def best_per_retailer(ranked):
    """Cheapest comparable option at each retailer -> optimizer input."""
    seen, out = {}, []
    for c in ranked:
        if c.unit_price is None:
            continue
        if c.retailer not in seen or c.unit_price < seen[c.retailer].unit_price:
            seen[c.retailer] = c
    for c in seen.values():
        out.append(LineOption(slot_id="", retailer=c.retailer,
                              product_key=c.product_key, title=c.title,
                              price=c.price, unit_price=c.unit_price))
    return out'''

NEW_BEST = '''def best_per_retailer(ranked, category=None, slot_id=None):
    """
    Cheapest comparable option at each retailer -> optimizer input.

    taxable is set HERE, from the slot, because it is a property of the goods.
    Leaving it at its default of True was how tax ended up approximated at the
    retailer level.
    """
    taxable = is_taxable(category, slot_id)
    seen, out = {}, []
    for c in ranked:
        if c.unit_price is None:
            continue
        if c.retailer not in seen or c.unit_price < seen[c.retailer].unit_price:
            seen[c.retailer] = c
    for c in seen.values():
        out.append(LineOption(slot_id="", retailer=c.retailer,
                              product_key=c.product_key, title=c.title,
                              price=c.price, unit_price=c.unit_price,
                              taxable=taxable))
    return out'''

OLD_CALL = '''        opts = best_per_retailer(res["ranked"])'''
NEW_CALL = '''        opts = best_per_retailer(res["ranked"],
                                 category=slot.get("category"),
                                 slot_id=slot["slot_id"])'''

EDITS = [("retailer terms", OLD_TERMS, NEW_TERMS),
         ("best_per_retailer", OLD_BEST, NEW_BEST),
         ("call site", OLD_CALL, NEW_CALL)]


def main():
    if not os.path.exists(TARGET):
        raise SystemExit(f"FAIL  no such file: {TARGET}")
    src = open(TARGET, encoding="utf-8").read()

    if "TAXABLE_CATEGORIES" in src:
        print("ALREADY PATCHED. Nothing to do.")
        return 0

    # Assert every anchor exists BEFORE writing anything. A partial patch is
    # worse than no patch.
    for name, old, _ in EDITS:
        if src.count(old) != 1:
            raise SystemExit(
                f"FAIL  anchor {name!r} found {src.count(old)} times, "
                f"expected exactly 1. File has drifted; not patching.")
    if "import os" not in src:
        raise SystemExit("FAIL  cycle.py does not import os; TAX_RATE needs it")

    out = src
    for name, old, new in EDITS:
        out = out.replace(old, new, 1)
        print(f"  patched: {name}")

    try:
        compile(out, TARGET, "exec")
    except SyntaxError as e:
        raise SystemExit(f"FAIL  patched source does not compile: {e}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    bak = f"{TARGET}.bak.{stamp}"
    shutil.copy2(TARGET, bak)
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(out)

    # Re-read from disk. Do not trust the write.
    back = open(TARGET, encoding="utf-8").read()
    if back != out:
        shutil.copy2(bak, TARGET)
        raise SystemExit("FAIL  readback mismatch; restored from backup")
    compile(back, TARGET, "exec")

    print(f"\nOK    {TARGET} patched and verified")
    print(f"      backup: {bak}")
    print(f"      {len(src)} -> {len(back)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
