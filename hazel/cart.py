"""
Hazel: Kroger cart write.

The cycle ends at a staged basket. This module is the APPROVE step that turns
the Kroger portion of that basket into a real cart under Matt's Kroger account.

WHAT THIS DOES NOT DO
    It does not check out. It does not pay. It does not select a fulfilment
    window. The Kroger Public API has no order-submit endpoint, so this is
    enforced by the API surface rather than by instruction. Payment stays with
    the human, permanently.

    Amazon and Walmart lines are NOT touched. There is no sanctioned cart write
    for either. They are reported as deferred with counts and dollars, never
    silently dropped: a basket that half-executes while reporting success is
    worse than one that fails.

TOKEN MODEL, and why it is not an env var
    Price lookups use grant_type=client_credentials -- an APPLICATION token
    that can never touch a cart, because a cart belongs to a person.

    Cart writes need a USER token, minted once via authorize_kroger.py and kept
    alive by a refresh token.

    VERIFIED 2026-08-18 AGAINST THE LIVE API: Kroger ROTATES the refresh token
    on every use. One refresh returned a different token (c18e39e9 -> fbe13270).
    Two consequences, both load-bearing:

      1. The new token MUST be persisted before the access token is returned,
         or the integration works today and dies silently later.
      2. The live token CANNOT live in a Railway variable. start.sh's boot-seed
         loop rewrites the volume .env from Railway on every boot, which would
         stamp a dead token over the live one on every redeploy. The symptom
         would point at the deploy, not the token.

    So: the volume file is the source of truth. The env var is a one-time
    bootstrap seed, read only when the file does not exist, and named
    KROGER_REFRESH_TOKEN_SEED so its staleness is visible rather than implied.

MODALITY
    PICKUP by default. DELIVERY only when a human says so at approval time.
    Never inferred, never remembered from last run.
"""

import base64
import glob
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# Data lives next to the code. /data/hazel on Railway and /opt/data/hazel
# on Portal are both 'the directory this file sits in', so this default is
# correct on either without an environment variable. Set HAZEL_HOME to
# split code from data.
HAZEL_HOME = os.environ.get(
    "HAZEL_HOME", os.path.dirname(os.path.abspath(__file__)))

KROGER_BASE = "https://api.kroger.com/v1"
TOKEN_FILE = os.environ.get("HAZEL_KROGER_TOKEN", os.path.join(HAZEL_HOME, "kroger_token.json"))
STAGE_DIR = os.environ.get("HAZEL_STAGE", os.path.join(HAZEL_HOME, "staged"))

MODALITIES = ("PICKUP", "DELIVERY")
DEFAULT_MODALITY = "PICKUP"

# Kroger UPCs from search_kroger() are p.get("upc"), 13 digits. A shorter value
# means the productId fallback was used, which /cart/add will not accept.
UPC_LEN = 13


class CartError(Exception):
    """Anything that failed but is worth retrying."""


class CartAuthError(CartError):
    """
    The token is gone or refused. Distinct type because the operator action is
    'run authorize_kroger.py again', not 'retry'. Retrying cannot fix this and
    a retry loop would just burn the rate limit.
    """


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

_access = {"value": None, "expires": 0}


def _read_refresh():
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE) as f:
                tok = json.load(f).get("refresh_token")
            if tok:
                return tok, "file"
        except Exception:
            pass  # fall through to the seed rather than dying on a bad file
    seed = os.environ.get("KROGER_REFRESH_TOKEN_SEED")
    if seed:
        return seed, "seed"
    raise CartAuthError(
        f"no refresh token at {TOKEN_FILE} and no KROGER_REFRESH_TOKEN_SEED. "
        "Run authorize_kroger.py on a machine with a browser.")


