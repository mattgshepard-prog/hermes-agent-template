#!/usr/bin/env python
"""Route behavioral lessons to Honcho as conclusions.

The single source of truth for whether Honcho is reachable and for writing
behavioral lessons to it. The ingester agent must use this script for both,
never environment inspection and never an improvised route. Built 2026-07-16
after the agent, finding no concrete write path, wrongly reported
"HONCHO_API_KEY is not present in this environment" while the key sat in the
gateway env the whole time. One command, one truth.

Identity contract (verified against the live provider 2026-07-16):
  observer  = the assistant peer (cfg.ai_peer, "hermes" by default). Dialectic
              queries run with ai_observe_others=True, so the assistant peer's
              conclusions about Matt are exactly what dialectic reads back.
  observed  = Matt's runtime peer. Resolution: GARRY_OPERATOR_PEER_ID env var
              if set, else the first entry of TELEGRAM_ALLOWED_USERS (the
              Telegram runtime peer id IS the chat id, so this derives the
              peer instead of hand-maintaining it). Multi-tenant safe: no
              hardcoded ids.

Config resolves through the honcho plugin's own chain
($HERMES_HOME/honcho.json -> ~/.honcho/config.json -> env vars), so this
script and the live memory provider always agree on workspace and key.

Commands:
  check   Print JSON {configured, workspace, observer, observed}.
          Exit 0 when Honcho is reachable and both peers resolve.
          Exit 2 when not configured, with a "reason" field. The digest must
          quote that reason verbatim, never a guessed one.
  write   Read a JSON array from stdin: [{"content": str, "row_id": str}].
          Write each as a conclusion in one batch. Print JSON:
          {"written": N, "results": [{"row_id", "conclusion_id"}, ...],
           "workspace", "observer", "observed"}.
          Exit 0 when every row got a conclusion_id, 1 otherwise.
          Only rows with a conclusion_id in the output may be set ingested.
"""

import json
import os
import re
import sys

sys.path.insert(0, "/opt/hermes-agent")


def _resolve_observed() -> str | None:
    explicit = os.environ.get("GARRY_OPERATOR_PEER_ID", "").strip()
    if explicit:
        return explicit
    allowed = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
    if allowed:
        first = re.split(r"[,\s]+", allowed)[0].strip()
        if first:
            return first
    return None


def _connect():
    """Return (client, cfg, observer_id, observed_id) or raise with a plain reason."""
    from plugins.memory.honcho.client import (
        HonchoClientConfig,
        get_honcho_client,
    )

    cfg = HonchoClientConfig.from_global_config()
    if not cfg.enabled or not (cfg.api_key or cfg.base_url):
        raise RuntimeError(
            "Honcho config chain resolved no API key or base URL "
            "(checked honcho.json, ~/.honcho/config.json, env vars)."
        )
    observed = _resolve_observed()
    if not observed:
        raise RuntimeError(
            "No observed peer: set GARRY_OPERATOR_PEER_ID or "
            "TELEGRAM_ALLOWED_USERS in the environment."
        )
    client = get_honcho_client(cfg)
    observer = (getattr(cfg, "ai_peer", None) or "hermes").strip() or "hermes"
    return client, cfg, observer, observed


def cmd_check() -> int:
    try:
        client, cfg, observer, observed = _connect()
        # Prove reachability with a real API call, not just config presence.
        client.peer(observer).conclusions_of(observed).list(size=1)
        print(json.dumps({
            "configured": True,
            "workspace": cfg.workspace_id,
            "observer": observer,
            "observed": observed,
        }))
        return 0
    except Exception as e:
        print(json.dumps({
            "configured": False,
            "reason": f"{type(e).__name__}: {e}",
        }))
        return 2


def cmd_write() -> int:
    try:
        rows = json.load(sys.stdin)
        if not isinstance(rows, list) or not rows:
            print(json.dumps({"error": "stdin must be a non-empty JSON array"}))
            return 1
        for r in rows:
            if not isinstance(r, dict) or not str(r.get("content", "")).strip():
                print(json.dumps({"error": "every row needs a non-empty 'content'"}))
                return 1

        client, cfg, observer, observed = _connect()
        scope = client.peer(observer).conclusions_of(observed)
        created = scope.create(
            [{"content": str(r["content"]).strip()} for r in rows]
        )
        results = []
        for r, c in zip(rows, created):
            results.append({
                "row_id": r.get("row_id"),
                "conclusion_id": getattr(c, "id", None),
            })
        ok = len(created) == len(rows) and all(x["conclusion_id"] for x in results)
        print(json.dumps({
            "written": sum(1 for x in results if x["conclusion_id"]),
            "results": results,
            "workspace": cfg.workspace_id,
            "observer": observer,
            "observed": observed,
        }))
        return 0 if ok else 1
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        return 1


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "check":
        return cmd_check()
    if cmd == "write":
        return cmd_write()
    print(json.dumps({"error": "usage: route_behavioral_to_honcho.py check|write"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
