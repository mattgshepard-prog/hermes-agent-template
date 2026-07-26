#!/usr/bin/env python
"""Route behavioral lessons to Honcho as conclusions.

The single source of truth for whether Honcho is reachable and for writing
behavioral lessons to it. The ingester agent must use this script for both,
never environment inspection and never an improvised route. Built 2026-07-16
after the agent, finding no concrete write path, wrongly reported
"HONCHO_API_KEY is not present in this environment" while the key sat in the
gateway env the whole time. One command, one truth.

Root cause, confirmed 2026-07-25. The agent's terminal tool spawns
subprocesses with a SCRUBBED environment. Running `diag` from the gateway vs
over SSH proves it:

    gateway: HONCHO_API_KEY unset, TELEGRAM_ALLOWED_USERS unset,
             HOME=/data/.hermes/home, cwd=/tmp, _HERMES_GATEWAY=1
    ssh:     HONCHO_API_KEY len 71, TELEGRAM_ALLOWED_USERS len 10,
             HOME=/data, cwd=/

honcho.json carries enabled/workspace/aiPeer/peerName but no credential, and
base_url is null, so in the gateway the config chain resolved api_key empty
and every cron behavioral route failed (Garry 07-23 06:01, 07-23 10:00,
07-25 10:01; Bailey 07-25 10:00). The 07-16 note above is therefore wrong:
that agent was correct about its own environment, and the script written to
end that class of error inherited the same dependency.

Two fixes. The credential now comes from honcho.json, seeded there on every
boot by start.sh, which the plugin reads BEFORE the environment. The observed
peer now falls back to the pinned peerName in the same file, because
TELEGRAM_ALLOWED_USERS is scrubbed too and would have failed one check later.

The original _connect() also collapsed two distinct failure conditions into
one message, so a disabled provider reported itself as a missing credential.
The plugin's own CLI gets this right (plugins/memory/honcho/cli.py:1065).
This script now does too, and attaches a "diag" object on every failure.

No secret values are ever printed. Keys are reported as presence and length.

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
          Exit 2 when not configured, with a "reason" field and a "diag"
          object. The digest must quote that reason verbatim, never a
          guessed one.
  diag    Print the full resolution picture and exit 0 regardless of state.
          Use this to compare contexts (gateway vs shell vs cron) without
          needing a failure to be in progress.
  write   Read a JSON array of [{"content": str, "row_id": str}] from the
          path given by --file PATH, or from stdin when --file is absent.
          Prefer --file. Piping into the interpreter is refused by the
          command scanner and no one is present to approve it on a cron run.
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
import time

sys.path.insert(0, "/opt/hermes-agent")

HERMES_HOME_DEFAULT = "/data/.hermes"

# Populated by _connect() so failure paths can report what was actually
# resolved rather than guessing.
_LAST_CFG = None


def _hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or HERMES_HOME_DEFAULT


def _path_state(path: str) -> dict:
    """Existence and readability of a config path. Never reads contents."""
    try:
        exists = os.path.exists(path)
        return {
            "path": path,
            "exists": exists,
            "readable": os.access(path, os.R_OK) if exists else False,
            "size": os.path.getsize(path) if exists else None,
        }
    except Exception as e:
        return {"path": path, "error": f"{type(e).__name__}: {e}"}


def _env_state(name: str) -> dict:
    """Presence and length of an env var. Never the value."""
    v = os.environ.get(name)
    if v is None:
        return {"set": False}
    return {"set": True, "len": len(v)}


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _diag_payload(cfg=None) -> dict:
    home = _hermes_home()
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hermes_home_env": os.environ.get("HERMES_HOME"),
        "hermes_home_resolved": home,
        "home_env": os.environ.get("HOME"),
        "cwd": _safe(os.getcwd),
        "uid": _safe(os.getuid),
        "pid": os.getpid(),
        "gateway_flag": os.environ.get("_HERMES_GATEWAY"),
        "argv0": sys.argv[0],
        "files": {
            "honcho_json": _path_state(os.path.join(home, "honcho.json")),
            "hermes_env": _path_state(os.path.join(home, ".env")),
            "config_yaml": _path_state(os.path.join(home, "config.yaml")),
            "user_honcho_config": _path_state(
                os.path.expanduser("~/.honcho/config.json")
            ),
        },
        "env": {
            "HONCHO_API_KEY": _env_state("HONCHO_API_KEY"),
            "HONCHO_BASE_URL": _env_state("HONCHO_BASE_URL"),
            "HONCHO_WORKSPACE": _env_state("HONCHO_WORKSPACE"),
            "TELEGRAM_ALLOWED_USERS": _env_state("TELEGRAM_ALLOWED_USERS"),
            "GARRY_OPERATOR_PEER_ID": _env_state("GARRY_OPERATOR_PEER_ID"),
        },
    }
    if cfg is not None:
        api_key = getattr(cfg, "api_key", None) or ""
        payload["cfg"] = {
            "enabled": bool(getattr(cfg, "enabled", False)),
            "api_key_present": bool(api_key),
            "api_key_len": len(api_key),
            "base_url": getattr(cfg, "base_url", None),
            "workspace_id": getattr(cfg, "workspace_id", None),
            "ai_peer": getattr(cfg, "ai_peer", None),
            "peer_name": getattr(cfg, "peer_name", None),
        }
    else:
        payload["cfg"] = None
    return payload


def _load_cfg():
    """Resolve HonchoClientConfig, recording it for diagnostics."""
    global _LAST_CFG
    from plugins.memory.honcho.client import HonchoClientConfig

    cfg = HonchoClientConfig.from_global_config()
    _LAST_CFG = cfg
    return cfg


def _resolve_observed(cfg=None) -> str | None:
    """Resolve Matt's runtime peer.

    Order: GARRY_OPERATOR_PEER_ID -> TELEGRAM_ALLOWED_USERS -> honcho.json
    peerName. The third source exists because the agent's terminal tool
    scrubs the environment (proven 2026-07-25: both env vars absent in the
    gateway context, present over SSH), which left this returning None and
    failing one check later than the credential did.
    """
    explicit = os.environ.get("GARRY_OPERATOR_PEER_ID", "").strip()
    if explicit:
        return explicit
    allowed = os.environ.get("TELEGRAM_ALLOWED_USERS", "").strip()
    if allowed:
        first = re.split(r"[,\s]+", allowed)[0].strip()
        if first:
            return first
    pinned = (getattr(cfg, "peer_name", None) or "").strip() if cfg else ""
    if pinned:
        return pinned
    return None


def _connect():
    """Return (client, cfg, observer_id, observed_id) or raise with a plain reason."""
    from plugins.memory.honcho.client import get_honcho_client

    cfg = _load_cfg()

    # Two distinct conditions, two distinct reasons. Collapsing these is what
    # made the 07-23 and 07-25 cron failures unreadable.
    if not cfg.enabled:
        raise RuntimeError(
            "Honcho resolved as DISABLED (cfg.enabled is False). "
            "This is not a missing credential. See diag."
        )
    if not (cfg.api_key or cfg.base_url):
        raise RuntimeError(
            "Honcho enabled but the config chain resolved neither an API key "
            "nor a base URL. See diag."
        )

    observed = _resolve_observed(cfg)
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
            "diag": _diag_payload(_LAST_CFG),
        }))
        return 2


def cmd_diag() -> int:
    """Always exit 0. Reports what resolved, whether or not it works."""
    cfg = None
    cfg_error = None
    try:
        cfg = _load_cfg()
    except Exception as e:
        cfg_error = f"{type(e).__name__}: {e}"

    payload = _diag_payload(cfg)
    payload["cfg_error"] = cfg_error
    payload["observed_resolved"] = _resolve_observed(cfg)

    reach = None
    if cfg is not None and cfg.enabled and (cfg.api_key or cfg.base_url):
        try:
            from plugins.memory.honcho.client import get_honcho_client
            observer = (getattr(cfg, "ai_peer", None) or "hermes").strip() or "hermes"
            observed = _resolve_observed(cfg)
            if observed:
                client = get_honcho_client(cfg)
                client.peer(observer).conclusions_of(observed).list(size=1)
                reach = "ok"
            else:
                reach = "skipped: no observed peer"
        except Exception as e:
            reach = f"{type(e).__name__}: {e}"
    else:
        reach = "skipped: config gate not passed"
    payload["reachability"] = reach

    print(json.dumps(payload, sort_keys=True))
    return 0


def cmd_write(path=None) -> int:
    try:
        if path:
            with open(path) as fh:
                rows = json.load(fh)
        else:
            rows = json.load(sys.stdin)
        if not isinstance(rows, list) or not rows:
            print(json.dumps({"error": "input must be a non-empty JSON array"}))
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
        print(json.dumps({
            "error": f"{type(e).__name__}: {e}",
            "diag": _diag_payload(_LAST_CFG),
        }))
        return 1


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "check":
        return cmd_check()
    if cmd == "diag":
        return cmd_diag()
    if cmd == "write":
        path = None
        if "--file" in sys.argv:
            i = sys.argv.index("--file")
            if i + 1 >= len(sys.argv):
                print(json.dumps({"error": "--file needs a path"}))
                return 1
            path = sys.argv[i + 1]
        return cmd_write(path)
    print(json.dumps({
        "error": "usage: route_behavioral_to_honcho.py check|diag|write [--file PATH]"
    }))
    return 1


if __name__ == "__main__":
    sys.exit(main())
