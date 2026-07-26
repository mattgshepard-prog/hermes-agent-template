#!/bin/bash
set -e

# Mirror dashboard-ref-only's startup: create every directory hermes expects
# and seed a default config.yaml if the volume is empty. Without these,
# `hermes dashboard` endpoints that hit logs/, sessions/, cron/, etc. can fail
# with opaque errors even though no auth is actually involved.
mkdir -p /data/.hermes/cron /data/.hermes/sessions /data/.hermes/logs \
         /data/.hermes/memories /data/.hermes/skills /data/.hermes/pairing \
         /data/.hermes/hooks /data/.hermes/image_cache /data/.hermes/audio_cache \
         /data/.hermes/workspace /data/.hermes/skins /data/.hermes/plans \
         /data/.hermes/home

# Stamp the install method as "docker" so hermes treats this as an immutable
# container image, not a pip checkout. hermes's detect_install_method() reads
# $HERMES_HOME/.install_method FIRST (before any .git / pip fallback). Without
# this stamp the template falls through to "pip" — because the Dockerfile strips
# /opt/hermes-agent/.git — and the dashboard's "Update Hermes" button then runs
# a real `hermes update` (PyPI pip-upgrade) INSIDE the running container. That
# upgrade is ephemeral (reverts on the next redeploy) and can desync the Python
# package from the image's pre-built web_dist/ui-tui bundles. Stamping "docker"
# makes that button correctly refuse with "pull a fresh image / redeploy", which
# matches the real upgrade path here (bump HERMES_REF in Railway + redeploy).
# Written unconditionally each boot so it stays correct and self-heals.
printf 'docker\n' > /data/.hermes/.install_method

if [ ! -f /data/.hermes/config.yaml ] && [ -f /opt/hermes-agent/cli-config.yaml.example ]; then
  cp /opt/hermes-agent/cli-config.yaml.example /data/.hermes/config.yaml
fi

[ ! -f /data/.hermes/.env ] && touch /data/.hermes/.env

# ── Seed /data/.hermes/.env from Railway variables on every boot ───────────
# Root cause (found 2026-07-08 on Garry CoS): server.py's GatewayManager.start()
# builds the gateway subprocess env as `{**os.environ}` then `.update(read_env(
# ENV_FILE))` — so .env ALWAYS wins over Railway variables, even when .env holds
# a stale or wrong value and Railway has the correct one. A bad LLM_MODEL value
# and a missing OPENROUTER_API_KEY sat in .env for days, silently shadowing
# working Railway config, and the resulting HTTP 400/402s surfaced as a vague
# "Provider authentication failed" alert instead of pointing at .env.
#
# Fix: before the gateway ever reads .env, overwrite each of these keys in .env
# from the container's actual environment (i.e. Railway variables), but only
# when Railway has a non-empty value for that key. If Railway has nothing for a
# key, whatever is already in .env is left alone — this preserves OAuth tokens
# and any value hermes itself writes to .env at runtime (xai-oauth, qwen-oauth,
# etc.), matching the "preserve user-managed state" intent already documented
# in write_config_yaml(). Mirrors the HERMES_AUTH_JSON_BOOTSTRAP idiom below:
# Railway/env is the source of truth, .env is the persisted mirror.
#
# Keep this list in sync with server.py's ENV_VARS registry if that changes.
HERMES_ENV_KEYS="LLM_MODEL OPENROUTER_API_KEY DEEPSEEK_API_KEY DASHSCOPE_API_KEY \
GLM_API_KEY KIMI_API_KEY MINIMAX_API_KEY HF_TOKEN NVIDIA_API_KEY ARCEEAI_API_KEY \
STEPFUN_API_KEY GEMINI_API_KEY NOVITA_API_KEY FIREWORKS_API_KEY ANTHROPIC_API_KEY \
XAI_API_KEY AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION \
COPILOT_GITHUB_TOKEN GMI_API_KEY OPENCODE_ZEN_API_KEY OPENCODE_GO_API_KEY \
KILOCODE_API_KEY OLLAMA_API_KEY AZURE_FOUNDRY_API_KEY AZURE_FOUNDRY_BASE_URL \
CUSTOM_PROVIDER_API_KEY CUSTOM_PROVIDER_BASE_URL CUSTOM_PROVIDER_NAME \
PARALLEL_API_KEY FIRECRAWL_API_KEY TAVILY_API_KEY FAL_KEY BROWSERBASE_API_KEY \
BROWSERBASE_PROJECT_ID GITHUB_TOKEN VOICE_TOOLS_OPENAI_KEY HONCHO_API_KEY \
TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_USERS DISCORD_BOT_TOKEN DISCORD_ALLOWED_USERS \
SLACK_BOT_TOKEN SLACK_APP_TOKEN WHATSAPP_ENABLED EMAIL_ADDRESS EMAIL_PASSWORD \
EMAIL_IMAP_HOST EMAIL_SMTP_HOST MATTERMOST_URL MATTERMOST_TOKEN MATRIX_HOMESERVER \
MATRIX_ACCESS_TOKEN MATRIX_USER_ID GATEWAY_ALLOW_ALL_USERS ADMIN_USERNAME \
ADMIN_PASSWORD COMPOSIO_API_KEY COMPOSIO_USER_ID NOTION_TOKEN DIGEST_EMAIL_TO"

