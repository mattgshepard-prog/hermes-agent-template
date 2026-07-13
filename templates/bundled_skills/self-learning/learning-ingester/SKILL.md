---
name: learning-ingester
description: "Garry's nightly self-learning ingester: read unprocessed rows from the Notion Learning Log, apply skill changes under a strict scoring fence, promote applied lessons into memory, mark each row's status, and email Matt a digest of what was applied, flagged, or discarded."
version: 1.0.0
author: Matt Shepard
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  env_vars: [NOTION_API_KEY, NOTION_LEARNING_LOG_DS, NOTION_SKILL_REGISTRY_DS, NOTION_SKILL_REGISTRY_DB]
triggers:
  - the Self-Learning Ingester cron job fires
  - user asks to ingest the learning log
  - user asks to run the nightly self-learning
tags: [self-learning, notion, skills, cron, memory, digest]
metadata:
  hermes:
    tags: [SelfLearning, Notion, Skills, Cron, Memory]
---

# Learning Ingester

Garry's standing nightly job: turn scored rows in the Notion Learning Log into real changes, safely. Read unprocessed rows, apply the ones that pass a strict fence, flag the ones that need Matt, discard the weak ones, then email Matt a digest of exactly what happened. Every applied change is reversible.

This is the reader half of the self-learning loop. The writer half (`learning-writer`) produces the rows this skill consumes. This skill never writes new lessons; it only processes existing ones.

## Files

- Pre-pass script: `scripts/ingest_learning_log.py` (in this skill)
- Notion access: HTTP + curl per the `notion` skill, or the script's own `requests` calls
- Skill Registry lives in Notion; skill edits are made there (the canonical store), not to local skill files
- Cron delivery: Matt's Telegram

## Environment (read from .env, never hardcode)

- `NOTION_API_KEY` — integration token, shared with the Learning Log and Skill Registry databases
- `NOTION_LEARNING_LOG_DS` — data_source_id of the Learning Log (for querying rows)
- `NOTION_SKILL_REGISTRY_DS` — data_source_id of the Skill Registry (for finding target skills)
- `NOTION_SKILL_REGISTRY_DB` — database_id of the Skill Registry (for creating/patching skill pages)

If any required variable is missing, exit with a single clear error to Telegram and do nothing else. Never guess an ID.

## How it runs

1. The cron job (or the user) runs the pre-pass script:
   `HERMES_HOME=/data/.hermes python /data/.hermes/skills/self-learning/learning-ingester/scripts/ingest_learning_log.py`
2. The script queries the Learning Log for rows where `Status = New`, and prints JSON:
   ```json
   {
     "rows": [
       {"page_id":"...","learning":"...","score":82,"type":"Skill Change","target":"garry-email-followup","body":"...","source":"Cowork","session_id":"cowork-2026-07-12"}
     ],
     "count": 0,
     "learning_log_ds": "...",
     "skill_registry_ds": "...",
     "skill_registry_db": "..."
   }
   ```
3. Garry reads that output and processes each row through the fence below.
4. Garry marks each row's `Status` to `Applied`, `Flagged`, or `Discarded` and, for applied rows, writes the changelog and a one-line memory breadcrumb.
5. Garry emails Matt the digest (final step).

If `count` is 0, do NOT stay silent. Send the steady-state digest (see "The digest" below), then exit. A daily report that reliably arrives, even to say nothing changed, is the point.

## The fence (Carol Roderick's governance model, adopted verbatim)

Every row is already scored 0-100 by the writer. Route by score AND type. Type governs first, score governs second.

**Only `Skill Change` rows are ever auto-applied.** Every other type (Voice, Positioning, Rosetta Stone, Process, Other) takes the flag-and-approve path at any score, no exceptions.

For `Skill Change` rows:
- **Score 70 or above: Auto-apply.** Edit the target skill in the Notion Skill Registry. Make the smallest edit that fully implements the learning. Bump the skill's version (semver patch unless the change is structural). Write one changelog line. That line is the rollback record. Set the row's `Status = Applied`.
- **Score 40 to 69: Flag.** Do not edit anything. Set `Status = Flagged`. It goes into the digest under "Waiting on you" with the proposed change, for Matt's yes or no.
- **Score under 40: Discard.** Set `Status = Discarded`. It appears in the digest only as a count.

For `Voice` and `Positioning` rows: **never auto-apply, at any score.** Brand voice and positioning do not get edited by a machine overnight. Set `Status = Flagged`, surface in the digest for Matt's approval.

