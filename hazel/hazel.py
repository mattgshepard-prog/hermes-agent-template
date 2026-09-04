#!/usr/bin/env python3
"""
Hazel CLI. Every household-pricing operation goes through here.

The agent calls these subcommands. It does NOT do the arithmetic itself:
ranking, unit-price math, threshold optimization and verdict filtering are all
deterministic and live in code, where they cannot be argued with.

    hazel.py slots [--add ...]
    hazel.py price <slot_id>
    hazel.py cycle [--force] [--stage]
    hazel.py approve [--basket PATH] [--delivery] [--dry-run]
    hazel.py verdict <slot_id> <product_key> <keep|acceptable|rejected|untried>
    hazel.py pantry [--set slot=units] [--proposals] [--confirm ID]
    hazel.py alerts [--resolve ID]
    hazel.py backup
    hazel.py doctor
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import alerts as A          # noqa: E402
import cart as CT           # noqa: E402
import cycle as C           # noqa: E402
import menu as M            # noqa: E402
import pantry as P          # noqa: E402
import prices               # noqa: E402
import shopping_list as SL  # noqa: E402
import store as S           # noqa: E402

# Data lives next to the code. /data/hazel on Railway and /opt/data/hazel
# on Portal are both 'the directory this file sits in', so this default is
# correct on either without an environment variable. Set HAZEL_HOME to
# split code from data.
HAZEL_HOME = os.environ.get(
    "HAZEL_HOME", os.path.dirname(os.path.abspath(__file__)))

BACKUP_DIR = os.environ.get("HAZEL_BACKUP", os.path.join(HAZEL_HOME, "backup"))


def out(obj):
    print(json.dumps(obj, indent=2, default=str))


def cmd_slots(args, con):
    if args.add:
        slot_id, display, category, uom, term = args.add
        S.upsert_slot(con, slot_id, display, category, uom, [term],
                      cadence=args.cadence, max_stock=args.max_stock,
                      brand_policy=args.brand_policy,
                      locked_brand=args.locked_brand,
                      must_not_have=args.must_not_have or [])
        out({"added": slot_id})
        return
    out(S.list_slots(con))


def cmd_price(args, con):
    slot = S.get_slot(con, args.slot_id)
    if not slot:
        out({"error": f"no slot {args.slot_id!r}"})
        return 1
    res = C.price_slot(con, slot, record=not args.dry_run)
    out({
        "slot": res["slot_id"], "term": res["term"],
        "errors": res["errors"],
        "dropped": [{"key": d[0]["product_key"], "why": d[1]} for d in res["dropped"]],
        "not_comparable": [{"title": c.title[:60],
                            "confidence": c.pack.confidence,
                            "why": c.pack.notes} for c in res["skipped"]],
        "ranked": [c.to_dict() for c in res["ranked"]],
    })


def cmd_cycle(args, con):
    basket = C.run_cycle(con, force=args.force)
    if args.stage:
        basket["staged_to"] = C.stage(basket)
    if args.json:
        out(basket)
    else:
        print(C.summarize(basket))
        if basket.get("staged_to"):
            print(f"\nstaged: {basket['staged_to']}")


def cmd_links(args, con):
    """
    One-click cart URLs for a staged basket.

    Kroger's cart is filled by `approve`; its link just opens the cart.
    Amazon and Walmart are filled BY the link itself. Neither reports a
    failed add, so the output says so rather than implying success.
    """
    import links as LK
    path = args.basket or CT.latest_basket()
    d = LK.build(path)
    if args.json:
        out(d)
    else:
        print(d["text"])


def cmd_defer(args, con):
    """Skip a slot on future cycles. Reversible with --undo."""
    active = 1 if args.undo else 0
    row = con.execute("SELECT display_name FROM slots WHERE slot_id=?",
                      (args.slot_id,)).fetchone()
    if not row:
        out({"error": "no such slot", "slot_id": args.slot_id})
        return
    con.execute("UPDATE slots SET active=? WHERE slot_id=?",
                (active, args.slot_id))
    con.commit()
    S.raise_alert(con, args.slot_id, "deferred",
                  args.reason or ("undeferred by user" if args.undo
                                  else "deferred by user"), 0.0)
    out({"slot_id": args.slot_id, "active": active,
         "reason": args.reason or ""})


def cmd_switch(args, con):
    """
    Pin a slot to one retailer until cleared.

    Stored on the slot rather than edited into a basket, so the preference
    survives the next cycle instead of being silently undone by it.
    """
    row = con.execute("SELECT display_name FROM slots WHERE slot_id=?",
                      (args.slot_id,)).fetchone()
    if not row:
        out({"error": "no such slot", "slot_id": args.slot_id})
        return
    cols = [r[1] for r in con.execute("PRAGMA table_info(slots)")]
    if "prefer_retailer" not in cols:
        con.execute("ALTER TABLE slots ADD COLUMN prefer_retailer TEXT")
    val = None if args.clear else args.retailer
    con.execute("UPDATE slots SET prefer_retailer=? WHERE slot_id=?",
                (val, args.slot_id))
    con.commit()
    out({"slot_id": args.slot_id, "prefer_retailer": val})


def cmd_approve(args, con):
    """
    Turn the Kroger portion of a staged basket into a real cart.

    PICKUP unless --delivery is passed for THIS approval. The flag is not
    sticky: the choice is made per order by a human, never carried forward,
    because groceries arriving at the house when pickup was meant is not a
    recoverable mistake.

    Amazon and Walmart lines are reported as deferred, never submitted. There
    is no sanctioned cart write for either.
    """
    modality = "DELIVERY" if args.delivery else "PICKUP"
    try:
        res = CT.approve(basket_path=args.basket, modality=modality,
                         dry_run=args.dry_run)
    except CT.CartAuthError as e:
        out({"error": "auth", "detail": str(e),
             "action": "re-run authorize_kroger.py on a machine with a browser; "
                       "retrying will not help"})
        return 2
    except CT.CartError as e:
        out({"error": "cart", "detail": str(e)})
        return 1
    if args.json:
        out(res)
    else:
        print(CT.summarize_approval(res))
    return 0


def cmd_verdict(args, con):
    S.set_verdict(con, args.slot_id, args.product_key, args.verdict,
                  reason=args.reason)
    n = S.export_verdicts(con, os.path.join(BACKUP_DIR, "verdicts.json"))
    out({"slot": args.slot_id, "product": args.product_key,
         "verdict": args.verdict, "verdicts_exported": n})


def cmd_pantry(args, con):
    P.init(con)
    if args.set:
        slot_id, units = args.set.split("=", 1)
        P.set_on_hand(con, slot_id, float(units), "stated")
        out({"set": slot_id, "units": float(units)})
        return
    if args.confirm:
        out(P.confirm_proposal(con, int(args.confirm), args.units))
        return
    if args.reject:
        P.reject_proposal(con, int(args.reject))
        out({"rejected": int(args.reject)})
        return
    if args.proposals:
        out(P.open_proposals(con))
        return
    rows = []
    for slot in S.list_slots(con):
        oh = P.get_on_hand(con, slot["slot_id"])
        rows.append({
            "slot": slot["slot_id"],
            "on_hand": oh["on_hand_units"] if oh else None,
            "source": oh["source"] if oh else None,
            "updated": oh["updated_at"] if oh else None,
            "weeks_left": P.weeks_remaining(con, slot["slot_id"], slot["category"]),
            "needs_restock": P.needs_restock(con, slot["slot_id"], slot["category"]),
        })
    out({"pantry": rows,
         "stale": P.stale_slots(con),
         "open_proposals": len(P.open_proposals(con)),
         "vision_accuracy": P.vision_accuracy(con)})


def cmd_alerts(args, con):
    if args.resolve:
        S.resolve_alert(con, int(args.resolve), args.note or "resolved")
        out({"resolved": int(args.resolve)})
        return
    out(S.open_alerts(con, limit=args.limit))


def cmd_backup(args, con):
    """
    The verdict table is the ONE thing here that cannot be regenerated.
    Price history rebuilds itself in a few cycles; months of living with
    products does not.
    """
    n = S.export_verdicts(con, os.path.join(BACKUP_DIR, "verdicts.json"))
    pruned = S.rollup_and_prune(con) if args.prune else 0
    out({"verdicts_exported": n,
         "backup_dir": BACKUP_DIR,
         "raw_observations_pruned": pruned,
         "observations_remaining": S.observation_count(con)})


def cmd_doctor(args, con):
    """Verify live state. Never trust configuration alone."""
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})

    for k in ("SERPAPI_API_KEY", "KROGER_CLIENT_ID", "KROGER_CLIENT_SECRET",
              "HOUSEHOLD_ZIP"):
        add(f"env:{k}", bool(os.environ.get(k)),
            "present" if os.environ.get(k) else "MISSING")

    try:
        prices.kroger_token()
        add("kroger:token", True, "client_credentials ok")
        loc = prices.kroger_location_id()
        add("kroger:location", True, f"locationId {loc}")
    except Exception as e:
        add("kroger:token", False, e)

    # Cart auth is a SEPARATE credential from the price token. A working price
    # token says nothing about whether the cart can be written.
    try:
        CT.kroger_user_token()
        add("kroger:cart_token", True, "user token ok")
    except Exception as e:
        add("kroger:cart_token", False, e)

    if not args.offline:
        try:
            r = prices.search_walmart("paper towels", limit=1)
            add("serpapi:walmart", bool(r), f"{len(r)} results")
        except Exception as e:
            add("serpapi:walmart", False, e)

    add("db:slots", True, f"{len(S.list_slots(con))} slots")
    add("db:observations", True, f"{S.observation_count(con)} observations")
    add("db:open_alerts", True, f"{len(S.open_alerts(con))} open")

    failed = [c for c in checks if not c["ok"]]
    out({"checks": checks, "failures": len(failed),
         "status": "OK" if not failed else "DEGRADED"})
    return 1 if failed else 0


def cmd_menu(args, con):
    """Meal planning commands."""
    if args.action == "show":
        menu = M.get_menu(con, args.week)
        out(menu)
    
    elif args.action == "meals":
        meals = M.list_meals(con)
        out({"meals": meals})
    
    elif args.action == "add-meal":
        if not args.meal or not args.meal_name:
            out({"error": "add-meal requires --meal (id) and --meal-name"})
            return 1
        ingredients = json.loads(args.ingredients) if args.ingredients else []
        M.add_meal(con, args.meal, args.meal_name, args.description or "", ingredients)
        out({"added": args.meal, "name": args.meal_name})
    
    elif args.action == "set":
        if args.day is None or not args.meal:
            out({"error": "set requires --day and --meal"})
            return 1
        week = args.week or M.get_week_start()
        M.set_menu(con, week, args.day, args.meal, args.notes)
        out({"week": week, "day": args.day, "meal": args.meal})
    
    elif args.action == "clear":
        week = args.week or M.get_week_start()
        M.clear_menu(con, week)
        out({"cleared": week})
    
    elif args.action == "ingredients":
        week = args.week or M.get_week_start()
        needed = M.get_needed_slots(con, week)
        out({"week": week, "ingredients": needed})


def cmd_list(args, con):
    """Shopping list commands."""
    if args.action == "show":
        items = SL.list_items(con, include_purchased=args.all)
        out({"items": items, "total": len(items)})
    
    elif args.action == "add":
        if not args.item:
            out({"error": "add requires --item"})
            return 1
        item_id = SL.add_item(con, args.item, slot_id=args.slot, notes=args.notes)
        out({"added": item_id, "item": args.item})
    
    elif args.action == "map":
        if not args.item_id or not args.slot:
            out({"error": "map requires --item-id and --slot"})
            return 1
        SL.map_item(con, int(args.item_id), args.slot)
        out({"mapped": int(args.item_id), "slot": args.slot})
    
    elif args.action == "done":
        if not args.item_id:
            out({"error": "done requires --item-id"})
            return 1
        SL.mark_purchased(con, int(args.item_id))
        out({"purchased": int(args.item_id)})
    
    elif args.action == "remove":
        if not args.item_id:
            out({"error": "remove requires --item-id"})
            return 1
        SL.remove_item(con, int(args.item_id))
        out({"removed": int(args.item_id)})
    
    elif args.action == "clear":
        removed = SL.clear_purchased(con)
        out({"cleared": removed, "action": "removed all purchased items"})


def main():
    ap = argparse.ArgumentParser(prog="hazel")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("slots")
    p.add_argument("--add", nargs=5,
                   metavar=("SLOT_ID", "DISPLAY", "CATEGORY", "UOM", "TERM"))
    p.add_argument("--cadence", default="monthly",
                   choices=["weekly", "monthly", "quarterly"])
    p.add_argument("--max-stock", type=float, dest="max_stock")
    p.add_argument("--brand-policy", default="open",
                   choices=["open", "locked", "preferred"], dest="brand_policy")
    p.add_argument("--locked-brand", dest="locked_brand")
    p.add_argument("--must-not-have", nargs="*", dest="must_not_have")
    p.set_defaults(fn=cmd_slots)

    p = sub.add_parser("price")
    p.add_argument("slot_id")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_price)

    p = sub.add_parser("cycle")
    p.add_argument("--force", action="store_true")
    p.add_argument("--stage", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_cycle)

    p = sub.add_parser("approve")
    p.add_argument("--basket", help="path to a staged basket; default is newest")
    p.add_argument("--delivery", action="store_true",
                   help="DELIVERY instead of PICKUP for this order only")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be submitted, submit nothing")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("links")
    p.add_argument("--basket", help="path to a staged basket; default is newest")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_links)

    p = sub.add_parser("defer")
    p.add_argument("slot_id")
    p.add_argument("--reason")
    p.add_argument("--undo", action="store_true")
    p.set_defaults(fn=cmd_defer)

    p = sub.add_parser("switch")
    p.add_argument("slot_id")
    p.add_argument("--retailer", choices=["kroger", "walmart", "amazon"])
    p.add_argument("--clear", action="store_true")
    p.set_defaults(fn=cmd_switch)

    p = sub.add_parser("verdict")
    p.add_argument("slot_id")
    p.add_argument("product_key")
    p.add_argument("verdict", choices=["keep", "acceptable", "rejected", "untried"])
    p.add_argument("--reason")
    p.set_defaults(fn=cmd_verdict)

    p = sub.add_parser("pantry")
    p.add_argument("--set", metavar="SLOT=UNITS")
    p.add_argument("--proposals", action="store_true")
    p.add_argument("--confirm", metavar="PROPOSAL_ID")
    p.add_argument("--units", type=float)
    p.add_argument("--reject", metavar="PROPOSAL_ID")
    p.set_defaults(fn=cmd_pantry)

    p = sub.add_parser("alerts")
    p.add_argument("--resolve", metavar="ALERT_ID")
    p.add_argument("--note")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(fn=cmd_alerts)

    p = sub.add_parser("backup")
    p.add_argument("--prune", action="store_true")
    p.set_defaults(fn=cmd_backup)

    p = sub.add_parser("doctor")
    p.add_argument("--offline", action="store_true")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("menu")
    p.add_argument("action", choices=["show", "set", "clear", "meals", "add-meal", "ingredients"])
    p.add_argument("--week", help="ISO week start date (Monday), defaults to this week")
    p.add_argument("--day", type=int, choices=range(7), help="0=Mon, 6=Sun")
    p.add_argument("--meal", help="meal_id to assign")
    p.add_argument("--meal-name", help="name for new meal")
    p.add_argument("--description", help="description for new meal")
    p.add_argument("--ingredients", help="JSON list of {slot_id, quantity, notes}")
    p.add_argument("--notes", help="notes for menu entry")
    p.set_defaults(fn=cmd_menu)

    p = sub.add_parser("list")
    p.add_argument("action", choices=["show", "add", "map", "done", "remove", "clear"])
    p.add_argument("--item", help="item name to add")
    p.add_argument("--slot", help="slot_id to map to")
    p.add_argument("--item-id", help="item_id for map/done/remove")
    p.add_argument("--notes", help="notes for item")
    p.add_argument("--all", action="store_true", help="include purchased items in show")
    p.set_defaults(fn=cmd_list)

    args = ap.parse_args()
    con = S.connect()
    P.init(con)
    M.init_schema(con)
    sys.exit(args.fn(args, con) or 0)


if __name__ == "__main__":
    main()
