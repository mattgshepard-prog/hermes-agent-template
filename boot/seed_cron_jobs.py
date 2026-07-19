#!/usr/bin/env python
"""Boot-time cron job seeder for the self-learning loop (non-destructive).

Registers the two self-learning cron jobs in $HERMES_HOME/cron/jobs.json if
(and only if) a job with the same name does not already exist. Runs from
start.sh before the gateway starts, so there is no lock contention with the
running scheduler.

Design rules (Bot Builder cold-deploy standard):
- Idempotent: matched by job-name SUFFIX. A pre-existing job whose name
  ends with the wanted name (e.g. "Garry Learning Writer" for "Learning
  Writer") satisfies the seed, so personalized bots never get duplicates. Existing jobs are NEVER modified,
  re-scheduled, or overwritten — a job the operator has edited on the volume wins.
- Non-blocking: every failure path logs and exits 0. A broken seed must
  never stop gateway boot.
- Atomic write: temp file + os.replace, matching hermes's own write style.
- No hardcoded tenant identity: the Telegram delivery target comes from
  BOT_TELEGRAM_CHAT_ID (legacy alias GARRY_TELEGRAM_CHAT_ID), falling back to the first entry in
  TELEGRAM_ALLOWED_USERS. If neither is set, seeding is skipped with a loud
  log line (a digest with nowhere to deliver is worse than no job).

Job schema is copied field-for-field from live jobs registered manually on
2026-07-14 (Garry CoS, Railway), so seeded jobs are indistinguishable from
manually registered ones.
"""

import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/data/.hermes"))
JOBS_PATH = HERMES_HOME / "cron" / "jobs.json"

LOG_PREFIX = "[seed_cron_jobs]"


def log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}", flush=True)


def resolve_chat_id() -> str | None:
    """Telegram chat id for cron digest delivery, from env only."""
    explicit = (os.environ.get("BOT_TELEGRAM_CHAT_ID", "").strip()
                or os.environ.get("GARRY_TELEGRAM_CHAT_ID", "").strip())
    if explicit:
        return explicit
    allowed = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
    if allowed:
        first = re.split(r"[,\s]+", allowed)[0].strip()
        if first:
            return first
    return None


def next_daily_utc(hour: int) -> str:
    """ISO timestamp of the next occurrence of HH:00 UTC."""
    now = datetime.now(timezone.utc)
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def build_job(name: str, skill: str, prompt: str, cron_expr: str,
              cron_hour: int, chat_id: str) -> dict:
    """Full job dict matching the live jobs.json schema (2026-07-14)."""
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "prompt": prompt,
        "skills": [skill],
        "skill": skill,
        "model": None,
        "provider": None,
        "base_url": None,
        "script": None,
        "no_agent": False,
        "context_from": None,
        "schedule": {
            "kind": "cron",
            "expr": cron_expr,
            "display": cron_expr,
        },
        "schedule_display": cron_expr,
        "repeat": {"times": None, "completed": 0},
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "next_run_at": next_daily_utc(cron_hour),
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "last_delivery_error": None,
        "deliver": f"telegram:{chat_id}",
        "origin": None,
        "enabled_toolsets": None,
        "workdir": None,
        "fire_claim": None,
    }


WRITER_PROMPT = (
    "Run the learning-writer skill exactly as its SKILL.md specifies. "
    "Review yesterday's sessions and runs, extract lessons, score each one, "
    "and write them as pending rows to the Notion Self-Learning Log. "
    "Run silently if there is nothing to write."
)

INGESTER_PROMPT = (
    "Run the learning-ingester skill exactly as its SKILL.md specifies. "
    "This is a scheduled cron run, not a manual go learn trigger. "
    "Read all pending rows from the Notion Self-Learning Log, apply the "
    "scoring fence (70 plus auto-applies per the Skill Registry Self "
    "Revision and Blast Radius rules, 40 to 69 flags for approval, under "
    "40 rejects, voice and positioning never auto-apply), update row "
    "statuses, and deliver the digest."
)


def main() -> int:
    chat_id = resolve_chat_id()
    if not chat_id:
        log("SKIPPED: no BOT_TELEGRAM_CHAT_ID or TELEGRAM_ALLOWED_USERS "
            "in env; cron jobs need a Telegram delivery target. Nothing "
            "was written.")
        return 0

    wanted = [
        ("Learning Writer", "learning-writer", WRITER_PROMPT,
         "0 6 * * *", 6),
        ("Learning Ingester", "learning-ingester", INGESTER_PROMPT,
         "0 10 * * *", 10),
    ]

    try:
        JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
        if JOBS_PATH.exists():
            raw = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        else:
            raw = {"jobs": [], "updated_at": None}
        if not isinstance(raw, dict) or not isinstance(raw.get("jobs"), list):
            log(f"SKIPPED: unexpected jobs.json shape at {JOBS_PATH}; "
                "refusing to touch it.")
            return 0

        existing_names = [
            str(j.get("name") or "") for j in raw["jobs"] if isinstance(j, dict)
        ]

        added = []
        for name, skill, prompt, expr, hour in wanted:
            match = next((e for e in existing_names if e.endswith(name)), None)
            if match is not None:
                log(f"present: {match} satisfies '{name}' (untouched)")
                continue
            raw["jobs"].append(
                build_job(name, skill, prompt, expr, hour, chat_id)
            )
            added.append(name)

        if not added:
            log("All self-learning cron jobs already registered; no write.")
            return 0

        raw["updated_at"] = datetime.now(timezone.utc).isoformat()
        tmp = JOBS_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(tmp, JOBS_PATH)
        log(f"Seeded {len(added)} cron job(s): {', '.join(added)} "
            f"(deliver=telegram:{chat_id})")
        return 0

    except Exception as e:  # never block boot
        log(f"WARNING: seed failed and was skipped: {type(e).__name__}: {e}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