def _write_refresh(token):
    """
    Atomic. A half-written token file means the browser ceremony has to be
    repeated, so the rename is not optional.
    """
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    tmp = TOKEN_FILE + ".tmp"
    payload = {"refresh_token": token, "rotated_at": int(time.time())}
    with open(tmp, "w") as f:
        json.dump(payload, f)
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, TOKEN_FILE)


def _post_form(url, form, basic):
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic " + basic,
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        if e.code in (400, 401):
            raise CartAuthError(f"HTTP {e.code}: {body}") from None
        raise CartError(f"HTTP {e.code}: {body}") from None
    except Exception as e:
        raise CartError(str(e)[:200]) from None


def kroger_user_token():
    """
    A user-scoped access token. Kroger expiry is 1800s; refresh at 60s margin.

    The rotation write-back happens BEFORE the access token is returned. If the
    process dies between the API call and the write, the new token is lost and
    the browser ceremony must be repeated -- so the write is first.
    """
    if _access["value"] and time.time() < _access["expires"] - 60:
        return _access["value"]

    cid = os.environ.get("KROGER_CLIENT_ID")
    sec = os.environ.get("KROGER_CLIENT_SECRET")
    if not cid or not sec:
        raise CartAuthError("KROGER_CLIENT_ID / KROGER_CLIENT_SECRET not set")

    refresh, source = _read_refresh()
    basic = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    res = _post_form(f"{KROGER_BASE}/connect/oauth2/token", {
        "grant_type": "refresh_token", "refresh_token": refresh}, basic)

    new_refresh = res.get("refresh_token")
    if new_refresh and new_refresh != refresh:
        _write_refresh(new_refresh)
    elif source == "seed":
        # First run off the env seed. Take ownership of the file immediately so
        # the seed is never consulted again.
        _write_refresh(refresh)

    access = res.get("access_token")
    if not access:
        raise CartAuthError("no access_token in refresh response")
    _access["value"] = access
    _access["expires"] = time.time() + int(res.get("expires_in", 1800))
    return access


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------

def resolve_modality(explicit=None):
    """
    PICKUP unless a human said otherwise for THIS approval. An unrecognised
    value is refused rather than coerced -- silently shipping groceries to the
    house when pickup was meant is not a recoverable error.
    """
    val = (explicit or os.environ.get("HAZEL_KROGER_MODALITY")
           or DEFAULT_MODALITY)
    val = str(val).strip().upper()
    if val not in MODALITIES:
        raise CartError(f"modality must be one of {MODALITIES}, got {val!r}")
    return val


def valid_upc(key):
    key = str(key or "")
    return key.isdigit() and len(key) == UPC_LEN


def add_to_kroger_cart(items, modality=DEFAULT_MODALITY, token=None):
    """
    PUT /cart/add. Returns 204 No Content on success.

    A 204 proves Kroger ACCEPTED the request. It does not prove the cart holds
    what you think, and the Public API has no GET /cart to check against. That
    gap is reported honestly upstream rather than papered over here.
    """
    if not items:
        return {"submitted": 0, "http_status": None}
    body = json.dumps({"items": [
        {"upc": i["upc"], "quantity": int(i.get("quantity", 1)),
         "modality": modality} for i in items]}).encode()

    req = urllib.request.Request(f"{KROGER_BASE}/cart/add", data=body,
                                 method="PUT")
    req.add_header("Authorization", "Bearer " + (token or kroger_user_token()))
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return {"submitted": len(items), "http_status": r.status}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        if e.code in (401, 403):
            raise CartAuthError(f"HTTP {e.code}: {detail}") from None
        raise CartError(f"HTTP {e.code}: {detail}") from None
    except Exception as e:
        raise CartError(str(e)[:200]) from None


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------

def latest_basket():
    paths = sorted(glob.glob(os.path.join(STAGE_DIR, "basket_*.json")))
    if not paths:
        raise CartError(f"no staged basket in {STAGE_DIR}")
    return paths[-1]


