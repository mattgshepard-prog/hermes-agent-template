"""
Meal planning module for Hazel.

Integrates weekly menus with the shopping cycle: plan meals, derive needed
ingredients, flag slots for the cycle to prioritize.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone


def init_schema(con):
    """Create meal planning tables if they don't exist."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS meals (
            meal_id         TEXT PRIMARY KEY,
            meal_name       TEXT NOT NULL UNIQUE,
            description     TEXT,
            ingredients     TEXT NOT NULL,  -- JSON: [{slot_id, quantity, notes}, ...]
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS weekly_menu (
            menu_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start      TEXT NOT NULL,  -- Monday ISO date
            day_of_week     INTEGER NOT NULL,  -- 0=Monday, 6=Sunday
            meal_id         TEXT NOT NULL,
            notes           TEXT,
            created_at      TEXT NOT NULL,
            FOREIGN KEY (meal_id) REFERENCES meals(meal_id),
            UNIQUE(week_start, day_of_week)
        );
        
        CREATE INDEX IF NOT EXISTS idx_menu_week
            ON weekly_menu(week_start);
    """)
    con.commit()


def get_week_start(date_str=None):
    """Return Monday of the week containing date_str (or today)."""
    if date_str:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    else:
        dt = datetime.now(timezone.utc)
    
    # Find Monday (weekday 0)
    days_since_monday = dt.weekday()
    monday = dt - timedelta(days=days_since_monday)
    return monday.date().isoformat()


def add_meal(con, meal_id, meal_name, description, ingredients):
    """
    Add or update a meal in the library.
    
    ingredients: list of dicts with {slot_id, quantity, notes}
    """
    now = datetime.now(timezone.utc).isoformat()
    
    con.execute("""
        INSERT INTO meals (meal_id, meal_name, description, ingredients, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(meal_id) DO UPDATE SET
            meal_name = excluded.meal_name,
            description = excluded.description,
            ingredients = excluded.ingredients,
            updated_at = excluded.updated_at
    """, (meal_id, meal_name, description, json.dumps(ingredients), now, now))
    con.commit()


def list_meals(con):
    """Return all meals in the library."""
    rows = con.execute("""
        SELECT meal_id, meal_name, description, ingredients
        FROM meals
        ORDER BY meal_name
    """).fetchall()
    
    return [{
        'meal_id': r[0],
        'meal_name': r[1],
        'description': r[2],
        'ingredients': json.loads(r[3])
    } for r in rows]


def set_menu(con, week_start, day_of_week, meal_id, notes=None):
    """Assign a meal to a day in the weekly menu."""
    now = datetime.now(timezone.utc).isoformat()
    
    con.execute("""
        INSERT INTO weekly_menu (week_start, day_of_week, meal_id, notes, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(week_start, day_of_week) DO UPDATE SET
            meal_id = excluded.meal_id,
            notes = excluded.notes
    """, (week_start, day_of_week, meal_id, notes, now))
    con.commit()


def get_menu(con, week_start=None):
    """Return the menu for a given week."""
    if not week_start:
        week_start = get_week_start()
    
    rows = con.execute("""
        SELECT wm.day_of_week, wm.meal_id, m.meal_name, m.description, m.ingredients, wm.notes
        FROM weekly_menu wm
        JOIN meals m ON wm.meal_id = m.meal_id
        WHERE wm.week_start = ?
        ORDER BY wm.day_of_week
    """, (week_start,)).fetchall()
    
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    
    return {
        'week_start': week_start,
        'meals': [{
            'day': days[r[0]],
            'day_num': r[0],
            'meal_id': r[1],
            'meal_name': r[2],
            'description': r[3],
            'ingredients': json.loads(r[4]),
            'notes': r[5]
        } for r in rows]
    }


def clear_menu(con, week_start=None):
    """Clear all meals for a given week."""
    if not week_start:
        week_start = get_week_start()
    
    con.execute("DELETE FROM weekly_menu WHERE week_start = ?", (week_start,))
    con.commit()


def get_needed_slots(con, week_start=None):
    """
    Return aggregated slot requirements for a given week.
    
    Returns: list of {slot_id, total_quantity, meals_used_in}
    """
    if not week_start:
        week_start = get_week_start()
    
    menu = get_menu(con, week_start)
    if not menu['meals']:
        return []
    
    # Aggregate ingredients across all meals
    slot_needs = {}
    for meal in menu['meals']:
        for ing in meal['ingredients']:
            slot = ing['slot_id']
            qty = ing.get('quantity', 1)
            
            if slot not in slot_needs:
                slot_needs[slot] = {'slot_id': slot, 'total_quantity': 0, 'meals': []}
            
            slot_needs[slot]['total_quantity'] += qty
            slot_needs[slot]['meals'].append(meal['meal_name'])
    
    return list(slot_needs.values())


def mark_slots_needed(con, week_start=None):
    """
    Flag slots as needed based on weekly menu.
    
    Returns list of slot_ids that should be prioritized in the cycle.
    """
    needed = get_needed_slots(con, week_start)
    return [s['slot_id'] for s in needed]
