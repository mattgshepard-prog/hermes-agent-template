---
name: learning-ingester
description: "The assistant's self-learning ingester, runnable two ways: the nightly cron, or on demand when the operator says 'go learn' on Telegram. Reads pending rows from the Notion Learning Log, applies tool-specific skill edits under a two-layer fence (score plus the target skill's Self Revision setting), routes behavioral lessons to Honcho, sets each row's status, and reports what was ingested, flagged for approval, or rejected. Cron runs email the operator a digest; manual runs reply in the chat."
version: 2.3.0
author: Matt Shepard
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  env_vars: [NOTION_TOKEN, NOTION_LEARNING_LOG_DS, NOTION_SKILL_REGISTRY_DS]
triggers:
  - the Self-Learning Ingester cron job fires
  - user says "go learn"
  - user says "learn now"
  - user says "run the ingester"
  - user says "ingest your lessons"
  - user says "process the learning log"
  - user asks to ingest the learning log
  - user asks to run the nightly self-learning
tags: [self-learning, notion, skills, cron, memory, digest]
metadata:
  hermes:
    tags: [SelfLearning, Notion, Skills, Cron, Memory]
---

# Learning Ingester

The assistant's standing job: turn scored rows in the Notion Learning Log into real changes, safely. Read pending rows, apply the ones that pass the fence, flag the ones that need the operator, reject the weak ones, then report. Every applied change is reversible.

This is the reader half of the loop. The writer half (`learning-writer`) produces the rows this skill consumes. This skill never writes new lessons; it only processes existing ones.

## Two entry points, one implementation

This skill runs identically whether triggered by the nightly cron or by the operator on Telegram ("go learn", "learn now", "run the ingester", "ingest your lessons"). Same query, same fence, same status writes. The only difference is how the report is delivered:

- **Cron run**: email the digest to the operator (their primary inbox), as specified in the digest section.
- **Manual run (the operator asked in chat)**: reply with the digest content directly in the Telegram chat, immediately, in the same format. Do not also send the email; the operator just read it. The chat reply is the visibility record for that run.

Manual runs are safe to repeat. Only `pending` rows are processed, so "go learn" twice in a row processes nothing the second time and replies with the steady-state line.

Do not confuse this with `log-this-now`. That skill captures a NEW lesson from the current conversation and scores it. This skill processes lessons already sitting in the Learning Log. If the operator says "log this" or describes something to remember, that is `log-this-now`. If the operator says "go learn" or asks to process the log, that is this skill.

## Live schema (verified against the live Ops workspace, do not assume otherwise)

**Learning Log** rows have: `Lesson` (title, the actionable rule), `Score` (0-100), `Bucket` (`behavioral` | `tool-specific` | `unsorted`), `Target Skill` (relation to a Skill Registry page, set for tool-specific lessons), `Rationale` (text), `Source` (text), `Status` (`pending` | `ingested` | `rejected` | `needs-approval`), `Ingested` (date).

**Skill Registry** pages have: `Skill Name` (title), `Version` (text), `Definition` (file, the SKILL.md), `Self Revision` (`auto` | `propose-only` | `locked`), `Blast Radius` (`low` | `medium` | `high`), `Surface`, `Revised By`, `Last Revised`, `Lessons` (relation), `Notes`.

## Files

- Pre-pass script: `scripts/ingest_learning_log.py` (queries pending rows; helpers for status, ingested-stamp, skill lookup)
- Honcho routing script: `scripts/route_behavioral_to_honcho.py` (the ONLY check and write path for behavioral lessons)
- Notion access: the script's own HTTP calls, or the `notion` skill
- Cron delivery: the operator's Telegram; digest emailed to the operator

## How it runs

1. Run the pre-pass: `HERMES_HOME=/data/.hermes python /data/.hermes/skills/self-learning/learning-ingester/scripts/ingest_learning_log.py`
   It prints pending rows: `{page_id, lesson, score, bucket, target_skill_ids, rationale, source}`.
