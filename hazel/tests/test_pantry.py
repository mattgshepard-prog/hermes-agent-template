import os, tempfile
os.environ["HAZEL_DB"] = os.path.join(tempfile.mkdtemp(), "p.db")

import store as S
import pantry as P

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
    print(("  PASS  " if cond else "  FAIL  ") + msg)


con = S.connect()
P.init(con)
S.upsert_slot(con, "paper_towels", "Paper Towels", "roll_goods", "sheets",
              ["bounty"], cadence="weekly", max_stock=6000)

print("=== HARD RULE: vision cannot write confirmed state ===")
try:
    P.set_on_hand(con, "paper_towels", 1440, "vision")
    check(False, "set_on_hand should REFUSE source='vision'")
except AssertionError:
    check(True, "set_on_hand refuses source='vision' outright")

print("\n=== proposal does not change state ===")
pid = P.propose(con, "paper_towels", 1440, source="vision", uom="sheets",
                model_note="confidence=medium", image_ref="shelf1.jpg")
check(P.get_on_hand(con, "paper_towels") is None,
      "pantry state still empty after a vision proposal")
check(len(P.open_proposals(con)) == 1, "proposal is queued for confirmation")

print("\n=== confirmation promotes it to state ===")
res = P.confirm_proposal(con, pid)
oh = P.get_on_hand(con, "paper_towels")
check(oh and oh["on_hand_units"] == 1440, "confirmed proposal becomes state")
check(oh["source"] == "stated" and oh["confirmed"] == 1,
      "confirmed state is recorded as human-stated, not vision")
check(res["resolution"] == "confirmed", "resolution recorded as confirmed")

print("\n=== correction is recorded as a correction ===")
pid2 = P.propose(con, "paper_towels", 5, source="vision", uom="sheets",
                 model_note="confidence=low; view obscured")
res2 = P.confirm_proposal(con, pid2, final_units=1440)
check(res2["resolution"] == "corrected", "human override logged as corrected")
acc = P.vision_accuracy(con)
check(acc["total"] == 2 and acc["corrected"] == 1,
      f"vision accuracy tracked: {acc}")
print("   ", acc)

print("\n=== rejection leaves state untouched ===")
before = P.get_on_hand(con, "paper_towels")["on_hand_units"]
pid3 = P.propose(con, "paper_towels", 99999, source="vision")
P.reject_proposal(con, pid3)
check(P.get_on_hand(con, "paper_towels")["on_hand_units"] == before,
      "rejected proposal does not alter state")
check(P.open_proposals(con) == [], "no proposals left open")

print("\n=== deliveries are facts ===")
P.add_delivered(con, "paper_towels", 1440, uom="sheets")
check(P.get_on_hand(con, "paper_towels")["on_hand_units"] == 2880,
      "delivery increments on-hand directly")

print("\n=== depletion and restock ===")
use = P.weekly_use(con, "paper_towels", category="roll_goods")
check(use == 300.0, f"falls back to category default: {use}")
wr = P.weeks_remaining(con, "paper_towels", "roll_goods")
check(wr == 9.6, f"2880 sheets / 300 per week = {wr} weeks")
check(not P.needs_restock(con, "paper_towels", "roll_goods"),
      "well stocked -> no restock")

P.set_on_hand(con, "paper_towels", 400, "stated")
check(P.needs_restock(con, "paper_towels", "roll_goods"),
      "400 sheets at 300/week -> restock before next delivery")

P.record_depletion(con, "paper_towels", "2026-07-01", "2026-07-08", 520)
P.record_depletion(con, "paper_towels", "2026-07-08", "2026-07-15", 480)
use2 = P.weekly_use(con, "paper_towels", category="roll_goods")
check(use2 == 500.0, f"observed usage replaces the default: {use2}")

print("\n=== unknown state asks rather than assumes ===")
check(P.needs_restock(con, "never_counted", "roll_goods"),
      "slot with no count returns needs_restock=True")

print("\n=== vision response parsing ===")
good = '''```json
{"items":[{"name":"Bounty Select-A-Size","count":3,"confidence":"high"},
          {"name":"Charmin Ultra Soft","count":1,"confidence":"low","note":"partly hidden"}],
 "obscured":true,"note":"back row not visible"}
```'''
items = P.parse_vision_response(good)
check(len(items) == 2 and items[0]["count"] == 3, "parses fenced JSON")
check(items[1]["obscured"] is True, "obscured flag propagates to each item")

check(P.parse_vision_response("I think there are about five cans?") == [],
      "unparseable prose yields NOTHING, never a guessed count")
check(P.parse_vision_response("") == [], "empty response yields nothing")

print("\n=== photo ingest creates proposals, never state ===")
S.upsert_slot(con, "toilet_paper", "Toilet Paper", "roll_goods", "sheets", ["charmin"])
slot_map = {"bounty select-a-size": "paper_towels",
            "charmin ultra soft": "toilet_paper"}
before_pt = P.get_on_hand(con, "paper_towels")["on_hand_units"]
out = P.ingest_photo_result(con, items, slot_map, image_ref="shelf2.jpg")
check(len(out["proposals"]) == 2, "two proposals created from the photo")
check(P.get_on_hand(con, "paper_towels")["on_hand_units"] == before_pt,
      "photo ingest did NOT change confirmed state")

unknown = [{"name": "Some Unknown Brand", "count": 2, "confidence": "high"}]
out2 = P.ingest_photo_result(con, unknown, slot_map)
check(out2["proposals"] == [] and len(out2["unmapped"]) == 1,
      "unrecognized product is returned unmapped, never guessed into a slot")

print("\nFAILURES:", len(fails))
for f in fails:
    print("  -", f)
