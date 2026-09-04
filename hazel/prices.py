"""
Hazel: price acquisition.

ONE interface, three adapters. The world is inconsistent; the spine absorbs
that so nothing above this layer has to know which retailer is which.

    search(slot, retailer) -> [Candidate, ...]

Rails, and why they differ:
  kroger   official API, free, client_credentials. Store-scoped pricing at a
           locationId, INCLUDING promo price, plus real UPCs. Nothing else
           gives store-level promos.
  walmart  SerpApi engine=walmart.
  amazon   SerpApi engine=amazon. Note: SerpApi returns LOGGED-OUT prices, so
           Subscribe & Save discounts are invisible and Amazon reads as more
           expensive than it actually is. Correct with per-slot adjustment.

Google Shopping was tested and REJECTED as a uniform rail on 2026-08-08: it
returned Sam's Club, Office Depot, ULINE and marketplace resellers, with no
King Soopers, no Amazon, and no Walmart first-party. Do not reintroduce it.
"""

import base64
import json
import os
import time
import urllib.error
import html as _html
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

from normalize import parse_pack, unit_price, PackSize

# Data lives next to the code. /data/hazel on Railway and /opt/data/hazel
# on Portal are both 'the directory this file sits in', so this default is
# correct on either without an environment variable. Set HAZEL_HOME to
# split code from data.
HAZEL_HOME = os.environ.get(
    "HAZEL_HOME", os.path.dirname(os.path.abspath(__file__)))

KROGER_BASE = "https://api.kroger.com/v1"
SERPAPI_BASE = "https://serpapi.com/search.json"

RETAILERS = ("kroger", "walmart", "amazon")


@dataclass
class Candidate:
    retailer: str
    product_key: str
    title: str
    price: Optional[float]
    brand: Optional[str] = None
    regular_price: Optional[float] = None
    promo_price: Optional[float] = None
    size_hint: str = ""
    url: Optional[str] = None
    pack: Optional[PackSize] = None
    unit_price: Optional[float] = None
    in_stock: Optional[bool] = None   # None = retailer gave no signal
    stock_note: str = ""
    raw: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "retailer": self.retailer, "product_key": self.product_key,
            "title": self.title, "price": self.price, "brand": self.brand,
            "regular_price": self.regular_price, "promo_price": self.promo_price,
            "unit_price": self.unit_price,
            "confidence": self.pack.confidence if self.pack else "none",
            "total_units": self.pack.total_units if self.pack else None,
            "unit_of_measure": self.pack.unit_of_measure if self.pack else None,
            "url": self.url,
            "in_stock": self.in_stock,
            "stock_note": self.stock_note,
        }


class PriceError(Exception):
    pass


def _get_json(url, headers=None, data=None, timeout=45):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        raise PriceError(f"HTTP {e.code}: {body}") from None
    except Exception as e:
        raise PriceError(str(e)[:200]) from None


# ---------------------------------------------------------------------------
# Kroger
# ---------------------------------------------------------------------------

_kroger_token = {"value": None, "expires": 0}


def kroger_token():
    """Cached client_credentials token. Kroger expiry is 1800s."""
    if _kroger_token["value"] and time.time() < _kroger_token["expires"] - 60:
        return _kroger_token["value"]
    cid = os.environ.get("KROGER_CLIENT_ID")
    sec = os.environ.get("KROGER_CLIENT_SECRET")
    if not cid or not sec:
        raise PriceError("KROGER_CLIENT_ID / KROGER_CLIENT_SECRET not set")
    basic = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials",
                                   "scope": "product.compact"}).encode()
    res = _get_json(f"{KROGER_BASE}/connect/oauth2/token", data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded",
                             "Authorization": "Basic " + basic})
    _kroger_token["value"] = res["access_token"]
    _kroger_token["expires"] = time.time() + int(res.get("expires_in", 1800))
    return _kroger_token["value"]


def kroger_location_id(zip_code=None, cache_path=None):
    """
    Resolve and cache the nearest store. Kroger returns NO price without a
    locationId, so this is required, not optional.
    """
    zip_code = zip_code or os.environ.get("HOUSEHOLD_ZIP", "80020")
    cache_path = cache_path or os.environ.get(
        "HAZEL_LOCATION_CACHE", os.path.join(HAZEL_HOME, "location.json"))
    if os.path.exists(cache_path):
        try:
            c = json.load(open(cache_path))
            if c.get("zip") == zip_code and c.get("location_id"):
                return c["location_id"]
        except Exception:
            pass
    tok = kroger_token()
    q = urllib.parse.urlencode({"filter.zipCode.near": zip_code, "filter.limit": 1})
    res = _get_json(f"{KROGER_BASE}/locations?{q}",
                    headers={"Authorization": "Bearer " + tok,
                             "Accept": "application/json"})
    data = res.get("data") or []
    if not data:
        raise PriceError(f"no Kroger location near {zip_code}")
    loc = data[0]
    out = {"zip": zip_code, "location_id": loc["locationId"],
           "name": loc.get("name"), "address": loc.get("address", {})}
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        json.dump(out, open(cache_path, "w"), indent=2)
    except Exception:
        pass
    return out["location_id"]


