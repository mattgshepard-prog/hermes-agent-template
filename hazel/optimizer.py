"""
Hazel: basket assignment and landed-cost optimization.

Deciding how to split a basket across retailers under free-shipping thresholds
is CONSTRAINED ARITHMETIC. It is done here, in Python, and never by the model.
A model asked to do this will hallucinate a threshold, and a hallucinated
threshold is a wrong purchase.

Landed cost, not item price, is the objective. Winning an item by $2 and
opening a third order to do it loses money.
"""

from dataclasses import dataclass, field
from itertools import product as iterproduct
from typing import Dict, List, Optional


@dataclass
class RetailerTerms:
    name: str
    free_ship_threshold: Optional[float] = None  # None = never free
    delivery_fee: float = 0.0
    order_minimum: float = 0.0        # below this, cannot order at all
    membership: bool = False          # Prime / Walmart+ / Boost
    tax_rate: float = 0.0             # non-food consumables are taxable in CO


@dataclass
class LineOption:
    slot_id: str
    retailer: str
    product_key: str
    title: str
    price: float
    unit_price: Optional[float] = None
    taxable: bool = True


@dataclass
class Assignment:
    picks: Dict[str, LineOption] = field(default_factory=dict)  # slot -> option
    per_retailer: Dict[str, float] = field(default_factory=dict)
    shipping: Dict[str, float] = field(default_factory=dict)
    tax: Dict[str, float] = field(default_factory=dict)
    blocked: List[str] = field(default_factory=list)
    total: float = 0.0

    @property
    def retailers_used(self):
        return sorted(r for r, v in self.per_retailer.items() if v > 0)


def landed_cost(picks: Dict[str, LineOption],
                terms: Dict[str, RetailerTerms]):
    """Subtotal + tax + shipping per retailer, with order minimums enforced."""
    sub, tax = {}, {}
    for opt in picks.values():
        t = terms.get(opt.retailer) or RetailerTerms(opt.retailer)
        sub[opt.retailer] = sub.get(opt.retailer, 0.0) + opt.price
        if opt.taxable and t.tax_rate:
            tax[opt.retailer] = tax.get(opt.retailer, 0.0) + opt.price * t.tax_rate

    ship, blocked = {}, []
    for r, amount in sub.items():
        t = terms.get(r) or RetailerTerms(r)
        if amount < t.order_minimum:
            blocked.append(r)
        if t.free_ship_threshold is not None and amount >= t.free_ship_threshold:
            ship[r] = 0.0
        else:
            ship[r] = t.delivery_fee

    total = sum(sub.values()) + sum(tax.values()) + sum(ship.values())
    a = Assignment(picks=dict(picks), per_retailer=sub, shipping=ship,
                   tax=tax, blocked=blocked, total=round(total, 2))
    return a


def optimize(options: Dict[str, List[LineOption]],
             terms: Dict[str, RetailerTerms],
             min_savings: float = 1.50,
             exact_max_combinations: int = 20000) -> Assignment:
    """
    Choose one option per slot to minimize LANDED cost.

    Exact search when the space is small enough (it usually is: a weekly basket
    is tens of slots across three retailers, and most slots have one obvious
    winner). Falls back to greedy + a threshold top-up pass, which lands within
    pennies at this size.

    min_savings is the floor for opening an ADDITIONAL retailer. Splitting an
    order to save $0.40 is a loss once you count the second delivery.
    """
    slots = [s for s, opts in options.items() if opts]
    if not slots:
        return Assignment()

    space = 1
    for s in slots:
        space *= len(options[s])
        if space > exact_max_combinations:
            break

    if space <= exact_max_combinations:
        best = None
        for combo in iterproduct(*(options[s] for s in slots)):
            picks = {s: o for s, o in zip(slots, combo)}
            a = landed_cost(picks, terms)
            if a.blocked:
                continue
            if best is None or a.total < best.total:
                best = a
        if best is None:
            best = landed_cost(
                {s: min(options[s], key=lambda o: o.price) for s in slots}, terms)
        return _consolidate(best, options, terms, min_savings)

    # Greedy: cheapest per slot, then consolidate.
    picks = {s: min(options[s], key=lambda o: o.price) for s in slots}
    return _consolidate(landed_cost(picks, terms), options, terms, min_savings)


def _consolidate(a: Assignment, options, terms, min_savings: float) -> Assignment:
    """
    Try collapsing each retailer into the others. Keeps the split ONLY when it
    beats consolidation by more than min_savings. This is what stops Hazel
    opening a third order to win forty cents on paper towels.
    """
    improved = True
    while improved:
        improved = False
        for drop in list(a.retailers_used):
            if len(a.retailers_used) <= 1:
                break
            moved = {}
            feasible = True
            for slot, opt in a.picks.items():
                if opt.retailer != drop:
                    moved[slot] = opt
                    continue
                alts = [o for o in options[slot] if o.retailer != drop]
                if not alts:
                    feasible = False
                    break
                moved[slot] = min(alts, key=lambda o: o.price)
            if not feasible:
                continue
            cand = landed_cost(moved, terms)
            if cand.blocked:
                continue
            # Keep the split only if it wins by more than the floor.
            if cand.total <= a.total + min_savings:
                a = cand
                improved = True
                break
    return a


def top_up_suggestion(a: Assignment, terms: Dict[str, RetailerTerms]):
    """
    Where a small addition would cross a free-shipping threshold and save more
    than it costs. Returns [(retailer, needed, fee_saved)].
    """
    out = []
    for r, amount in a.per_retailer.items():
        t = terms.get(r) or RetailerTerms(r)
        if t.free_ship_threshold is None or amount >= t.free_ship_threshold:
            continue
        need = round(t.free_ship_threshold - amount, 2)
        fee = a.shipping.get(r, t.delivery_fee)
        if fee > 0 and need < fee:
            out.append((r, need, fee))
    return sorted(out, key=lambda x: x[1])


def explain(a: Assignment, terms: Dict[str, RetailerTerms]) -> str:
    lines = []
    for r in a.retailers_used:
        t = terms.get(r) or RetailerTerms(r)
        sub = a.per_retailer[r]
        ship = a.shipping.get(r, 0.0)
        tax = a.tax.get(r, 0.0)
        items = [o for o in a.picks.values() if o.retailer == r]
        note = "free shipping" if ship == 0 and t.free_ship_threshold else \
               (f"${ship:.2f} delivery" if ship else "no fee")
        lines.append(f"{r}: {len(items)} items, ${sub:.2f}"
                     + (f" + ${tax:.2f} tax" if tax else "")
                     + f", {note}")
    lines.append(f"TOTAL LANDED: ${a.total:.2f}")
    for r, need, fee in top_up_suggestion(a, terms):
        lines.append(f"  tip: ${need:.2f} more at {r} saves the ${fee:.2f} fee")
    return "\n".join(lines)