2. Process each row through the fence below.
3. For each row, set its `Status` and (when ingested) stamp `Ingested`.
4. Deliver the report (final step): email on a cron run, in-chat reply on a manual run.

If `count` is 0, do NOT stay silent. Send the steady-state report through the run's delivery channel, then exit.

## The fence (two layers: Bucket/Score, then the target skill's Self Revision)

Bucket governs the destination. Score and the target skill's `Self Revision` govern whether a tool-specific edit auto-applies. The fence is identical for cron and manual runs. A manual "go learn" grants no extra permission: locked stays locked, propose-only stays propose-only, high blast radius still stops at needs-approval.

### behavioral lessons
Route to Honcho as a conclusion (a specific, falsifiable observation about how the operator decides, prioritizes, or works). These never edit a skill.

The routing script is the single source of truth for whether Honcho is configured AND for the write itself. Do not inspect environment variables to decide whether Honcho is available, do not reason about config.yaml, and do not improvise another route through memory tools. Run the script; believe its output; quote its output.

1. Check availability:
   `HERMES_HOME=/data/.hermes python /data/.hermes/skills/self-learning/learning-ingester/scripts/route_behavioral_to_honcho.py check`
   Exit 0 with `"configured": true` means Honcho is live. Anything else means it is not; the digest must quote the script's `reason` field verbatim, never a guessed explanation.
2. Write all behavioral rows in one call. Pipe a JSON array of `{"content": lesson text, "row_id": page_id}` to:
   `HERMES_HOME=/data/.hermes python /data/.hermes/skills/self-learning/learning-ingester/scripts/route_behavioral_to_honcho.py write`
   The script writes each lesson as a Honcho conclusion (observer: the assistant's peer, observed: the operator's peer) and prints JSON with a `conclusion_id` per `row_id`.
3. Set `Status = ingested` and stamp `Ingested` ONLY for rows whose `row_id` appears in the script output with a non-null `conclusion_id`. Any row not confirmed stays `needs-approval` with the script's error quoted in the digest, so the lesson is not silently lost.

### tool-specific lessons
These target one skill via the `Target Skill` relation. Resolve the relation to the Skill Registry page and read its `Self Revision` and `Blast Radius` with:
`ingest_learning_log.py get-skill TARGET_PAGE_ID`

Then apply this table:

- **Self Revision = locked**: never edit, at any score. Set `Status = needs-approval`. Digest note: "target skill is locked."
- **Self Revision = propose-only**: never auto-edit, at any score. Set `Status = needs-approval` with the proposed change in the digest for the operator's yes or no.
- **Self Revision = auto**:
  - **Score 70+**: auto-apply. Edit the skill's `Definition` (the SKILL.md): make the smallest change that fully implements the `Lesson`. Bump `Version`. Set `Revised By = skill-b`. Set `Last Revised = today`. Set the row `Status = ingested` and stamp `Ingested`.
  - **Score 40-69**: `Status = needs-approval`. Surface in the digest under "Waiting on you."
  - **Score under 40**: `Status = rejected`. Digest count only.

**Blast Radius is a hard brake regardless of Self Revision.** If the target skill's `Blast Radius = high` (money, client sends, courts, deploys), never auto-apply even at score 70+ with Self Revision auto. Set `Status = needs-approval`. High blast radius always gets human eyes. This protects the skills where a bad edit has real-world cost.

### unsorted bucket
The writer left the bucket undetermined. Do not guess. Set `Status = needs-approval` and note "bucket unsorted, needs classification."

### When unsure
Treat as `needs-approval`, never `ingested`. Ambiguity flags.

### Target Skill relation checks
- If a tool-specific row has no `Target Skill` relation, set `Status = needs-approval`, note "tool-specific but no target skill."
- If the relation points to more than one skill, set `Status = needs-approval`, note "multiple target skills."
- One lesson, one edit. If two rows target the same skill and conflict, the higher score wins and applies (if it passes the fence); the other becomes `needs-approval` with a conflict note.