def search_kroger(term, limit=12, location_id=None) -> List[Candidate]:
    tok = kroger_token()
    loc = location_id or kroger_location_id()
    q = urllib.parse.urlencode({"filter.term": term,
                                "filter.locationId": loc,
                                "filter.limit": limit})
    res = _get_json(f"{KROGER_BASE}/products?{q}",
                    headers={"Authorization": "Bearer " + tok,
                             "Accept": "application/json"})
    out = []
    for p in res.get("data", []):
        items = p.get("items") or [{}]
        it = items[0]
        pr = it.get("price") or {}
        reg = pr.get("regular")
        promo = pr.get("promo")
        # promo of 0 means "no promo", not "free".
        promo = promo if promo else None
        effective = promo if promo else reg
        if effective is None:
            continue
        inv = (it.get("inventory") or {}).get("stockLevel")
        ful = it.get("fulfillment") or {}
        # TEMPORARILY_OUT_OF_STOCK is the only definite negative Kroger
        # gives. HIGH/LOW are both buyable. Absent means unknown.
        k_stock = None if not inv else (inv.upper() != "TEMPORARILY_OUT_OF_STOCK")
        if k_stock is not False and ful and not ful.get("curbside", True):
            k_stock = False
            inv = (inv or "") + " (not curbside eligible)"
        out.append(Candidate(
            retailer="kroger",
            in_stock=k_stock,
            stock_note=str(inv or ""),
            product_key=p.get("upc") or p.get("productId") or "",
            title=_html.unescape(p.get("description") or ""),
            price=float(effective),
            brand=p.get("brand"),
            regular_price=float(reg) if reg is not None else None,
            promo_price=float(promo) if promo is not None else None,
            size_hint=it.get("size") or "",
            raw={"categories": p.get("categories")},
        ))
    return out


# ---------------------------------------------------------------------------
# SerpApi
# ---------------------------------------------------------------------------

def _serp(params):
    key = os.environ.get("SERPAPI_API_KEY")
    if not key:
        raise PriceError("SERPAPI_API_KEY not set")
    p = dict(params)
    p["api_key"] = key
    res = _get_json(SERPAPI_BASE + "?" + urllib.parse.urlencode(p))
    if res.get("error"):
        raise PriceError("serpapi: " + str(res["error"])[:200])
    return res


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        v = v.get("raw") or v.get("value") or v.get("price")
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def search_walmart(term, limit=12) -> List[Candidate]:
    res = _serp({"engine": "walmart", "query": term})
    out = []
    for r in (res.get("organic_results") or [])[:limit]:
        offer = r.get("primary_offer") or {}
        price = _num(offer.get("offer_price")) or _num(r.get("price"))
        if price is None:
            continue
        oos = r.get("out_of_stock")
        out.append(Candidate(
            retailer="walmart",
            in_stock=(not oos) if oos is not None else None,
            stock_note="out of stock" if oos else "",
            product_key=str(r.get("us_item_id") or r.get("product_id") or r.get("item_id") or ""),
            title=_html.unescape(r.get("title") or ""),
            price=price,
            brand=r.get("seller_name") or r.get("brand"),
            url=r.get("product_page_url") or r.get("link"),
        ))
    return out


def search_amazon(term, limit=12) -> List[Candidate]:
    res = _serp({"engine": "amazon", "k": term})
    out = []
    for r in (res.get("organic_results") or [])[:limit]:
        price = _num(r.get("price")) or _num(r.get("extracted_price"))
        if price is None:
            continue
        out.append(Candidate(
            retailer="amazon",
            stock_note="amazon exposes no stock field",
            product_key=str(r.get("asin") or ""),
            title=_html.unescape(r.get("title") or ""),
            price=price,
            brand=r.get("brand"),
            url=r.get("link"),
        ))
    return out


# ---------------------------------------------------------------------------
# Uniform interface
# ---------------------------------------------------------------------------

ADAPTERS = {
    "kroger": search_kroger,
    "walmart": search_walmart,
    "amazon": search_amazon,
}


def search(term, retailer, limit=12, category=None, uom=None) -> List[Candidate]:
    """One call shape for every retailer. Normalization applied here, once."""
    fn = ADAPTERS.get(retailer)
    if not fn:
        raise PriceError(f"unknown retailer {retailer!r}")
    cands = fn(term, limit=limit)
    for c in cands:
        c.pack = parse_pack(c.title, c.size_hint, category=category, uom=uom)
        c.unit_price = unit_price(c.price, c.pack)
    return cands


def search_all(term, retailers=RETAILERS, limit=8, category=None, uom=None):
    """
    Query every rail. A single retailer failing must NOT take the run down --
    a partial comparison is useful, a crashed cycle is not.
    """
    results, errors = [], {}
    for r in retailers:
        try:
            results.extend(search(term, r, limit=limit, category=category,
                                  uom=uom))
        except PriceError as e:
            errors[r] = str(e)
    return results, errors


def rank(candidates, comparable_only=True):
    """
    Cheapest per USE UNIT, not per package.

    Non-comparable parses are excluded rather than ranked on package price.
    Ranking a LOW-confidence item alongside HIGH ones silently compares a
    12-pack against a 2-pack and recommends the wrong thing.
    """
    ok = [c for c in candidates if c.unit_price is not None]
    skipped = [c for c in candidates if c.unit_price is None]
    ok.sort(key=lambda c: c.unit_price)
    return (ok, skipped) if comparable_only else (ok + skipped, [])
