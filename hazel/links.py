"""One-click cart links for every rail.

Kroger has a sanctioned cart write (see cart.py approve). Amazon and Walmart
do not, but both accept a URL that fills a cart in one click:

  Amazon   /gp/aws/cart/add.html?AssociateTag=..&ASIN.n=..&Quantity.n=..
           The tag is REQUIRED. Without it Amazon silently drops the caller
           at an empty cart. It is not validated, so a placeholder works and
           nothing is attributed to any Associates account.
  Walmart  /affil/cart/addToCart?items=ID|QTY,ID|QTY

Neither reports failure. A link that adds nothing looks identical to one that
works, so the caller must eyeball the cart. That is stated in the output
rather than assumed.
"""
import json
import os

AMAZON_TAG = os.environ.get("HAZEL_AMAZON_TAG", "hzltest-20")
AMAZON_BATCH = 25          # keep URLs short enough for any client
KROGER_CART = "https://www.kingsoopers.com/cart"


def _chunk(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def amazon_urls(items):
    """items: [(asin, qty)] -> list of add-to-cart URLs."""
    urls = []
    for batch in _chunk(items, AMAZON_BATCH):
        parts = [f"AssociateTag={AMAZON_TAG}"]
        for i, (key, qty) in enumerate(batch, start=1):
            parts.append(f"ASIN.{i}={key}")
            parts.append(f"Quantity.{i}={int(qty)}")
        urls.append("https://www.amazon.com/gp/aws/cart/add.html?"
                    + "&".join(parts))
    return urls


def walmart_urls(items):
    """items: [(us_item_id, qty)] -> list of add-to-cart URLs."""
    spec = ",".join(f"{k}|{int(q)}" for k, q in items)
    return ["https://affil.walmart.com/cart/addToCart?items=" + spec] if spec else []


def build(basket_path):
    """Read a staged basket, return per-retailer links and a text block."""
    with open(basket_path) as fh:
        basket = json.load(fh)

    lines = basket.get("lines") or []
    by_retailer = {}
    for ln in lines:
        r = ln.get("retailer")
        key = ln.get("product_key")
        if not key:
            continue
        qty = int(ln.get("quantity") or 1)
        by_retailer.setdefault(r, []).append((key, qty))

    totals = basket.get("totals") or {}
    sub = totals.get("subtotal_by_retailer") or {}

    out = {
        "basket": basket_path,
        "status": basket.get("status"),
        "amazon": {"count": len(by_retailer.get("amazon", [])),
                   "subtotal": sub.get("amazon"),
                   "urls": amazon_urls(by_retailer.get("amazon", []))},
        "walmart": {"count": len(by_retailer.get("walmart", [])),
                    "subtotal": sub.get("walmart"),
                    "urls": walmart_urls(by_retailer.get("walmart", []))},
        "kroger": {"count": len(by_retailer.get("kroger", [])),
                   "subtotal": sub.get("kroger"),
                   "urls": [KROGER_CART],
                   "note": "submitted by `hazel.py approve`; link opens the cart"},
    }
    out["text"] = render(out)
    return out


def render(d):
    """Telegram-friendly block. Short, no markdown that mangles URLs."""
    rows = []
    for r in ("kroger", "walmart", "amazon"):
        blk = d[r]
        if not blk["count"]:
            continue
        sub = f"${blk['subtotal']:.2f}" if blk.get("subtotal") else ""
        rows.append(f"{r.upper()}  {blk['count']} items  {sub}".rstrip())
        for i, u in enumerate(blk["urls"], start=1):
            label = f"  link {i}/{len(blk['urls'])}: " if len(blk["urls"]) > 1 else "  "
            rows.append(label + u)
    rows.append("")
    rows.append("Check each cart before paying. None of these three report a "
                "failed add, so a wrong count is only visible in the cart.")
    return "\n".join(rows)
