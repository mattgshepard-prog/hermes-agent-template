# Subscribe & Save Monitoring System

**Created:** August 9, 2026  
**Total Items:** 14  
**Next Delivery:** August 21, 2026 → 1104 McIntosh Ave, Broomfield, CO  
**Edit Deadline:** August 15, 2026

## System Overview

Three-part system:
1. **Catalog slots** — brand-locked to prevent duplicate ordering through other retailers
2. **Tracker database** — monitors delivery schedules, pricing, availability
3. **Alert system** — flags issues requiring attention

## Commands

### View all Subscribe & Save items
```bash
python3 /opt/data/hazel/subscribe_save.py list
```

### Check upcoming deliveries (next 30 days)
```bash
python3 /opt/data/hazel/subscribe_save.py upcoming [days]
```

### View unavailable items
```bash
python3 /opt/data/hazel/subscribe_save.py unavailable
```

### Update item status
```bash
python3 /opt/data/hazel/subscribe_save.py update-status <item_id> <status> [notes]
# Status: active, paused, unavailable, cancelled
```

### Record delivery received
```bash
python3 /opt/data/hazel/subscribe_save.py record-delivery <item_id> [date]
```

## Current Tracked Items

### Monthly (4 items) - $124.39/month
1. **Starbucks Whole Coffee Beans, Dark** - $56.73 (15% savings)
2. **Bona Hardwood Floor Cleaner Refill** - $17.97 (10% savings)
3. **Monster Energy Ultra Variety Pack** - $26.76 (15% savings)
4. **Monster Energy Zero Ultra, Sugar Free** - $22.93 (15% savings)

### Every 2 Months (1 item) - $10.49/month average
5. **Tide PODS laundry detergent, 3-in-1** - $20.97 (15% savings)

### Every 3 Months (1 item) - $3.16/month average
6. **Vanish Drop-Ins Blue, 1.7 oz 4-pack** - $9.49 (5% savings)

### Every 5 Months (1 item) - $1.46/month average
7. **FOVURTE Bamboo Cotton Swabs 400ct** - $7.30 (15% savings)

### Every 6 Months (7 items) - $18.17/month average
8. **Colgate Total Whitening Toothpaste** - $13.58 (15% savings)
9. **Cottonelle Fresh Feel Flushable Wipes** - $13.42 (15% savings)
10. **ICEPURE UKF8001 Refrigerator Water Filter** - $16.41 (20% savings)
11. **Replacement Shaver Part Cutter** - $26.60 (5% savings)
12. **Finish Jet-Dry Dishwasher Rinse Aid** - $12.32 (15% savings)
13. **Clorox Concentrated Bleach Powder** - $13.69 (15% savings)
14. ⚠️ **ZEISS Pre-Moistened Lens Cleaning Wipes** - 5% savings - **UNAVAILABLE**

**Total average monthly cost:** ~$157.67/month

## Current Alerts

### ⚠️ ZEISS Lens Wipes - Temporarily Unavailable
- **Item ID:** 14
- **Status:** Unavailable
- **Frequency:** Every 6 months
- **Next delivery:** August 21, 2026
- **Action required by:** August 15, 2026
- **Notes:** Temporarily unavailable per Amazon. Need to either:
  - Find substitute product
  - Remove from this delivery
  - Wait for availability

**Recommended action:** Check Amazon before August 15 deadline and either substitute or skip.

## Integration with Hazel

All Subscribe & Save items are **brand-locked in the catalog** to prevent:
- Accidentally ordering the same item through Walmart/King Soopers
- Price comparisons on items already on auto-delivery
- Duplicate inventory

When cycle runs, these items are excluded from shopping basket because they're already on auto-delivery.

## Monthly Spend Breakdown

| Frequency | Items | Monthly Average | Total/Cycle |
|-----------|-------|-----------------|-------------|
| Monthly | 4 | $124.39 | $124.39 |
| 2 months | 1 | $10.49 | $20.97 |
| 3 months | 1 | $3.16 | $9.49 |
| 5 months | 1 | $1.46 | $7.30 |
| 6 months | 7 | $18.17 | $109.02 |
| **Total** | **14** | **~$157.67** | varies |

## Next Steps

1. **Before August 15:** Resolve ZEISS wipes unavailability
2. **After each delivery:** Record delivery dates using `record-delivery`
3. **Periodic review:** Check if Subscribe & Save prices remain competitive
4. **Integration:** Consider linking to cycle.py to show "items already ordered" message
