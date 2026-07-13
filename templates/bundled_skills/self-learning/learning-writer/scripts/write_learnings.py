#!/usr/bin/env python3
"""
write_learnings.py -- writer for the learning-writer skill.

Reconciled to the LIVE Learning Log schema (verified 2026-07-13):
  Lesson       (title)   required
  Score        (number)  required, 0-100
  Bucket       (select)  "behavioral" | "tool-specific" | "unsorted"
  Target Skill (relation -> Skill Registry) required for tool-specific rows
  Rationale    (text)    why it scored what it scored
  Source       (text)    session id
  Status       (select)  always "pending" on write

Creates rows in the Learning Log from a JSON array on stdin. Only the
deterministic Notion write lives here; extraction, scoring, and bucket routing
are the agent's judgment per the skill.

behavioral lessons routed to Honcho do NOT come here. This writes only rows that
belong in the Learning Log (tool-specific edits, or unsorted needing triage).

Env vars:
  NOTION_TOKEN (or NOTION_API_KEY)   integration token
  NOTION_LEARNING_LOG_DB             database_id of the Learning Log

Stdin: JSON array of objects, each:
  {
    "lesson":            "one-line actionable rule",   # required
    "score":             82,                            # required 0-100
    "bucket":            "tool-specific",               # required
    "target_skill_id":   "<skill registry page id>",   # required if tool-specific
    "rationale":         "why it scored this",          # optional
    "source":            "cowork-2026-07-12"            # optional
  }

Usage:
  echo '[{...}]' | write_learnings.py
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


def _text(s):
    return {"rich_text": [{"text": {"content": s[:2000]}}]} if s else {"rich_text": []}


def build_props(row):
    lesson = (row.get("lesson") or "").strip()
    if not lesson:
        raise ValueError("row missing 'lesson'")
    score = row.get("score")
    if score is None:
        raise ValueError("row missing 'score'")
    bucket = (row.get("bucket") or "").strip()
    if bucket not in ("behavioral", "tool-specific", "unsorted"):
        raise ValueError(f"invalid bucket '{bucket}'")

    props = {
        "Lesson": {"title": [{"text": {"content": lesson[:2000]}}]},
        "Score": {"number": int(score)},
        "Bucket": {"select": {"name": bucket}},
        "Status": {"select": {"name": "pending"}},
    }
    if row.get("rationale"):
        props["Rationale"] = _text(row["rationale"].strip())
    if row.get("source"):
        props["Source"] = _text(row["source"].strip())
    if bucket == "tool-specific":
        tid = (row.get("target_skill_id") or "").strip()
        if not tid:
            raise ValueError("tool-specific row missing 'target_skill_id'")
        props["Target Skill"] = {"relation": [{"id": tid}]}
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