seeded_count=0
for key in $HERMES_ENV_KEYS; do
  # Indirect expansion (POSIX-safe, avoids bash-only ${!key}).
  value="$(eval "printf '%s' \"\${$key:-}\"")"
  if [ -n "$value" ]; then
    grep -v "^${key}=" /data/.hermes/.env > /data/.hermes/.env.tmp 2>/dev/null || true
    mv /data/.hermes/.env.tmp /data/.hermes/.env
    printf '%s=%s\n' "$key" "$value" >> /data/.hermes/.env
    seeded_count=$((seeded_count + 1))
  fi
done
echo "[start.sh] Seeded ${seeded_count} key(s) into /data/.hermes/.env from Railway variables."

# Bootstrap OAuth tokens from env var (e.g. xAI Grok SuperGrok).
# Set HERMES_AUTH_JSON_BOOTSTRAP to the contents of a locally-generated
# ~/.hermes/auth.json. Written only once — subsequent token refreshes update
# the file in place on the persistent volume.
if [ ! -f /data/.hermes/auth.json ] && [ -n "${HERMES_AUTH_JSON_BOOTSTRAP}" ]; then
  printf '%s' "${HERMES_AUTH_JSON_BOOTSTRAP}" > /data/.hermes/auth.json
  chmod 600 /data/.hermes/auth.json
fi

# Clear any stale gateway PID file left over from the previous container.
# `hermes gateway` writes /data/.hermes/gateway.pid on start but does not
# remove it on SIGTERM. Since /data is a persistent volume, the file
# survives container restarts and causes every subsequent boot to exit with
# "ERROR gateway.run: PID file race lost to another gateway instance".
# No hermes process can be running at this point (we're pre-exec in a fresh
# container), so removing the file unconditionally is safe.
rm -f /data/.hermes/gateway.pid

# ── Pre-warm the Notion MCP server package (A2: awkoy/notion-mcp-server) ────
# Garry reaches Notion via a stdio MCP server that hermes spawns on demand with
# `npx -y notion-mcp-server`. On a cold container, npx would fetch the package
# (hundreds of transitive deps) on the FIRST Notion call — which already timed
# out at >60s during setup. Pre-warming the npm cache at boot turns that first
# call into an instant spawn from cache instead of a live download.
#
# Backgrounded and fully failure-tolerant: the subshell disables `set -e` (via
# running in its own `bash -c`) and always exits 0, so a slow mirror or a
# transient npm error can NEVER block or fail gateway boot. Mirrors the
# "Composio never blocks boot" discipline already used for the Tool Router hook.
# Idempotent: npx/npm cache is on the persistent /data-independent image layer
# per-boot, so this is cheap on warm boots and only does real work when cold.
(
  bash -c 'npx -y notion-mcp-server --help >/tmp/notion_mcp_prewarm.log 2>&1 || true' &
) 2>/dev/null || true
echo "[start.sh] Notion MCP pre-warm started in background (non-blocking)."


# ── Seed bundled skills onto the volume on boot (non-destructive) ──────────
# Phase 1 self-learning loop and any future bundled skills ship inside the
# image at /app/templates/bundled_skills/ (COPYd by the Dockerfile). Skills
# must live on the persistent volume at /data/.hermes/skills/ for hermes to
# load them. This copies each bundled skill into place ONLY if it is not
# already present on the volume, so it never clobbers a skill that has been
# edited on the volume (e.g. by the self-learning loop itself). New bundled
# skills appear automatically on the next boot after a push; existing ones are
# left untouched. Fully failure-tolerant: never blocks gateway boot.
#
# To force a bundled skill to overwrite the volume copy (e.g. shipping a fix),
# bump a version marker by deleting that skill dir on the volume, or set
# HERMES_RESEED_SKILLS=1 in Railway to overwrite all bundled skills this boot.
BUNDLED_SKILLS_DIR="/app/templates/bundled_skills"
if [ -d "$BUNDLED_SKILLS_DIR" ]; then
  seeded_skills=0
  for group in "$BUNDLED_SKILLS_DIR"/*/; do
    [ -d "$group" ] || continue
    for skill in "$group"*/; do
      [ -d "$skill" ] || continue
      rel="${skill#$BUNDLED_SKILLS_DIR/}"
      dest="/data/.hermes/skills/${rel%/}"
      if [ ! -d "$dest" ] || [ "${HERMES_RESEED_SKILLS:-0}" = "1" ]; then
        # Remove any existing dest first. Without this, `cp -r src dest` when
        # dest already exists copies src INTO dest (dest/src/...) instead of
        # overwriting, nesting the skill one level deep and leaving the stale
        # copy at the top level (found 2026-07-13 during the v2 reseed).
        rm -rf "$dest"
        mkdir -p "$(dirname "$dest")"
        cp -r "$skill" "$dest" 2>/dev/null && seeded_skills=$((seeded_skills + 1)) || true
      fi
    done
  done
  echo "[start.sh] Seeded ${seeded_skills} bundled skill(s) onto the volume."
