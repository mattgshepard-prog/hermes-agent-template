"""
Hazel: pack-size normalization.

Turns a retailer's free-text product title into a structured pack size and a
comparable unit price.

DESIGN RULE: this module never guesses. Every parse carries a confidence.
A LOW confidence parse must NOT be compared or alerted on. A silently wrong
unit price is worse than no unit price, because it produces a confident
recommendation to buy the wrong thing.

Roll-equivalence claims ("12 Mega = 48 Regular") are MARKETING ARITHMETIC and
are deliberately NOT trusted as a measurement. They are used only as a weak
fallback when no sheet count is available, and downgrade confidence.
"""

import re
from dataclasses import dataclass, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------

HIGH = "high"      # explicit count + explicit unit size (e.g. "12 rolls, 147 sheets")
MEDIUM = "medium"  # explicit count + inferred unit size from a known roll type
LOW = "low"        # count only, or ambiguous -- NEVER compare on this
NONE = "none"      # nothing parseable

COMPARABLE = (HIGH, MEDIUM)


# ---------------------------------------------------------------------------
# Category unit definitions
# ---------------------------------------------------------------------------
# The "use unit" is what a household actually consumes: a sheet, a load,
# a fluid ounce. Package counts are not comparable across brands.

CATEGORY_UNITS = {
    "paper_towel": "sheets",
    "toilet_paper": "sheets",
    "facial_tissue": "sheets",
    "roll_goods": "sheets",
    "laundry_detergent": "fl_oz",   # NOT "loads" -- see note below
    "laundry_pods": "count",
    "dish_soap": "fl_oz",
    "hand_soap": "fl_oz",
    "shampoo": "fl_oz",
    "trash_bags": "count",
    "food_weight": "oz",
    "food_volume": "fl_oz",
    "generic_count": "count",
}

# "Loads" is a manufacturer claim, not a measurement, and is not comparable
# across brands. Detergent normalizes to fluid ounces; pods to unit count.
UNTRUSTED_UNITS = {"loads", "load", "washes", "uses"}


# Roll-type sheet estimates. These are FALLBACKS ONLY and always yield MEDIUM
# at best. Real sheet counts from the listing always win.
# Values are approximate industry norms; brands vary.
# Calibrated 2026-08-08 against sheet counts Walmart actually publishes:
#   "6 Double Rolls, 108 Sheets per Roll"   -> double ~= 82-108
#   "12 Triple Rolls, 123 Sheets per Roll"  -> triple ~= 123
#   "6 Mega Rolls, 164 Sheets per Roll"     -> mega   ~= 164
# The earlier values (double 120, triple 180) were guesses and OVERSTATED
# sheet counts, which understates unit price and makes every inferred item
# look cheaper than it is. Re-derive these from observed data periodically.
ROLL_TYPE_SHEETS = {
    "regular": 55,
    "double": 95,
    "triple": 123,
    "mega": 164,
    "super mega": 240,
    "family": 140,
    "jumbo": 140,
}

# Sanity bounds for sheets per roll. A parse outside this range is a
# misread, not a product. "3 Sheet Sizes (Quarter, Half, Full)" was read as
# 3 sheets/roll at HIGH confidence and produced a 20x wrong unit price.
SHEETS_PER_ROLL_MIN = 30
SHEETS_PER_ROLL_MAX = 500

ROLL_TYPE_ALIASES = [
    ("super mega", "super mega"),
    ("triple", "triple"),
    ("double", "double"),
    ("family", "family"),
    ("jumbo", "jumbo"),
    ("mega", "mega"),
    ("regular", "regular"),
    ("single", "regular"),
]


@dataclass
class PackSize:
    count: Optional[int] = None            # number of packages (e.g. 12 rolls)
    count_noun: Optional[str] = None       # "rolls", "pods", "bottles"
    unit_size: Optional[float] = None      # size of ONE package (147 sheets)
    unit_of_measure: Optional[str] = None  # "sheets", "fl_oz", "count"
    total_units: Optional[float] = None    # count * unit_size
    roll_type: Optional[str] = None        # double / triple / mega ...
    confidence: str = NONE
    notes: str = ""

    @property
    def comparable(self) -> bool:
        return self.confidence in COMPARABLE and bool(self.total_units)

    def to_dict(self):
        d = asdict(self)
        d["comparable"] = self.comparable
        return d


