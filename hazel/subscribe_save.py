#!/usr/bin/env python3
"""
Amazon Subscribe & Save monitoring system.
Tracks delivery schedules, pricing, and availability alerts.
"""

import sqlite3
import json
import sys
from datetime import datetime, timezone, timedelta
import os

# Data lives next to the code. /data/hazel on Railway and /opt/data/hazel
# on Portal are both 'the directory this file sits in', so this default is
# correct on either without an environment variable. Set HAZEL_HOME to
# split code from data.
HAZEL_HOME = os.environ.get(
    "HAZEL_HOME", os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.path.join(HAZEL_HOME, "hazel.db")

SCHEMA_ADDITION = """
-- Amazon Subscribe & Save tracking
CREATE TABLE IF NOT EXISTS subscribe_save (
    item_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id         TEXT,  -- Link to slots table if applicable
    product_name    TEXT NOT NULL,
    brand           TEXT,
    asin            TEXT,  -- Amazon ASIN if known
    frequency       TEXT NOT NULL,  -- monthly, 2_months, 3_months, 5_months, 6_months
    price           REAL,
    savings_percent INTEGER,
    next_delivery   TEXT,  -- ISO date of next scheduled delivery
    last_delivery   TEXT,  -- ISO date of last actual delivery
    status          TEXT DEFAULT 'active',  -- active, paused, unavailable, cancelled
    notes           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (slot_id) REFERENCES slots(slot_id)
);
CREATE INDEX IF NOT EXISTS idx_subscribe_save_delivery
    ON subscribe_save(next_delivery) WHERE status = 'active';
"""

def init_subscribe_save_table(con):
    """Add subscribe_save table if it doesn't exist"""
    con.executescript(SCHEMA_ADDITION)
    con.commit()

def add_item(con, product_name, frequency, price=None, savings_percent=None, 
             brand=None, slot_id=None, asin=None, next_delivery=None, notes=None):
    """Add Subscribe & Save item"""
    now = datetime.now(timezone.utc).isoformat()
    cur = con.execute("""
        INSERT INTO subscribe_save 
        (product_name, brand, asin, frequency, price, savings_percent, 
         next_delivery, slot_id, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (product_name, brand, asin, frequency, price, savings_percent, 
          next_delivery, slot_id, notes, now, now))
    con.commit()
    return cur.lastrowid

def update_status(con, item_id, status, notes=None):
    """Update item status (active, paused, unavailable, cancelled)"""
    now = datetime.now(timezone.utc).isoformat()
    con.execute("""
        UPDATE subscribe_save
        SET status = ?, notes = COALESCE(?, notes), updated_at = ?
        WHERE item_id = ?
    """, (status, notes, now, item_id))
    con.commit()

def record_delivery(con, item_id, delivery_date=None):
    """Record that an item was delivered"""
    if not delivery_date:
        delivery_date = datetime.now(timezone.utc).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    con.execute("""
        UPDATE subscribe_save
        SET last_delivery = ?, updated_at = ?
        WHERE item_id = ?
    """, (delivery_date, now, item_id))
    con.commit()

def get_all_items(con):
    """Get all Subscribe & Save items"""
    cur = con.execute("""
        SELECT item_id, slot_id, product_name, brand, frequency, price, 
               savings_percent, next_delivery, last_delivery, status, notes
        FROM subscribe_save
        ORDER BY 
            CASE frequency
                WHEN 'monthly' THEN 1
                WHEN '2_months' THEN 2
                WHEN '3_months' THEN 3
                WHEN '5_months' THEN 5
                WHEN '6_months' THEN 6
                ELSE 99
            END,
            product_name
    """)
    return [{"item_id": r[0], "slot_id": r[1], "product_name": r[2], 
             "brand": r[3], "frequency": r[4], "price": r[5],
             "savings_percent": r[6], "next_delivery": r[7], 
             "last_delivery": r[8], "status": r[9], "notes": r[10]} 
            for r in cur.fetchall()]

def get_upcoming_deliveries(con, days=30):
    """Get items with deliveries in next N days"""
    cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    cur = con.execute("""
        SELECT item_id, product_name, brand, next_delivery, status
        FROM subscribe_save
        WHERE next_delivery IS NOT NULL 
          AND next_delivery <= ?
          AND status = 'active'
        ORDER BY next_delivery
    """, (cutoff,))
    return [{"item_id": r[0], "product_name": r[1], "brand": r[2],
             "next_delivery": r[3], "status": r[4]} for r in cur.fetchall()]

def get_unavailable_items(con):
    """Get items marked as unavailable"""
    cur = con.execute("""
        SELECT item_id, product_name, brand, notes
        FROM subscribe_save
        WHERE status = 'unavailable'
        ORDER BY product_name
    """)
    return [{"item_id": r[0], "product_name": r[1], "brand": r[2], 
             "notes": r[3]} for r in cur.fetchall()]

def main():
    con = sqlite3.connect(DB_PATH)
    
    if len(sys.argv) < 2:
        print("Usage: subscribe_save.py <init|add|list|upcoming|unavailable|update-status|record-delivery>")
        return 1
    
    cmd = sys.argv[1]
    
    if cmd == "init":
        init_subscribe_save_table(con)
        print(json.dumps({"status": "initialized"}, indent=2))
    
    elif cmd == "add":
        if len(sys.argv) < 4:
            print("Usage: subscribe_save.py add <product_name> <frequency> [price] [savings_pct] [brand] [slot_id]")
            return 1
        product_name = sys.argv[2]
        frequency = sys.argv[3]
        price = float(sys.argv[4]) if len(sys.argv) > 4 else None
        savings = int(sys.argv[5]) if len(sys.argv) > 5 else None
        brand = sys.argv[6] if len(sys.argv) > 6 else None
        slot_id = sys.argv[7] if len(sys.argv) > 7 else None
        item_id = add_item(con, product_name, frequency, price, savings, brand, slot_id)
        print(json.dumps({"added": item_id, "product": product_name}, indent=2))
    
    elif cmd == "list":
        items = get_all_items(con)
        print(json.dumps({"subscribe_save_items": items, "count": len(items)}, indent=2))
    
    elif cmd == "upcoming":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        items = get_upcoming_deliveries(con, days)
        print(json.dumps({"upcoming_deliveries": items, "within_days": days}, indent=2))
    
    elif cmd == "unavailable":
        items = get_unavailable_items(con)
        print(json.dumps({"unavailable_items": items, "count": len(items)}, indent=2))
    
    elif cmd == "update-status":
        if len(sys.argv) < 4:
            print("Usage: subscribe_save.py update-status <item_id> <status> [notes]")
            return 1
        item_id = int(sys.argv[2])
        status = sys.argv[3]
        notes = sys.argv[4] if len(sys.argv) > 4 else None
        update_status(con, item_id, status, notes)
        print(json.dumps({"updated": item_id, "status": status}, indent=2))
    
    elif cmd == "record-delivery":
        if len(sys.argv) < 3:
            print("Usage: subscribe_save.py record-delivery <item_id> [delivery_date]")
            return 1
        item_id = int(sys.argv[2])
        delivery_date = sys.argv[3] if len(sys.argv) > 3 else None
        record_delivery(con, item_id, delivery_date)
        print(json.dumps({"recorded_delivery": item_id}, indent=2))
    
    else:
        print(f"Unknown command: {cmd}")
        return 1
    
    con.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
