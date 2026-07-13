---
name: log-this-now
description: "On-demand learning capture: when Matt says 'log this now', take the lesson from the current conversation, score it, write it to the Notion Learning Log, and immediately run it through the ingester's apply path so the knowledge is live in the same session instead of waiting for the nightly cron."
version: 1.0.0
author: Matt Shepard
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  env_vars: [NOTION_API_KEY, NOTION_LEARNING_LOG_DB, NOTION_LEARNING_LOG_DS, NOTION_SKILL_REGISTRY_DS, NOTION_SKILL_REGISTRY_DB]
triggers:
  - user says "log this now"
  - user says "log that now"
  - user says "capture this lesson now"
  - user asks to save a correction immediately
tags: [self-learning, notion, on-demand, skills]
metadata:
  hermes:
    tags: [SelfLearning, Notion, OnDemand]
---

# Log This Now

Matt corrects Garry, or reveals a preference, and wants it to stick right now, not tomorrow morning. This skill captures the single lesson from the current conversation, writes it, and applies it in-session, so the change is live immediately.

This is the on-demand version of the nightly loop, scoped to exactly one lesson. It reuses the writer and ingester machinery rather than duplicating it.

## When it fires

Matt says "log this now" (or a close variant). The lesson is whatever was just discussed or corrected in the current conversation. If it is not obvious what the lesson is, ask Matt one short question to confirm the single lesson before writing. Never guess and write a wrong lesson; a wrong applied lesson is worse than none.

## Steps

**Step 1 — Distill the lesson.** From the current conversation, write:
- `learning`: one sentence, what was learned
- `type`: `Skill Change` if it is a procedural edit to a named skill; otherwise `Voice`, `Positioning`, `Process`, `Rosetta Stone`, or `Other`
- `target`: the exact skill slug (for Skill Change)
- `body`: the full, self-contained instruction, written so it can be applied without this conversation
- `score`: score it honestly, 0-100, per the writer's scoring rules

**Score honestly even under time pressure.** "Matt said do it now" is not automatically a 100. Score by the actual clarity and strength of the lesson. If the intent is ambiguous, score 69 so it flags rather than auto-applies, exactly as in the nightly loop.

**Step 2 — Write the row.** Pipe the single-row array to the writer script:
`echo '[{...}]' | HERMES_HOME=/data/.hermes python /data/.hermes/skills/self-learning/learning-writer/scripts/write_learnings.py`
Capture the created `page_id`.

**Step 3 — Apply immediately.** Run the ingester's fence on this one row, in-session, using the same rules as the nightly ingester (`../learning-ingester/SKILL.md`):
- If it passes the fence (Skill Change, score 70+, slug matches exactly one skill): apply the edit to the Skill Registry, bump version, write the changelog line, set the row `Status = Applied`, and tell Matt in one line what changed.
- If it flags (score 40-69, or any Voice/Positioning/Process/Rosetta/Other, or slug mismatch): set `Status = Flagged`, and tell Matt it is captured and waiting on his approval. Do NOT apply it.
- If it discards (score under 40): set `Status = Discarded` and tell Matt it was too weak to keep.

Use the ingester script for the status write:
`HERMES_HOME=/data/.hermes python /data/.hermes/skills/self-learning/learning-ingester/scripts/ingest_learning_log.py set-status PAGE_ID STATUS`

**Step 4 — Confirm in one line.** Tell Matt exactly what happened: applied (with the skill and new version), flagged (waiting on him), or discarded. No filler.

## Why this reuses the loop rather than shortcutting it

The lesson still lands in the Learning Log with a status, so the nightly digest and the audit trail stay complete. "Log this now" only skips the wait, not the fence. The same governance that protects the overnight auto-apply protects the on-demand one: ambiguity flags, voice never auto-applies, slug mismatch flags, and every applied edit has a changelog line that is its own undo.

## Hard rules

- Never bypass the fence because Matt is in a hurry. Speed changes the timing, not the safety.
- No em dashes in the lesson body or the confirmation.
- One question maximum if you need to confirm the lesson; otherwise proceed.