# ---------------------------------------------------------------------------
# Regex library
# ---------------------------------------------------------------------------

NUM = r"(\d+(?:\.\d+)?)"

RE_SHEETS_PER = re.compile(
    NUM + r"\s*(?:2|3)?[- ]?ply?\s*sheets?\s*(?:per|/)\s*roll", re.I)
RE_SHEETS_PLAIN = re.compile(NUM + r"\s*sheets?", re.I)
# Retailers write "12 Double Rolls", "6 Triple Rolls", "16 Family Rolls".
# The roll-type adjective sits BETWEEN the number and the noun, so it must be
# allowed to intervene. Missing this silently zeroes out Walmart and Amazon.
ROLL_ADJ = (r"(?:super\s+mega|mega|double|triple|family|jumbo|giant|"
            r"regular|single|big|huge|quick[- ]size|select[- ]a[- ]size|"
            r"xl|xxl|plus)")
RE_COUNT_ROLLS = re.compile(
    NUM + r"\s*(?:ct|count)?\s*(?:" + ROLL_ADJ + r"\s+)*\b(rolls?)\b", re.I)
RE_COUNT_GENERIC = re.compile(
    NUM + r"\s*\b(pods?|packs?|bottles?|bars?|boxes?|bags?|cans?|ct|count)\b", re.I)
# "Pack of 12" -- the inverted form. RE_COUNT_GENERIC only matches "<n> pack",
# so "1 Ounce (Pack of 40)" parsed as 1 oz total and silently produced a
# 40x-wrong unit price at HIGH confidence. Found 2026-09-04 after 61 spurious
# unusually_low alerts traced back to poisoned trailing medians.
RE_PACK_OF = re.compile(r"\bpack\s+of\s+" + NUM, re.I)
RE_FL_OZ = re.compile(NUM + r"\s*(?:fl\.?\s*oz|fluid ounces?)", re.I)
RE_OZ = re.compile(NUM + r"\s*(?:oz\b|ounces?)", re.I)
RE_LB = re.compile(NUM + r"\s*(?:lbs?\b|pounds?)", re.I)
RE_ML = re.compile(NUM + r"\s*ml\b", re.I)
RE_LITER = re.compile(NUM + r"\s*(?:l\b|liters?|litres?)", re.I)
RE_LOADS = re.compile(NUM + r"\s*(loads?|washes|uses)\b", re.I)
RE_SQ_FT = re.compile(NUM + r"\s*(?:sq\.?\s*ft|square feet)", re.I)

# "12 Mega Rolls = 48 Regular Rolls" -- captured to DETECT, not to trust.
RE_EQUIV_CLAIM = re.compile(
    NUM + r"\s*(?:[A-Za-z-]+\s+){0,3}(?:rolls?\s*)?=\s*" + NUM +
    r"\s*(?:[A-Za-z-]+\s+){0,3}rolls?", re.I)

# Shrinkflation tell on packaging copy.
RE_RELAUNCH = re.compile(r"new look,?\s*same", re.I)


def _f(m, group=1):
    try:
        return float(m.group(group))
    except (AttributeError, ValueError):
        return None


def detect_roll_type(text: str) -> Optional[str]:
    t = text.lower()
    for needle, canonical in ROLL_TYPE_ALIASES:
        if re.search(r"\b" + re.escape(needle) + r"\b", t):
            return canonical
    return None


