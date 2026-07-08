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
ADMIN_PASSWORD COMPOSIO_API_KEY COMPOSIO_USER_ID"

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

exec python /app/server.py
