---
name: learning-writer
description: "Nightly self-learning sweep: read the day's session transcripts, extract and score learnings 0-100, and route them by destination. Operator-facts about Matt go to Honcho as conclusions. Asset changes (skill edits) become scored rows in the Notion Learning Log with Status = New for the ingester to process."
version: 1.0.0
author: Matt Shepard
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  env_vars: [NOTION_API_KEY, NOTION_LEARNING_LOG_DB, HONCHO_API_KEY]
triggers:
  - the Self-Learning Sweep cron job fires
  - user says "wrap up" to run the sweep early
  - user asks to run the nightly learning sweep
tags: [self-learning, notion, honcho, transcripts, cron, scoring]
metadata:
  hermes:
    tags: [SelfLearning, Notion, Honcho, Transcripts, Cron]
---

# Learning Writer

The writer half of the self-learning loop. Once a day, read everything that happened, distill it into scored learnings, and route each one to the right home. This never applies changes; it only produces the material the ingester acts on.

Runs primarily on the Cowork surface (where the day's work and transcripts live). Hermes can also run it to log its own learnings inline, tagged `Source = Hermes`, per the two-way loop design.

## Files

- Pre-pass script: `scripts/write_learnings.py` (in this skill) — creates Learning Log rows
- Notion access: HTTP + curl per the `notion` skill, or the script's `requests` calls
- Honcho: written via the Hermes Honcho memory provider / MCP tools, not by this script

## Environment (read from .env, never hardcode)

- `NOTION_API_KEY` — integration token, shared with the Learning Log
- `NOTION_LEARNING_LOG_DB` — database_id of the Learning Log (for creating rows)
- `HONCHO_API_KEY` — Honcho workspace key (operator-facts destination)

## How it runs

1. Gather the day's transcripts. On Cowork, read the full session transcripts for the day, including any that were compacted (read the compacted summary if the raw is unavailable). On Hermes, the agent logs its own learnings inline as it runs, so a separate sweep is not required, but the same routing applies.
2. Extract candidate learnings: corrections Matt made, friction in a skill, decisions with their reasoning, revealed preferences, and any skill that misbehaved.
3. Score and route each one (below).
4. Write skill-change learnings to the Learning Log as rows with `Status = New`. Write operator-facts to Honcho as conclusions.

If there is nothing worth recording, write nothing and exit. An empty day is fine.

## Scoring (0-100, signal strength and clarity of intent)

- A hard, explicit correction from Matt scores near 100.
- A clear preference stated once scores 70-90.
- A reasonable inference from context scores 40-69.
- A weak inference from one ambiguous moment scores under 40.

**When intent is ambiguous, score 69, not 70.** This is deliberate. 69 flags for Matt's approval in the ingester; 70 auto-applies. Ambiguity must never auto-apply. This single rule is what makes the overnight auto-edit safe.

## Routing by destination

Decide where each learning belongs before writing it.

**Operator-facts go to Honcho, not the Learning Log.** Anything about Matt as a person or operator, his preferences, mental models, business facts, standing instructions, goes to Honcho as a conclusion. Write it as a specific, falsifiable observation. Hermes never needs these individually; it inherits them through the shared Honcho workspace. Do NOT create Learning Log rows for operator-facts.

**Asset changes go to the Learning Log.** Anything that requires changing a skill becomes a Learning Log row. Each row is self-contained: the ingester must be able to act on the `Body` without ever seeing the transcript. A row has:
- `Learning` — one sentence, what was learned
- `Score` — 0-100
- `Type` — `Skill Change` for skill edits. Use `Voice`, `Positioning`, `Rosetta Stone`, `Process`, or `Other` for anything that must not auto-apply.
- `Target` — the exact skill slug to edit (for `Skill Change` rows). Must match a skill in the Skill Registry exactly. If you are not certain of the slug, put your best guess and set `Type` in a way that flags, or lower the score below 70.
- `Body` — the full, self-contained instruction: what to change and how, written so the ingester can apply the smallest edit without context.
- `Source` — `Cowork` or `Hermes`
- `Session ID` — the day's session id (e.g. `cowork-2026-07-12`)
- `Status` — always `New` on write

**Type discipline.** Only mark a row `Skill Change` if it is genuinely a procedural edit to a named skill. Anything touching brand voice or positioning gets `Voice` or `Positioning` so the ingester's fence forces human approval. When in doubt about type, choose the type that flags, not the one that auto-applies.

## Writing rows

Use the script:
`HERMES_HOME=/data/.hermes python /data/.hermes/skills/self-learning/learning-writer/scripts/write_learnings.py`
Pipe a JSON array of rows on stdin (see the script's header for the exact shape). The script creates each row in the Learning Log with `Status = New` and prints the created page ids.

## Hard rules

- Self-contained bodies only. A row the ingester cannot act on without the transcript is a defect.
- Never write an operator-fact as a Learning Log row. It belongs in Honcho.
- Score conservatively. When unsure, 69.
- No em dashes in any body text.
