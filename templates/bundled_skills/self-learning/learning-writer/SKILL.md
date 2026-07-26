---
name: learning-writer
description: "Nightly self-learning sweep: read the day's session transcripts, extract and score lessons 0-100, and route by bucket. Every lesson becomes a pending row in the Notion Learning Log, tool-specific ones linked to the target skill in the Skill Registry. The ingester processes them and is the only component that writes to Honcho."
version: 2.2.0
author: Matt Shepard
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  env_vars: [NOTION_TOKEN, NOTION_LEARNING_LOG_DB, NOTION_SKILL_REGISTRY_DS]
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

The writer half of the loop. Once a day, read everything that happened, distill it into scored lessons, and route each to the right home. This never applies changes; it only produces the material the ingester acts on.

Runs primarily on the Cowork surface (where the day's transcripts live). Hermes can also run it to log its own lessons inline, with `Source` noting the Hermes session.

## Live schema (verified against the live Ops workspace)

**Learning Log** row: `Lesson` (title, the actionable rule), `Score` (0-100), `Bucket` (`behavioral` | `tool-specific` | `unsorted`), `Target Skill` (relation to a Skill Registry page, for tool-specific), `Rationale` (text), `Source` (text), `Status` (`pending` on write).

**Skill Registry** page: `Skill Name` (title), plus governance fields the ingester reads (`Self Revision`, `Blast Radius`). The writer only needs the page id of the target skill, resolved by name.

## Files

- Writer script: `scripts/write_learnings.py` (creates Learning Log rows)
- Skill lookup: `../learning-ingester/scripts/ingest_learning_log.py find-skill "Skill Name"` resolves a skill name to its Registry page id for the `Target Skill` relation
- Honcho: NOT written by this skill at all. Behavioral lessons are written as `pending` Learning Log rows and the learning-ingester routes them to Honcho with its own sanctioned script. Do not look for Honcho MCP tools; there are none on this surface.

## How it runs

1. Gather the day's transcripts. On Cowork read the full session transcripts, including compacted ones (read the compacted summary if the raw is unavailable). On Hermes use the `session_search` tool, which is the supported way to read this agent's own sessions. Do not shell out to `sqlite3` (not installed) and do not reach for `execute_code` (blocked on cron runs).
2. Extract candidate lessons: corrections the operator made, friction in a skill, decisions with reasoning, revealed preferences, any skill that misbehaved.
3. Score and bucket each one (below).
4. Write every lesson to the Learning Log as a `pending` row, behavioral ones included. The ingester routes behavioral rows to Honcho on its next run.

If nothing is worth recording, write nothing and exit. An empty day is fine.

## Scoring (0-100)

- Hard explicit correction from the operator: near 100.
- Clear preference stated once: 70-90.
- Reasonable inference from context: 40-69.
- Weak inference from one ambiguous moment: under 40.

**When intent is ambiguous, score 69, not 70.** 69 sends a tool-specific lesson to needs-approval in the ingester; 70 can auto-apply (if the target skill also allows it). Ambiguity must never auto-apply.

## Bucketing (the destination decision)

**behavioral**: anything about the operator as a person, preferences, mental models, business facts, standing instructions. Phrase it as a specific, falsifiable observation, because it becomes a Honcho conclusion. Write it as a Learning Log row with `Bucket = behavioral`, no `Target Skill`, and `Status = pending`. The ingester performs the Honcho write and stamps the row `ingested` with the returned `conclusion_id`. Do not attempt a Honcho write here and do not hold the lesson back if Honcho looks unavailable; the row is the handoff.

**tool-specific**: a procedural edit to one named skill. Becomes a Learning Log row with `Bucket = tool-specific` and a `Target Skill` relation to that skill's Registry page. The `Lesson` title must be self-contained: the ingester acts on it without the transcript. Resolve the target skill's page id first:
`HERMES_HOME=/data/.hermes python /data/.hermes/skills/self-learning/learning-ingester/scripts/ingest_learning_log.py find-skill "Exact Skill Name"`
If the name matches no skill, set `Bucket = unsorted` (do not guess a target) and let the ingester flag it.

**unsorted**: you cannot cleanly classify it. Write it `unsorted` with no target; the ingester routes it to needs-approval for the operator to triage. Prefer unsorted over a wrong guess.

## Writing rows

Write the JSON array to a file with the `write_file` tool, for example `/tmp/learning_rows.json`, then run:

`HERMES_HOME=/data/.hermes python /data/.hermes/skills/self-learning/learning-writer/scripts/write_learnings.py --file /tmp/learning_rows.json`

Do NOT pipe the JSON in. `echo ... | python ...` is refused by the command scanner as a pipe to an interpreter, and on a cron run there is no operator present to approve it. If any command here is refused, do not rewrite it to get around the refusal; report the refusal verbatim.

Row shape: `lesson`, `score`, `bucket`, `target_skill_id` (for tool-specific), `rationale`, `source`. All rows write with `Status = pending`.

## Hard rules

- Self-contained `Lesson` titles. A row the ingester cannot act on without the transcript is a defect. Put the reasoning in `Rationale`, the rule in `Lesson`.
- Every lesson is a Learning Log row, behavioral included. This skill never writes to Honcho; the ingester does.
- Prefer `unsorted` over a wrong `Target Skill`.
- Score conservatively. When unsure, 69.
- No em dashes in any text.

## Changelog

v2.1.0 -- operator-neutral wording for Bot Builder client baseline; logic and fence unchanged -- 2026-07-19
v2.2.0 -- behavioral lessons now become `pending` Learning Log rows instead of direct Honcho writes, because the documented Honcho MCP path does not exist on this surface and both bots improvised around it (Bailey dumped to unsorted 2026-07-24; Garry wrote behavioral rows against its own rule, which is the only reason any conclusion exists). The ingester is now the single Honcho writer. write_learnings.py takes `--file PATH` because the piped form is refused by the command scanner. Step 1 names session_search as the Hermes transcript path. Scoring and bucketing unchanged -- 2026-07-26