def guess_category(text: str) -> str:
    t = text.lower()
    if re.search(r"paper towel|kitchen roll", t):
        return "paper_towel"
    if re.search(r"toilet paper|bath tissue|toilet tissue", t):
        return "toilet_paper"
    if re.search(r"facial tissue|\bkleenex\b", t):
        return "facial_tissue"
    if re.search(r"\bpods?\b|pacs\b", t) and re.search(r"detergent|laundry", t):
        return "laundry_pods"
    if re.search(r"laundry detergent|liquid detergent", t):
        return "laundry_detergent"
    if re.search(r"dish soap|dishwashing liquid", t):
        return "dish_soap"
    if re.search(r"hand soap|hand wash", t):
        return "hand_soap"
    if re.search(r"shampoo|conditioner", t):
        return "shampoo"
    if re.search(r"trash bag|garbage bag|kitchen bag", t):
        return "trash_bags"
    # Fallback: anything sold in rolls and measured in sheets is a sheet good,
    # even when the title never names the product type. Both paper towels and
    # bath tissue normalize to sheets, so the shared unit is what matters.
    # A roll is NOT a comparable unit: a double roll and a triple roll are
    # different amounts of product. Anything sold in rolls therefore goes down
    # the sheets path, where an unknown roll type correctly degrades to LOW
    # instead of counting rolls as if they were interchangeable.
    if re.search(r"\brolls?\b", t):
        return "roll_goods"
    return "generic_count"