def approve(basket_path=None, modality=None, dry_run=False):
    """
    Turn the Kroger lines of a staged basket into a real cart.

    The status fence is the idempotency mechanism: only AWAITING_APPROVAL is
    eligible, and success rewrites the status. Double-approval is therefore
    structurally impossible rather than merely unlikely.
    """
    path = basket_path or latest_basket()
    with open(path) as f:
        basket = json.load(f)

    status = basket.get("status")
    if status != "AWAITING_APPROVAL":
        raise CartError(
            f"basket status is {status!r}, not AWAITING_APPROVAL. "
            "Already approved, or not a staged basket. Run a fresh cycle.")

    mod = resolve_modality(modality)
    lines = basket.get("lines") or []

    kroger, deferred, unusable = [], [], []
    for L in lines:
        if L.get("retailer") != "kroger":
            deferred.append(L)
            continue
        if not valid_upc(L.get("product_key")):
            unusable.append(L)
            continue
        kroger.append({"upc": str(L["product_key"]),
                       "quantity": int(L.get("quantity", 1)),
                       "title": L.get("title", ""),
                       "price": L.get("price")})

    result = {
        "basket": path,
        "modality": mod,
        "kroger_lines": len(kroger),
        "kroger_total": round(sum(k["price"] or 0 for k in kroger), 2),
        "deferred": [{"retailer": L.get("retailer"), "title": L.get("title"),
                      "price": L.get("price")} for L in deferred],
        "deferred_total": round(sum(L.get("price") or 0 for L in deferred), 2),
        "unusable": [{"title": L.get("title"),
                      "product_key": L.get("product_key")} for L in unusable],
        "dry_run": dry_run,
    }

    if dry_run:
        result["submitted"] = False
        return result
    if not kroger:
        result["submitted"] = False
        result["note"] = "no Kroger lines to submit"
        return result

    res = add_to_kroger_cart(kroger, modality=mod)
    result["submitted"] = True
    result["http_status"] = res["http_status"]
    result["items"] = [{"upc": k["upc"], "quantity": k["quantity"],
                        "title": k["title"]} for k in kroger]

    basket["status"] = "CART_STAGED_KROGER"
    basket["cart"] = {
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        "modality": mod,
        "http_status": res["http_status"],
        "items": result["items"],
        # Deliberate wording. Kroger accepted the request; that is all we know.
        "verification": "submitted_not_independently_confirmed",
    }
    with open(path, "w") as f:
        json.dump(basket, f, indent=2, default=str)
    return result


def summarize_approval(r) -> str:
    """Telegram-shaped. Matches cycle.summarize() conventions."""
    out = []
    if r.get("dry_run"):
        out.append(f"DRY RUN - nothing submitted ({r['modality']})")
    elif r.get("submitted"):
        out.append(f"KROGER CART STAGED - {r['modality']}")
    else:
        out.append("NOTHING SUBMITTED")
        if r.get("note"):
            out.append(f"  {r['note']}")

    if r.get("kroger_lines"):
        out.append(f"  {r['kroger_lines']} items, ${r['kroger_total']:.2f}")
    if r.get("submitted"):
        # Never say "in your cart". We know Kroger returned 204.
        out.append("  Submitted to Kroger. Open the King Soopers app to "
                   "confirm and pay.")

    if r.get("deferred"):
        by = {}
        for d in r["deferred"]:
            by[d["retailer"]] = by.get(d["retailer"], 0) + 1
        parts = ", ".join(f"{v} {k}" for k, v in sorted(by.items()))
        out.append("")
        out.append(f"NOT ORDERED: {parts} (${r['deferred_total']:.2f})")
        out.append("  No sanctioned cart write for these. Order them yourself.")

    if r.get("unusable"):
        out.append("")
        out.append(f"SKIPPED {len(r['unusable'])} Kroger lines with no valid UPC:")
        for u in r["unusable"][:5]:
            out.append(f"  {u['title'][:50]} (key={u['product_key']!r})")

    return "\n".join(out)
