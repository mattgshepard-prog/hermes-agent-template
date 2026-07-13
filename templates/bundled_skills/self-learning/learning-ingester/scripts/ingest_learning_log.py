#!/usr/bin/env python3
"""
ingest_learning_log.py — pre-pass for the learning-ingester skill.

Reads unprocessed rows (Status = New) from the Notion Learning Log and prints
them as JSON for Garry to process through the scoring fence. Also exposes
subcommands the agent calls to mark row status and to patch skill pages, so the
deterministic Notion I/O lives here and the judgment lives in the skill.

All IDs and the API key are read from the environment (seeded into
/data/.hermes/.env from Railway on every boot). Nothing is hardcoded.

Env vars required:
  NOTION_API_KEY            integration token (shared with both databases)
  NOTION_LEARNING_LOG_DS    data_source_id of the Learning Log (for querying)
  NOTION_SKILL_REGISTRY_DS  data_source_id of the Skill Registry (for lookup)
  NOTION_SKILL_REGISTRY_DB  database_id of the Skill Registry (for page create/patch)

Property names expected on the Learning Log (adjust here if the schema differs):
  Learning     (title)
  Score        (number)
  Type         (select: "Skill Change" | "Voice" | "Positioning" |
                "Rosetta Stone" | "Process" | "Other")
  Target       (rich_text) — skill slug for Skill Change rows
  Body         (rich_text) — self-contained instruction Hermes can act on
  Source       (select: "Cowork" | "Hermes")
  Session ID   (rich_text)
  Status       (select: "New" | "Applied" | "Flagged" | "Discarded")

Usage:
  ingest_learning_log.py                 # print New rows as JSON (default)
  ingest_learning_log.py list            # same as default
  ingest_learning_log.py set-status PAGE_ID STATUS
  ingest_learning_log.py find-skill SLUG # print matching Skill Registry page(s)
"""

import json
import os
import sys
import urllib.request
import urllib.error

NOTION_VERSION = "2025-09-03"
API = "https://api.notion.com/v1"


def _env(name, required=True):
    v = os.environ.get(name, "").strip()
    if required and not v:
        _fail(f"Missing required env var: {name}")
    return v


def _fail(msg):
    print(json.dumps({"error": msg}))
    sys.exit(1)


def _notion_key():
    # This stack seeds the Notion token as NOTION_TOKEN. Accept NOTION_API_KEY
    # as a fallback so the skill is portable to other bots.
    v = os.environ.get("NOTION_TOKEN", "").strip() or os.environ.get("NOTION_API_KEY", "").strip()
    if not v:
        _fail("Missing required env var: NOTION_TOKEN (or NOTION_API_KEY)")
    return v


def _headers():
    return {
        "Authorization": f"Bearer {_notion_key()}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(method, url, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        _fail(f"HTTP {e.code} on {method} {url}: {detail[:400]}")
    except Exception as e:  # noqa: BLE001
        _fail(f"Request failed on {method} {url}: {e}")


def _plain(prop):
    """Extract a plain string from a Notion property value, tolerant of type."""
    if not prop:
        return ""
    t = prop.get("type")
    if t == "title":
        return "".join(x.get("plain_text", "") for x in prop.get("title", []))
    if t == "rich_text":
        return "".join(x.get("plain_text", "") for x in prop.get("rich_text", []))
    if t == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""
    if t == "number":
        n = prop.get("number")
        return n if n is not None else None
    return ""


def list_new_rows():
    ds = _env("NOTION_LEARNING_LOG_DS")
    url = f"{API}/data_sources/{ds}/query"
    body = {
        "filter": {"property": "Status", "select": {"equals": "New"}},
        "page_size": 100,
    }
    rows = []
    while True:
        resp = _request("POST", url, body)
        for page in resp.get("results", []):
            props = page.get("properties", {})
            rows.append({
                "page_id": page.get("id"),
                "learning": _plain(props.get("Learning")),
                "score": _plain(props.get("Score")),
                "type": _plain(props.get("Type")),
                "target": _plain(props.get("Target")),
                "body": _plain(props.get("Body")),
                "source": _plain(props.get("Source")),
                "session_id": _plain(props.get("Session ID")),
            })
        if resp.get("has_more") and resp.get("next_cursor"):
            body["start_cursor"] = resp["next_cursor"]
        else:
            break
    print(json.dumps({
        "rows": rows,
        "count": len(rows),
        "learning_log_ds": ds,
        "skill_registry_ds": _env("NOTION_SKILL_REGISTRY_DS", required=False),
        "skill_registry_db": _env("NOTION_SKILL_REGISTRY_DB", required=False),
    }, indent=2))


def set_status(page_id, status):
    valid = {"New", "Applied", "Flagged", "Discarded"}
    if status not in valid:
        _fail(f"Invalid status '{status}'. Must be one of {sorted(valid)}")
    url = f"{API}/pages/{page_id}"
    body = {"properties": {"Status": {"select": {"name": status}}}}
    _request("PATCH", url, body)
    print(json.dumps({"ok": True, "page_id": page_id, "status": status}))


def find_skill(slug):
    ds = _env("NOTION_SKILL_REGISTRY_DS")
    url = f"{API}/data_sources/{ds}/query"
    # Match on the skill's title/slug property. Assumes a "Name" title property.
    body = {
        "filter": {"property": "Name", "title": {"equals": slug}},
        "page_size": 10,
    }
    resp = _request("POST", url, body)
    matches = [{"page_id": p.get("id")} for p in resp.get("results", [])]
    print(json.dumps({"slug": slug, "match_count": len(matches), "matches": matches}))


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "list"
    if cmd in ("list", ""):
        list_new_rows()
    elif cmd == "set-status":
        if len(args) < 3:
            _fail("Usage: set-status PAGE_ID STATUS")
        set_status(args[1], args[2])
    elif cmd == "find-skill":
        if len(args) < 2:
            _fail("Usage: find-skill SLUG")
        find_skill(args[1])
    else:
        _fail(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