fi

# ── Wire Honcho as the memory provider (non-destructive) ────────────────────
# Root cause (found 2026-07-15 on Garry CoS): HONCHO_API_KEY was seeded into
# .env (honcho-wire-v1) but config.yaml still had memory.provider: '' — and
# the honcho plugin only loads (and only exposes its memory tools to the
# agent) when memory.provider selects it. Result: the learning-ingester
# correctly reported "Honcho not configured" and parked every behavioral
# lesson at needs-approval instead of routing it to Honcho.
#
# Fix: when HONCHO_API_KEY is present and memory.provider is EMPTY, set it to
# honcho via `hermes config set` (the official atomic write path). Guards:
# - Only fires when the provider is empty, so an explicitly chosen different
#   provider (mem0, supermemory, ...) — or a deliberate empty-after-disable
#   via HERMES_SKIP_HONCHO_WIRE=1 — is never clobbered.
# - config.yaml is backed up once before the first wire.
# - Fully failure-tolerant: never blocks gateway boot.
# The honcho plugin itself resolves credentials via its config chain
# ($HERMES_HOME/honcho.json -> ~/.honcho/config.json -> env vars); with no
# honcho.json present it falls through to HONCHO_API_KEY from the
# environment, which the .env seeding above guarantees.
if [ "${HERMES_SKIP_HONCHO_WIRE:-0}" != "1" ] && [ -n "${HONCHO_API_KEY:-}" ] && [ -f /data/.hermes/config.yaml ]; then
  current_provider="$(python - <<'PYEOF' 2>/dev/null || true
import yaml
try:
    cfg = yaml.safe_load(open("/data/.hermes/config.yaml", encoding="utf-8")) or {}
    print(((cfg.get("memory") or {}).get("provider") or "").strip())
except Exception:
    print("")
PYEOF
)"
  if [ -z "$current_provider" ]; then
    if [ ! -f /data/.hermes/config.yaml.bak-pre-honcho-wire ]; then
      cp /data/.hermes/config.yaml /data/.hermes/config.yaml.bak-pre-honcho-wire 2>/dev/null || true
    fi
    if hermes config set memory.provider honcho >/dev/null 2>&1; then
      echo "[start.sh] Wired memory.provider=honcho in config.yaml (was empty; HONCHO_API_KEY present)."
    else
      echo "[start.sh] WARNING: hermes config set memory.provider honcho failed; continuing boot without Honcho."
    fi
  else
    echo "[start.sh] memory.provider already '${current_provider}'; Honcho wire skipped."
  fi
else
  echo "[start.sh] Honcho wire skipped (disabled, no HONCHO_API_KEY, or no config.yaml yet)."
fi

# ── Seed Honcho peer pin (non-destructive) ──────────────────────────────────
# Root cause (found 2026-07-16 on Garry CoS): without a honcho.json, each
# gateway platform mints its own runtime peer, splitting one operator into
# multiple Honcho identities (Matt's email peer accumulated 33 conclusions
# apart from his Telegram peer before the manual merge). The fix on Garry was
# a hand-written /data/.hermes/honcho.json pinning all runtime identities to
# the canonical Telegram peer via pinUserPeer — which a fresh volume never
# gets. This seeds it automatically for the single-operator (client bot) case:
# peerName = first ID in TELEGRAM_ALLOWED_USERS, pinUserPeer = true.
# Guards:
# - Only when HONCHO_API_KEY and TELEGRAM_ALLOWED_USERS are both present.
# - Only when /data/.hermes/honcho.json is ABSENT — an existing file (however
#   edited, e.g. Garry's hand-merged pin) is never touched.
# - HERMES_SKIP_HONCHO_PIN=1 opts out (multi-user bots want per-user peers).
# - Fully failure-tolerant: never blocks gateway boot.
# Schema per hermes plugins/memory/honcho/client.py at v2026.6.19: honcho.json
# at $HERMES_HOME resolves first; keys peerName + pinUserPeer (which wins over
# legacy pinPeerName). File written 600 like auth.json.
if [ "${HERMES_SKIP_HONCHO_PIN:-0}" != "1" ] && [ -n "${HONCHO_API_KEY:-}" ] \
   && [ -n "${TELEGRAM_ALLOWED_USERS:-}" ] && [ ! -f /data/.hermes/honcho.json ]; then
  if python - <<'PYEOF2' >/dev/null 2>&1