## Making a skill edit (the Skill Registry is the source of truth)

The canonical skill lives in the Skill Registry `Definition` (the SKILL.md). Edit it there. The sync of that edited SKILL.md back into the running skill on the volume and into Cowork is the Phase 2 concern, not this skill's job.

For an auto-apply:
1. Read the target page's current `Definition` SKILL.md and `Version`.
2. Apply the smallest change that fully implements the `Lesson`. Do not rewrite unrelated sections. Do not improve beyond the lesson.
3. Bump `Version` (patch level unless the change adds or removes a step).
4. Append a changelog note (in `Notes` or at the bottom of the Definition): `vX.Y.Z -- one-sentence description -- from lesson <page_id short> -- <UTC date>`. That line is the rollback record.
5. Set `Revised By = skill-b`, `Last Revised = today`.
6. Set the source row `Status = ingested`, stamp `Ingested`.

The changelog line is the undo. A bad edit shows in the next digest and the changelog says exactly what to revert.

## Memory

Behavioral lessons go to Honcho (above), not to MEMORY.md. Tool-specific lessons live in the edited skill, not in memory. Do not copy lesson bodies into MEMORY.md. MEMORY.md is bounded (~2,200 chars); the durable home for a lesson is the skill it changed or the Honcho conclusion.

## The digest (final step)

Always send, even on a zero-change run. Steady-state framing, never "I learned nothing."

- One-line summary: `N ingested, M waiting on you, K rejected.`
- **Ingested** (if any): for each, `skill-name vX.Y.Z -- one-line description`. the operator's 24-hour visibility on every auto-edit. Behavioral ingests list as `honcho -- lesson first words -- conclusion_id`.
- **Waiting on you** (if any needs-approval): for each, `reason (score, target) -- proposed change -- [row link]`. Group by reason: propose-only, locked, high blast radius, score 40-69, unsorted, no target, conflict.
- **Rejected**: count only.
- If nothing: `No pending lessons today. Running on the current skill library.`

Delivery depends on the entry point:
- **Cron run**: email to the operator (their primary inbox). This is outbound-to-operator, a report, so it sends without an approval gate.
- **Manual run**: reply in the Telegram chat, same content and format, no email.

No em dashes. Short.

## Hard rules (standing communication rules)

- Never use em dashes anywhere in the digest or any skill edit. Use commas, periods, or restructure.
- No motivational filler. State what happened.
- Short and direct.

## Idempotency

Every processed row must end as `ingested`, `needs-approval`, or `rejected`. A row left `pending` will be re-processed tomorrow. If a Notion write fails, retry once; if it still fails, leave the row `pending` (retried next run, not lost) and note the failure in the digest. This same guarantee is what makes manual runs repeat-safe.

## The silent-failure mode to watch

If the query returns zero pending rows for several nights, that may mean the writer stopped producing rows, not that there was nothing to learn. If the operator reports the digest has said "nothing pending" for many days, check that `learning-writer` is running and writing rows before assuming the loop is healthy. A clean-looking empty result can mask an upstream failure.

## Changelog

v2.1.0 -- add on-demand Telegram trigger (go learn, learn now, run the ingester, ingest your lessons) with in-chat report delivery on manual runs; fence unchanged -- 2026-07-13
v2.2.0 -- behavioral routing made concrete: new scripts/route_behavioral_to_honcho.py is the only check and write path (Honcho conclusions, observer assistant peer, observed operator peer); agent forbidden from inferring Honcho availability from env inspection; ingested requires a conclusion_id from script output; fence unchanged -- 2026-07-16
v2.3.0 -- operator-neutral wording for Bot Builder client baseline; logic, fence, and scripts unchanged -- 2026-07-19
