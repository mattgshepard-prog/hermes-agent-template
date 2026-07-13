---
name: log-this-now
description: "On-demand learning capture: when Matt says 'log this now', take the lesson from the current conversation, score and bucket it, write it to the Notion Learning Log, and immediately run it through the ingester's fence so a passing tool-specific edit is live in the same session instead of waiting for the nightly cron."
version: 2.0.0
author: Matt Shepard
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  env_vars: [NOTION_TOKEN, NOTION_LEARNING_LOG_DB, NOTION_LEARNING_LOG_DS, NOTION_SKILL_REGISTRY_DS]
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

Matt corrects Garry, or reveals a preference, and wants it to stick right now, not tomorrow morning. This captures the single lesson from the current conversation, writes it, and runs it through the same fence in-session, so a passing change is live immediately.

This is the on-demand version of the nightly loop, scoped to exactly one lesson. It reuses the writer and ingester machinery and the same two-layer fence.

## When it fires

Matt says "log this now" (or a close variant). The lesson is whatever was just discussed or corrected. If it is not obvious what the single lesson is, ask Matt one short question to confirm before writing. Never guess and write a wrong lesson; a wrong applied lesson is worse than none.

## Steps

**Step 1 - Distill and bucket.** From the current conversation, write:
- `lesson`: one self-contained sentence, the actionable rule
- `bucket`: `behavioral` (about Matt, goes to Honcho), `tool-specific` (edits one named skill), or `unsorted`
- `target_skill_id` (tool-specific only): resolve with `ingest_learning_log.py find-skill "Exact Skill Name"`
- `rationale`: why it scored what it scored
- `score`: honest 0-100

**Score honestly even under time pressure.** "Do it now" is not automatically 100. If intent is ambiguous, score 69 so it flags rather than auto-applies, same as the nightly loop.

**Step 2 - Write the row.**
`echo '[{...}]' | HERMES_HOME=/data/.hermes python /data/.hermes/skills/self-learning/learning-writer/scripts/write_learnings.py`
Capture the created page id. (behavioral lessons go to Honcho instead, not the Learning Log.)

**Step 3 - Apply immediately, through the full fence.** Run the ingester's fence on this one row in-session, following `../learning-ingester/SKILL.md`:
- Resolve the `Target Skill` and read its `Self Revision` and `Blast Radius` via `ingest_learning_log.py get-skill TARGET_PAGE_ID`.
- `tool-specific`, `Self Revision = auto`, `Blast Radius` not high, score 70+: apply the edit to the skill's Definition, bump Version, set Revised By = skill-b and Last Revised, set row `Status = ingested`, stamp Ingested. Tell Matt in one line what changed.
- `propose-only`, `locked`, `Blast Radius = high`, score 40-69, unsorted, or no target: set `Status = needs-approval`, tell Matt it is captured and waiting on his approval. Do NOT apply.
- score under 40: `Status = rejected`, tell Matt it was too weak to keep.

Set status via `ingest_learning_log.py set-status PAGE_ID STATUS` and `stamp-ingested PAGE_ID` when ingested.

**Step 4 - Confirm in one line.** Tell Matt exactly what happened: ingested (skill and new version), waiting on approval (and why: propose-only, locked, high blast radius, or score), or rejected. No filler.

## Why this reuses the loop rather than shortcutting it

The lesson still lands in the Learning Log with a status, so the nightly digest and audit trail stay complete. "Log this now" skips the wait, not the fence. The same governance protects the on-demand path: ambiguity flags, locked and propose-only skills never auto-edit, high blast radius always gets human eyes, and every applied edit has a changelog line that is its own undo.

## Hard rules

- Never bypass the fence because Matt is in a hurry. Speed changes the timing, not the safety.
- No em dashes in the lesson or the confirmation.
- One question maximum if you need to confirm the lesson; otherwise proceed.