import json, os
peer = os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")[0].strip()
if not peer:
    raise SystemExit(1)
path = "/data/.hermes/honcho.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump({"peerName": peer, "pinUserPeer": True}, f, indent=2)
os.chmod(path, 0o600)
PYEOF2
  then
    echo "[start.sh] Seeded honcho.json peer pin (pinUserPeer=true, peer from TELEGRAM_ALLOWED_USERS)."
  else
    echo "[start.sh] WARNING: honcho.json peer pin seed failed; continuing boot."
  fi
else
  echo "[start.sh] Honcho peer pin seed skipped (present, disabled, or missing HONCHO_API_KEY/TELEGRAM_ALLOWED_USERS)."
fi

# ── Seed self-learning cron jobs (non-destructive) ──────────────────────────
# Registers Garry Learning Writer (06:00 UTC) and Garry Learning Ingester
# (10:00 UTC) in $HERMES_HOME/cron/jobs.json if absent, matched by name.
# Existing jobs are never touched, so manual edits on the volume win. Runs
# pre-gateway, so there is no lock contention with the live scheduler.
# Closes the last manual touch in the self-learning loop's cold deploy:
# Garry's live jobs were registered by hand on 2026-07-14; a fresh volume now
# gets them automatically. Failure-tolerant: never blocks gateway boot.
python /app/boot/seed_cron_jobs.py || echo "[start.sh] WARNING: cron job seed failed; continuing boot."

# -- Seed Honcho apiKey into honcho.json (every boot) -----------------------
# Root cause (confirmed 2026-07-25 on Garry CoS and Bailey): the agent's
# terminal tool spawns subprocesses with a SCRUBBED environment. Running the
# ingester's own diag from the gateway vs over SSH proves it:
#   gateway: HONCHO_API_KEY unset, TELEGRAM_ALLOWED_USERS unset,
#            HOME=/data/.hermes/home, cwd=/tmp, _HERMES_GATEWAY=1
#   ssh:     HONCHO_API_KEY len 71, TELEGRAM_ALLOWED_USERS len 10, HOME=/data
# honcho.json carries enabled/workspace/aiPeer/peerName but NO credential, and
# there is no baseUrl, so the plugin's config chain resolved api_key empty and
# every cron behavioral route failed with "no API key or base URL"
# (Garry 07-23 06:01, 07-23 10:00, 07-25 10:01; Bailey 07-25 10:00).
#
# Fix: write the key into honcho.json, which the plugin reads BEFORE the
# environment. Schema per plugins/memory/honcho/client.py at v2026.6.19:
#   api_key = host_block.get("apiKey") or raw.get("apiKey")
#             or os.environ.get("HONCHO_API_KEY")
# Written at ROOT level so it serves every host block without guessing the
# host name, and never overrides a host-specific apiKey if one is ever set.
#
# Rewritten on EVERY boot from HONCHO_API_KEY so the Railway variable stays the
# single source of truth for rotation. All other fields are preserved. Only
# touches an EXISTING honcho.json, so it can never create a stub file that
# would permanently block the peer-pin seed above.
# HERMES_SKIP_HONCHO_KEY_SEED=1 opts out. Never blocks gateway boot.
if [ "${HERMES_SKIP_HONCHO_KEY_SEED:-0}" != "1" ] && [ -n "${HONCHO_API_KEY:-}" ] \
   && [ -f /data/.hermes/honcho.json ]; then
  if python - <<'PYEOF3' >/dev/null 2>&1
import json, os
path = "/data/.hermes/honcho.json"
key = os.environ.get("HONCHO_API_KEY", "").strip()
if not key:
    raise SystemExit(1)
with open(path, encoding="utf-8") as f:
    data = json.load(f) or {}
if not isinstance(data, dict):
    raise SystemExit(1)
if data.get("apiKey") != key:
    data["apiKey"] = key
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
PYEOF3
  then
    echo "[start.sh] Honcho apiKey present in honcho.json (config chain is now env-independent)."
  else
    echo "[start.sh] WARNING: honcho.json apiKey seed failed; continuing boot."
  fi
else
  echo "[start.sh] Honcho apiKey seed skipped (disabled, no HONCHO_API_KEY, or no honcho.json)."
fi

exec python /app/server.py
