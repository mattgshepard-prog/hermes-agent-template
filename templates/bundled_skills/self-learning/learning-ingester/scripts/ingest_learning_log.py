#!/usr/bin/env python3
"""
ingest_learning_log.py -- pre-pass for the learning-ingester skill.

Reconciled to the LIVE Garry Ops schema (verified 2026-07-13):

Learning Log (data source 61d9c043-2485-4a62-bf57-777a3f294d5f):
  Lesson       (title)   one-line actionable rule
  Score        (number)  0-100
  Bucket       (select)  "behavioral" | "tool-specific" | "unsorted"
  Target Skill (relation -> Skill Registry) which skill a tool-specific lesson edits
  Rationale    (text)    why it scored what it scored
  Source       (text)    which session produced it
  Status       (select)  "pending" | "ingested" | "rejected" | "needs-approval"
  Ingested     (date)    stamped when processed
  Logged       (created_time)

Skill Registry (data source e96c1fc2-396d-4b54-ba6f-5d25b9d257e9):
  Skill Name    (title)
  Version       (text)
  Definition    (file)    the SKILL.md
  Self Revision (select)  "auto" | "propose-only" | "locked"  <- the per-skill fence
  Blast Radius  (select)  "low" | "medium" | "high"
  Surface       (select)  "claude" | "garry-cos" | "both"
  Revised By    (select)  "matt" | "skill-b"
  Last Revised  (date)
  Lessons       (relation -> Learning Log)
  Notes         (text)

This script does the deterministic Notion I/O. Judgment (the fence) lives in the
skill. All IDs and the token are read from the environment (seeded into
/data/.hermes/.env from Railway on every boot). Nothing is hardcoded.

Env vars:
  NOTION_TOKEN (or NOTION_API_KEY)   integration token
  NOTION_LEARNING_LOG_DS             data_source_id of the Learning Log
  NOTION_SKILL_REGISTRY_DS           data_source_id of the Skill Registry

Usage:
  ingest_learning_log.py                       # print pending rows as JSON (default)
  ingest_learning_log.py list
  ingest_learning_log.py set-status PAGE_ID STATUS   # pending|ingested|rejected|needs-approval
                                                     # 'ingested' also stamps Ingested = today (UTC)
  ingest_learning_log.py stamp-ingested PAGE_ID      # legacy; set-status ingested already does this
  ingest_learning_log.py get-skill PAGE_ID           # fetch one Skill Registry page's governance fields
  ingest_learning_log.py find-skill "Skill Name"     # resolve a skill name to its page id
"""

import datetime
import json
import os
import sys
import urllib.request
import urllib.error

NOTION_VERSION = "2025-09-03"
API = "https://api.notion.com/v1"


def _fail(msg):
    print(json.dumps({"error": msg}))
    sys.exit(1)


def _env(name, required=True):
    v = os.environ.get(name, "").strip()
    if required and not v:
        _fail(f"Missing required env var: {name}")
    return v


def _notion_key():
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
    if not prop:
        return ""
    t = prop.get("type")
    if t == "title":
        return "".join(x.get("plain_text", "") for x in prop.get("title", []))
    if t in ("rich_text", "text"):
        return "".join(x.get("plain_text", "") for x in prop.get("rich_text", []))
    if t == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""
    if t == "number":
        n = prop.get("number")
        return n if n is not None else None
    if t == "relation":
        return [r.get("id") for r in prop.get("relation", [])]
    return ""


def list_pending_rows():
    ds = _env("NOTION_LEARNING_LOG_DS")
    url = f"{API}/data_sources/{ds}/query"
    body = {
        "filter": {"property": "Status", "select": {"equals": "pending"}},
        "page_size": 100,
    }
    rows = []
    while True:
        resp = _request("POST", url, body)
        for page in resp.get("results", []):
            props = page.get("properties", {})
            rows.append({
                "page_id": page.get("id"),
                "lesson": _plain(props.get("Lesson")),
                "score": _plain(props.get("Score")),
                "bucket": _plain(props.get("Bucket")),
                "target_skill_ids": _plain(props.get("Target Skill")) or [],
                "rationale": _plain(props.get("Rationale")),
                "source": _plain(props.get("Source")),
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
    }, indent=2))


def set_status(page_id, status):
    valid = {"pending", "ingested", "rejected", "needs-approval"}
    if status not in valid:
        _fail(f"Invalid status '{status}'. Must be one of {sorted(valid)}")
    url = f"{API}/pages/{page_id}"
    props = {"Status": {"select": {"name": status}}}
    out = {"ok": True, "page_id": page_id, "status": status}
    if status == "ingested":
        today = datetime.datetime.now(
            datetime.timezone.utc).date().isoformat()
        props["Ingested"] = {"date": {"start": today}}
        out["ingested"] = today
    _request("PATCH", url, {"properties": props})
    print(json.dumps(out))


def stamp_ingested(page_id):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    url = f"{API}/pages/{page_id}"
    body = {"properties": {"Ingested": {"date": {"start": today}}}}
    _request("PATCH", url, body)
    print(json.dumps({"ok": True, "page_id": page_id, "ingested": today}))


def get_skill(page_id):
    """Fetch one Skill Registry page's governance fields, so the ingester can
    honor the per-skill fence (Self Revision, Blast Radius)."""
    url = f"{API}/pages/{page_id}"
    resp = _request("GET", url)
    props = resp.get("properties", {})
    print(json.dumps({
        "page_id": page_id,
        "skill_name": _plain(props.get("Skill Name")),
        "version": _plain(props.get("Version")),
        "self_revision": _plain(props.get("Self Revision")),
        "blast_radius": _plain(props.get("Blast Radius")),
        "surface": _plain(props.get("Surface")),
    }, indent=2))


def find_skill(name):
    ds = _env("NOTION_SKILL_REGISTRY_DS")
    url = f"{API}/data_sources/{ds}/query"
    body = {"filter": {"property": "Skill Name", "title": {"equals": name}}, "page_size": 10}
    resp = _request("POST", url, body)
    matches = []
    for p in resp.get("results", []):
        props = p.get("properties", {})
        matches.append({
            "page_id": p.get("id"),
            "skill_name": _plain(props.get("Skill Name")),
            "version": _plain(props.get("Version")),
            "self_revision": _plain(props.get("Self Revision")),
            "blast_radius": _plain(props.get("Blast Radius")),
        })
    print(json.dumps({"name": name, "match_count": len(matches), "matches": matches}, indent=2))


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "list"
    if cmd in ("list", ""):
        list_pending_rows()
    elif cmd == "set-status":
        if len(args) < 3:
            _fail("Usage: set-status PAGE_ID STATUS")
        set_status(args[1], args[2])
    elif cmd == "stamp-ingested":
        if len(args) < 2:
            _fail("Usage: stamp-ingested PAGE_ID")
        stamp_ingested(args[1])
    elif cmd == "get-skill":
        if len(args) < 2:
            _fail("Usage: get-skill PAGE_ID")
        get_skill(args[1])
    elif cmd == "find-skill":
        if len(args) < 2:
            _fail("Usage: find-skill \"Skill Name\"")
        find_skill(args[1])
    else:
        _fail(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
