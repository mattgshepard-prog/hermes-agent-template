#!/usr/bin/env python3
"""Let slash commands reach the dispatcher over email.

The defect
----------
`gateway/platforms/email.py` builds inbound message text like this:

    text = body
    if subject and not subject.startswith("Re:"):
        text = f"[Subject: {subject}]\\n\\n{body}"

The dispatcher recognises a slash command only when the text *begins* with
the command prefix. For a new email (any subject not starting with "Re:")
the text begins with "[Subject: ", so `/reset`, `/new` and every other
command silently fall through to the agent as ordinary conversation.

Why that is worse than it sounds
--------------------------------
Verified on beths-bot 2026-07-29 21:13 UTC. A message whose body was exactly
`/reset` produced the reply "Context reset. Ready for your next request."
while `sessions.json` still showed the same `session_id`, the same
`created_at`, and `is_fresh_reset: false`. The agent did not reset anything;
it inferred what a confirmation sounds like and wrote one. The user is told
the command worked and has no signal that it did not.

For an email-only client bot this means there is no working session control
at all, and the failure is invisible from the outside.

The fix
-------
Skip the subject prefix when the body already starts with the command
prefix. Commands then arrive exactly as they do on Telegram, and ordinary
mail is untouched because ordinary mail does not begin with "/".

Why this lives here
-------------------
`email.py` ships inside the hermes-agent package in the image, not in this
template repo, so there is no source file to commit a change to. This patch
is applied at boot, before the gateway starts.

Safety
------
- Idempotent: re-running is a no-op.
- Anchored: it edits one exact line. If an upgrade changes that line, the
  anchor stops matching, the patch declines to act, and it says so. It will
  never partially rewrite a file it no longer recognises.
- Non-fatal: any failure logs and exits 0, so a bot never fails to boot
  because of this.
"""

import sys

TARGET = "/opt/hermes-agent/gateway/platforms/email.py"

ANCHOR = '        if subject and not subject.startswith("Re:"):'
PATCHED = ('        if subject and not subject.startswith("Re:") '
           'and not body.startswith("/"):')

# Context that must sit directly beneath the anchor. Guards against editing a
# coincidentally identical line somewhere else in the file.
CONTEXT = '            text = f"[Subject: {subject}]\\n\\n{body}"'


def log(msg):
    print("[patch_email_slash_commands] %s" % msg, flush=True)


def main():
    try:
        with open(TARGET, encoding="utf-8") as fh:
            src = fh.read()
    except OSError as exc:
        log("SKIP: cannot read %s (%s)" % (TARGET, exc))
        return 0

    if PATCHED in src:
        log("already patched, nothing to do")
        return 0

    count = src.count(ANCHOR)
    if count != 1:
        log("SKIP: expected 1 anchor, found %d. Upstream changed; "
            "slash commands over email may be broken. Re-verify by hand." % count)
        return 0

    idx = src.index(ANCHOR)
    tail = src[idx + len(ANCHOR):]
    if CONTEXT not in tail.split("\n\n")[0]:
        log("SKIP: anchor found but the expected following line is missing. "
            "Not editing a file this patch no longer recognises.")
        return 0

    patched = src.replace(ANCHOR, PATCHED, 1)

    try:
        with open(TARGET, "w", encoding="utf-8") as fh:
            fh.write(patched)
    except OSError as exc:
        log("SKIP: cannot write %s (%s)" % (TARGET, exc))
        return 0

    # Read back rather than trusting the write.
    try:
        with open(TARGET, encoding="utf-8") as fh:
            check = fh.read()
    except OSError as exc:
        log("WARNING: wrote the patch but could not read it back (%s)" % exc)
        return 0

    if PATCHED in check and ANCHOR not in check.replace(PATCHED, ""):
        log("patched: slash commands now reach the dispatcher over email")
    else:
        log("WARNING: read-back did not confirm the patch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
