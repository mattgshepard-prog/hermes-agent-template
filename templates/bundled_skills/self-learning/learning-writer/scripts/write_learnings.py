#!/usr/bin/env python3
"""
write_learnings.py — writer for the learning-writer skill.

Creates rows in the Notion Learning Log from a JSON array supplied on stdin.
Only handles the deterministic Notion write. Extraction, scoring, and routing
are the agent's judgment, per the skill.

Operator-facts must NOT be sent here; they go to Honcho. This script only
writes Skill Change / Voice / Positioning / Rosetta Stone / Process / Other rows.

Env vars required:
  NOTION_API_KEY          integration token
  NOTION_LEARNING_LOG_DB  database_id of the Learning Log (for page creation)

Stdin: a JSON array of objects, each:
  {
    "learning":   "one sentence",         # required
    "score":      82,                      # required, 0-100
    "type":       "Skill Change",          # required
    "target":     "garry-email-followup",  # optional (required for Skill Change)
    "body":       "self-contained instruction",  # required
    "source":     "Cowork",                # "Cowork" | "Hermes"
    "session_id": "cowork-2026-07-12"      # optional
  }

Property names must match the Learning Log schema. Adjust the MAP below if the
database uses different property names.

Usage:
  echo '[{...},{...}]' | write_learnings.py
"""

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


def _env(name):
    v = os.environ.get(name, "").strip()
    if not v:
        _fail(f"Missing required env var: {name}")
    return v


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


def _request(method, url, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code}: {detail[:400]}")


def _rich(text):
    return {"rich_text": [{"text": {"content": text[:2000]}}]} if text else {"rich_text": []}


def build_props(row):
    learning = row.get("learning", "").strip()
    if not learning:
        raise ValueError("row missing 'learning'")
    score = row.get("score")
    if score is None:
        raise ValueError("row missing 'score'")
    rtype = row.get("type", "").strip()
    if not rtype:
        raise ValueError("row missing 'type'")
    body = row.get("body", "").strip()
    if not body:
        raise ValueError("row missing 'body'")

    props = {
        "Learning": {"title": [{"text": {"content": learning[:2000]}}]},
        "Score": {"number": int(score)},
        "Type": {"select": {"name": rtype}},
        "Body": _rich(body),
        "Status": {"select": {"name": "New"}},
    }
    if row.get("target"):
        props["Target"] = _rich(row["target"].strip())
    if row.get("source"):
        props["Source"] = {"select": {"name": row["source"].strip()}}
    if row.get("session_id"):
        props["Session ID"] = _rich(row["session_id"].strip())
    return props


def main():
    raw = sys.stdin.read().strip()
    if not raw:
        _fail("No input on stdin. Pipe a JSON array of learning rows.")
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as e:
        _fail(f"Invalid JSON on stdin: {e}")
    if not isinstance(rows, list):
        _fail("Input must be a JSON array.")

    db = _env("NOTION_LEARNING_LOG_DB")
    url = f"{API}/pages"
    created, errors = [], []
    for i, row in enumerate(rows):
        try:
            props = build_props(row)
            body = {"parent": {"database_id": db}, "properties": props}
            resp = _request("POST", url, body)
            created.append(resp.get("id"))
        except Exception as e:  # noqa: BLE001
            errors.append({"index": i, "error": str(e)})

    print(json.dumps({
        "created_count": len(created),
        "created_page_ids": created,
        "error_count": len(errors),
        "errors": errors,
    }, indent=2))


if __name__ == "__main__":
    main()