def parse_pack(text: str, size_hint: str = "", category: str = None,
               uom: str = None) -> PackSize:
    """
    Parse a product title (optionally plus a retailer 'size' field) into a
    structured pack size.

    size_hint is a separate field some retailers provide (Kroger returns
    "12 rolls"). It is merged with the title because Kroger's size field
    lacks the roll TYPE, which lives only in the description.
    """
    blob = f"{text} {size_hint}".strip()
    cat = category or guess_category(blob)
    # An explicit slot unit_of_measure WINS. Category lookup is only a
    # fallback: categories like "dairy" are absent from CATEGORY_UNITS and
    # silently degrade a fl_oz product to "count", which discards the size.
    uom = uom or CATEGORY_UNITS.get(cat, "count")
    ps = PackSize(unit_of_measure=uom)
    notes = []

    roll_type = detect_roll_type(blob)
    ps.roll_type = roll_type

    # --- package count -----------------------------------------------------
    # A roll-equivalence claim ("12 XL Family Rolls = 40 Regular Rolls") puts
    # an INFLATED number on the right. Take the count from the LEFT side and
    # ignore the right entirely, or the parser buys the marketing.
    equiv = RE_EQUIV_CLAIM.search(blob)
    m = RE_COUNT_ROLLS.search(blob)
    if equiv:
        ps.count = int(float(equiv.group(1)))
        ps.count_noun = "rolls"
        notes.append("count taken from left of roll-equivalence claim; "
                     "right side ignored as marketing")
    elif m:
        ps.count = int(float(m.group(1)))
        ps.count_noun = "rolls"
    else:
        m = RE_COUNT_GENERIC.search(blob)
        if m:
            ps.count = int(float(m.group(1)))
            ps.count_noun = m.group(2).lower()
        else:
            m = RE_PACK_OF.search(blob)
            if m:
                ps.count = int(float(m.group(1)))
                ps.count_noun = "pack"

    # --- sheet-based categories -------------------------------------------
    if uom == "sheets":
        per_roll = _f(RE_SHEETS_PER.search(blob))
        if per_roll is None:
            # A bare "147 sheets" next to a roll count is per-roll in practice.
            plain = RE_SHEETS_PLAIN.search(blob)
            if plain:
                val = _f(plain)
                if val is not None:
                    if ps.count and val > ps.count * 400:
                        # Implausibly large for one roll -> treat as pack total.
                        ps.total_units = val
                        ps.unit_size = val / ps.count if ps.count else None
                        ps.confidence = HIGH
                        notes.append("sheet count read as pack total")
                        ps.notes = "; ".join(notes)
                        return ps
                    per_roll = val

        if per_roll is not None and not (
                SHEETS_PER_ROLL_MIN <= per_roll <= SHEETS_PER_ROLL_MAX):
            notes.append(
                f"sheets/roll of {per_roll:.0f} is outside plausible range "
                f"{SHEETS_PER_ROLL_MIN}-{SHEETS_PER_ROLL_MAX}; parse rejected")
            per_roll = None

        if per_roll is not None and ps.count:
            ps.unit_size = per_roll
            ps.total_units = per_roll * ps.count
            ps.confidence = HIGH
        elif ps.count and roll_type:
            est = ROLL_TYPE_SHEETS.get(roll_type)
            if est:
                ps.unit_size = float(est)
                ps.total_units = float(est) * ps.count
                ps.confidence = MEDIUM
                notes.append(
                    f"sheet count inferred from '{roll_type}' roll type, not listed")
        elif ps.count:
            ps.confidence = LOW
            notes.append("roll count only, no sheet count and no roll type")
        else:
            ps.confidence = NONE
            notes.append("no pack count found")

        if RE_EQUIV_CLAIM.search(blob):
            notes.append("listing makes a roll-equivalence claim; not trusted")
            if ps.confidence == HIGH:
                pass  # real sheet counts still win
            elif ps.confidence == MEDIUM:
                ps.confidence = MEDIUM

    # --- volume categories -------------------------------------------------
    elif uom == "fl_oz":
        loads = RE_LOADS.search(blob)
        if loads:
            notes.append("'loads' claim ignored; not comparable across brands")

        val = _f(RE_FL_OZ.search(blob))
        if val is None:
            ml = _f(RE_ML.search(blob))
            if ml is not None:
                val = ml / 29.5735
                notes.append("converted from ml")
            else:
                lit = _f(RE_LITER.search(blob))
                if lit is not None:
                    val = lit * 33.814
                    notes.append("converted from liters")

        if val is not None:
            ps.unit_size = round(val, 2)
            ps.count = ps.count or 1
            ps.total_units = round(val * ps.count, 2)
            ps.confidence = HIGH
        else:
            ps.confidence = LOW if ps.count else NONE
            notes.append("no fluid volume found")

    # --- count categories --------------------------------------------------
    else:
        wt = _f(RE_OZ.search(blob))
        lb = _f(RE_LB.search(blob))
        if ps.count and wt is not None:
            # "10.5 oz Can (8 Pack)": count x per-unit weight is the real
            # amount of product. Counting the pack as 8 units of size 1
            # silently compares 8 cans against 1 can.
            ps.unit_of_measure = "oz"
            ps.unit_size = wt
            ps.total_units = round(wt * ps.count, 2)
            ps.confidence = HIGH
            notes.append("pack count x unit weight")
        elif ps.count and lb is not None:
            ps.unit_of_measure = "oz"
            ps.unit_size = round(lb * 16, 2)
            ps.total_units = round(lb * 16 * ps.count, 2)
            ps.confidence = HIGH
            notes.append("pack count x unit weight (from pounds)")
        elif ps.count:
            ps.unit_size = 1.0
            ps.total_units = float(ps.count)
            ps.confidence = HIGH
        else:
            if lb is not None:
                ps.unit_of_measure = "oz"
                ps.unit_size = lb * 16
                ps.count = 1
                ps.total_units = lb * 16
                ps.confidence = HIGH
                notes.append("converted from pounds")
            elif wt is not None:
                ps.unit_of_measure = "oz"
                ps.unit_size = wt
                ps.count = 1
                ps.total_units = wt
                ps.confidence = HIGH
            else:
                ps.confidence = NONE
                notes.append("no count or weight found")

    if RE_RELAUNCH.search(blob):
        notes.append("RELAUNCH COPY DETECTED - check for shrinkflation")

    ps.notes = "; ".join(notes)
    return ps


def unit_price(price: float, pack: PackSize, per: float = 100.0):
    """
    Price per `per` units (default: per 100 sheets / per 100 fl oz).

    Returns None when the parse is not comparable. Callers MUST treat None as
    "cannot compare", never as zero and never as "compare on package price".
    """
    if price is None or not pack.comparable or not pack.total_units:
        return None
    return round(price / pack.total_units * per, 6)


def describe(price: float, pack: PackSize, per: float = 100.0) -> str:
    up = unit_price(price, pack, per)
    if up is None:
        return f"not comparable ({pack.confidence}: {pack.notes})"
    return f"${up:.4f} per {int(per)} {pack.unit_of_measure} [{pack.confidence}]"