For `Rosetta Stone`, `Process`, `Other`: same flag-and-approve path as voice, at any score. Set `Status = Flagged`.

**When unsure, score is treated as 69.** The writer is instructed to score 69 rather than 70 whenever intent is ambiguous, so ambiguity flags instead of applies. If you the ingester are ever unsure whether a row qualifies as a clean Skill Change, treat it as Flagged, not Applied.

**One learning, one edit.** If two rows conflict on the same target skill, the higher score wins and applies; the lower-scored one is flagged with a note that it conflicted. Never apply both.

**Slug match is mandatory.** If a row's `target` does not match exactly one skill in the Skill Registry, do NOT guess and do NOT edit the closest match. Set `Status = Flagged` with reason "target slug matched no skill" (or "matched multiple"). Unmatched targets never auto-apply.

## Making a skill edit (the Notion Skill Registry is the source of truth)

Skills are edited in the Notion Skill Registry, not by writing local files on the volume. The registry page is the canonical version; the sync back to Cowork/Hermes is a separate concern (handled by the skill-sync process, Phase 2).

For an auto-apply:
1. Find the skill page in the Skill Registry by exact slug match on the row's `target`.
2. Read its current SOP/body and version.
3. Apply the smallest change that fully implements the learning `body`. Do not rewrite unrelated sections. Do not "improve" beyond the learning.
4. Bump the version (patch level, e.g. 1.2.0 to 1.2.1, unless the change adds or removes a step, then minor).
5. Append one changelog line: `vX.Y.Z — <one-sentence description> — from learning <page_id short> — <UTC date>`.
6. Update the skill page's version and changelog properties in Notion.
7. Set the source Learning Log row `Status = Applied` and stamp it with the applied timestamp.

The changelog line is the undo. If a bad edit ships, Matt sees it in the next digest and the changelog line tells him exactly what to revert.

## Promoting to memory (bounded, per the memory-stack design)

The durable home for a skill lesson is the skill it changed, not the memory file. Do NOT copy full lesson bodies into MEMORY.md. MEMORY.md is bounded (~2,200 chars) and fills fast.

For applied skill changes, write at most a single terse breadcrumb line to MEMORY.md under an "Applied lessons" note, only if the lesson changes how Garry should behave generally (not skill-internal detail). Most applied skill edits need no memory write at all, because the behavior now lives in the edited skill. When the memory note approaches its cap, consolidate the oldest breadcrumbs rather than growing the file.

Operator-facts about Matt do NOT come through this path at all. Those are routed by the writer straight to Honcho as conclusions and are inherited through the shared workspace. The ingester never writes operator-facts to memory.

## Marking status (idempotency)

Every row processed this run must end with `Status` set to `Applied`, `Flagged`, or `Discarded`. A row left as `New` will be re-ingested tomorrow. Setting status is not optional and is the last action per row. If a Notion write fails, retry once; if it still fails, leave the row `New` (so it is retried next run, not lost) and note the failure in the digest.

## The digest (final step, emailed to Matt)

Always send, even on a zero-change night. Steady-state framing, never "I learned nothing."

Structure:
- One-line summary: `N applied, M flagged, K discarded. Skill library now at V lessons total.`
- **Applied** (if any): for each, `skill-slug vX.Y.Z — one-line description`. This is Matt's 24-hour visibility on every auto-edit.
- **Waiting on you** (if any flagged): for each, `type — target — proposed change — [row link]`. Matt approves or rejects from Notion.
- **Discarded**: just the count.
- If nothing at all: `No new lessons today. Running on the current knowledge base of V lessons.`

Deliver as an email to Matt (his primary inbox), matching the write-before-send discipline: this is outbound-to-Matt, a report, so it sends without an approval gate. Do NOT use em dashes in the digest body. Keep it short.

## Hard rules (Matt's standing communication rules)

- Never use em dashes anywhere in the digest or any skill edit. Use commas, periods, or restructure.
- No motivational filler. State what happened.
- Short and direct.

## The silent-failure mode to watch

If the Notion query returns zero rows every night for several nights, that may mean the writer stopped producing rows, not that there was nothing to learn. The digest cannot tell the difference on its own. If Matt reports the digest has said "nothing new" for many days running, check that `learning-writer` is actually running and writing rows before assuming the loop is healthy. This mirrors the Railway "logs show the previous deployment" lesson: a clean-looking empty result can mask an upstream failure.
