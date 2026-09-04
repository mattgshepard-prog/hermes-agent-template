# Walmart Receipt Reconciliation - August 8, 2026

## Summary

Reconciled Amy's Walmart order against the whiteboard shopping list.
- **Brand preferences locked** for all purchased items
- **New recurring slots added** for items not previously tracked
- **Carryover system created** for unpurchased whiteboard items

---

## Whiteboard → Receipt Matching

### ✓ Purchased (5 items)
| Whiteboard Item | Walmart Product | Price | Brand Locked |
|----------------|----------------|-------|--------------|
| Vanilla Extract | Great Value Pure Vanilla Extract, 1 fl oz | $3.24/fl oz | Great Value |
| Cereal | Malt-O-Meal Frosted Mini Spooners, 40 oz | 14.9¢/oz ($5.97) | Malt-O-Meal |
| mac-n-cheese | Kraft Mac and Cheese, 5 Boxes | (truncated) | Kraft |
| pickles | Great Value Hamburger Dill Chip Pickles, 32 fl oz | 9.1¢/fl oz ($2.92) | Great Value |
| noodle soup | McCormick Lasagna Soup Seasoning, 1.5 oz | $2.77/oz ($1.00) | McCormick |

### ✗ Not Purchased (10 items - now in carryover)
- COQ 10 vitamins *(no slot - needs mapping)*
- eggs
- chicken stock
- chopped onions
- bread
- avocado
- italian sausage
- buns
- ketchup
- fish sticks

---

## New Recurring Slots Added

Items Amy bought that weren't on the whiteboard (all locked to specific brands):

| Slot ID | Product | Brand | Size | Unit Price | Cadence |
|---------|---------|-------|------|------------|---------|
| lasagna_pasta | Lasagna Pasta | Great Value | 16 oz | 11.5¢/oz | monthly |
| butter_chicken_sauce | Butter Chicken Sauce | Patak's | 15 oz | 31.1¢/oz | monthly |
| frozen_vegetables | Frozen Vegetables | Great Value | 12 oz | 9.7¢/oz | weekly |
| canned_tomatoes | Canned Tomatoes | Great Value | 14.5 oz | 6.6¢/oz | monthly |
| bread_flour | Bread Flour | Great Value | 5 lb (80 oz) | 4.9¢/oz | quarterly |
| popcorn_kernels | Popcorn Kernels | Great Value | 32 oz | 7.4¢/oz | monthly |

---

## System Changes

### 1. Brand Locking Implemented
All purchased items now have `brand_policy='locked'` with specific brands:
- When "cereal" appears on future orders → defaults to Malt-O-Meal Frosted Mini Spooners 40oz
- When "pickles" needed → defaults to Great Value Hamburger Dill Chip 32 fl oz
- And so on for all items

### 2. Shopping List Carryover System Created
New table and tool: `/opt/data/hazel/shopping_list.py`

**Commands:**
- `python3 shopping_list.py list` — show unpurchased items
- `python3 shopping_list.py add <item> [slot_id] [notes]` — add to list
- `python3 shopping_list.py mark-purchased <item_id|slot:SLOT_ID>` — mark as bought

**Current active list:** 10 items from whiteboard still needed

### 3. Integration Needed
The carryover list is now populated but not yet integrated into the weekly cycle.
Next step: modify `cycle.py` to include active shopping_list items in the basket.

---

## Walmart Pricing Baseline (August 8, 2026)

| Item | Unit Price | Notes |
|------|------------|-------|
| Malt-O-Meal Frosted Mini Spooners | 14.9¢/oz | Resealable bag |
| Great Value Lasagna Pasta | 11.5¢/oz | |
| McCormick Lasagna Soup Seasoning | $2.77/oz | Packet |
| Patak's Butter Chicken Sauce | 31.1¢/oz | |
| Great Value Frozen California Veg | 9.7¢/oz | Frozen |
| Great Value Diced Tomatoes | 6.6¢/oz | Can, bought ×2 |
| Great Value Bread Flour | 4.9¢/oz | 5 lb bag |
| Great Value Vanilla Extract | $3.24/fl oz | 1 fl oz |
| Great Value Popping Corn | 7.4¢/oz | Yellow |
| Great Value Hamburger Dill Pickles | 9.1¢/fl oz | 32 fl oz |

---

## Next Actions

1. **Test the cycle:** Run `hazel.py cycle` to verify locked brands appear correctly
2. **Map COQ 10 vitamins:** Decide if this should be a recurring slot or one-off purchase
3. **Integrate carryover:** Modify cycle logic to merge shopping_list items into weekly basket
4. **Record verdicts:** After delivery, mark these products as 'untried' or 'acceptable'
