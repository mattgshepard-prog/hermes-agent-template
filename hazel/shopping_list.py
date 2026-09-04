"""
Shopping list management.

Maintains a running list of items needed, separate from staged baskets.
Items can be added ad-hoc ("we're out of X") or unmapped ("whiteboard says
bananas"), then linked to slots for pricing when the basket is built.
"""

from datetime import datetime, timezone


def add_item(con, item_name, slot_id=None, notes=None, added_by='user'):
    """Add an item to the shopping list."""
    now = datetime.now(timezone.utc).isoformat()
    
    cur = con.execute("""
        INSERT INTO shopping_list (slot_id, item_name, added_at, added_by, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (slot_id, item_name, now, added_by, notes))
    con.commit()
    
    return cur.lastrowid


def list_items(con, include_purchased=False):
    """Return all items on the shopping list."""
    if include_purchased:
        rows = con.execute("""
            SELECT item_id, slot_id, item_name, added_at, added_by, purchased_at, notes
            FROM shopping_list
            ORDER BY purchased_at IS NULL DESC, added_at DESC
        """).fetchall()
    else:
        rows = con.execute("""
            SELECT item_id, slot_id, item_name, added_at, added_by, purchased_at, notes
            FROM shopping_list
            WHERE purchased_at IS NULL
            ORDER BY added_at
        """).fetchall()
    
    return [{
        'item_id': r[0],
        'slot_id': r[1],
        'item_name': r[2],
        'added_at': r[3],
        'added_by': r[4],
        'purchased_at': r[5],
        'notes': r[6]
    } for r in rows]


def map_item(con, item_id, slot_id):
    """Link an unmapped item to a slot."""
    con.execute("""
        UPDATE shopping_list
        SET slot_id = ?
        WHERE item_id = ?
    """, (slot_id, item_id))
    con.commit()


def mark_purchased(con, item_id):
    """Mark an item as purchased."""
    now = datetime.now(timezone.utc).isoformat()
    con.execute("""
        UPDATE shopping_list
        SET purchased_at = ?
        WHERE item_id = ?
    """, (now, item_id))
    con.commit()


def remove_item(con, item_id):
    """Remove an item from the list entirely."""
    con.execute("DELETE FROM shopping_list WHERE item_id = ?", (item_id,))
    con.commit()


def clear_purchased(con):
    """Remove all purchased items from the list."""
    con.execute("DELETE FROM shopping_list WHERE purchased_at IS NOT NULL")
    con.commit()
    return con.total_changes


def get_needed_slots(con):
    """
    Return list of slot_ids from unpurchased items that are mapped to slots.
    
    Use this to prioritize slots when building the basket.
    """
    rows = con.execute("""
        SELECT DISTINCT slot_id
        FROM shopping_list
        WHERE purchased_at IS NULL
          AND slot_id IS NOT NULL
    """).fetchall()
    
    return [r[0] for r in rows]
