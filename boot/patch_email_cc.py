#!/usr/bin/env python3
"""Teach the vendored email adapter to read Cc inbound and preserve it on reply.

WHY THIS IS A BOOT PATCH AND NOT A COMMIT
-----------------------------------------
gateway/platforms/email.py ships inside the hermes-agent package, cloned at
image build from HERMES_REF (v2026.6.19). It is not tracked in this repo --
`git ls-tree` returns zero gateway/ files. Same situation as commit 4888748
(email slash commands), and the same answer: an anchored, idempotent patch
applied at boot before the gateway starts.

WHAT IT FIXES
-------------
The adapter has zero Cc handling. grep -c 'Cc' returns 0 matches in any
recipient context. Consequences observed live on 2026-08-17:

  - Matt forwarded a thread to Bailey with Amy on Cc.
  - Bailey replied To: Matt only. Amy never saw it.
  - Every thread collapses back to Matt as a manual relay hub.

WHAT IT DOES NOT FIX
--------------------
This does NOT let the agent choose a recipient. _dispatch_message hardcodes
chat_id = sender_addr, and send() passes that straight through as to_addr.
"Bailey, send this to Amy" remains structurally impossible after this patch.
That is a separate capability -- see install_outbound_send.py.

SAFETY POSTURE
--------------
OFF BY DEFAULT. The injected code is inert unless EMAIL_PRESERVE_CC=1 is set
in the process environment. Preserving Cc means the bot answers everyone the
sender copied, which on a client bot is a disclosure risk. Opt in per bot.

Bailey's own address and the To address are always filtered out of the Cc
list, so she cannot copy herself into a loop or double-send to the recipient.

ANCHORS (all verified live on Bailey 2026-08-17, v2026.6.19)
------------------------------------------------------------
  line 503  in_reply_to = msg.get("In-Reply-To", "")          x1
  line 518  "in_reply_to": in_reply_to,                        x1
  line 587  "message_id": msg_data["message_id"],              x1
  lines 641/755/835  ctx = self._thread_context.get(to_addr, {})   x3

Each anchor's occurrence count is asserted before any write. If an upstream
bump changes a count, this patcher declines to act and leaves the file alone
rather than guessing. Exit code stays 0 on decline: a refused patch must not
block gateway boot.

Set HERMES_SKIP_EMAIL_CC_PATCH=1 to skip entirely.
"""

import os
import shutil
import sys

TARGET = "/opt/hermes-agent/gateway/platforms/email.py"
MARKER = "# --- hermes-cc-v1 ---"

# (anchor text, expected occurrence count, replacement text)
PATCHES = [
    (
        '                    in_reply_to = msg.get("In-Reply-To", "")\n',
        1,
        '                    in_reply_to = msg.get("In-Reply-To", "")\n'
        '                    cc_raw = msg.get("Cc", "") or ""  ' + MARKER + '\n',
    ),
    (
        '                        "in_reply_to": in_reply_to,\n',
        1,
        '                        "in_reply_to": in_reply_to,\n'
        '                        "cc": cc_raw,  ' + MARKER + '\n',
    ),
    (
        '            "message_id": msg_data["message_id"],\n',
        1,
        '            "message_id": msg_data["message_id"],\n'
        '            "cc": msg_data.get("cc", ""),  ' + MARKER + '\n',
    ),
    (
        '        ctx = self._thread_context.get(to_addr, {})\n',
        3,
        '        ctx = self._thread_context.get(to_addr, {})\n'
        '        ' + MARKER + '\n'
        '        if os.getenv("EMAIL_PRESERVE_CC", "0") == "1":\n'
        '            _cc_raw = ctx.get("cc", "") or ""\n'
        '            _cc_out = []\n'
        '            for _part in _cc_raw.split(","):\n'
        '                _a = _extract_email_address(_part).strip().lower()\n'
        '                if not _a:\n'
        '                    continue\n'
        '                if _a == (self._address or "").lower():\n'
        '                    continue\n'
        '                if _a == (to_addr or "").strip().lower():\n'
        '                    continue\n'
        '                if _a in _cc_out:\n'
        '                    continue\n'
        '                _cc_out.append(_a)\n'
        '            if _cc_out:\n'
        '                msg["Cc"] = ", ".join(_cc_out)\n'
        '                logger.info("[Email] Preserving Cc: %s", ", ".join(_cc_out))\n'
        '        # --- end hermes-cc-v1 ---\n',
    ),
]


def main() -> int:
    if os.getenv("HERMES_SKIP_EMAIL_CC_PATCH", "0") == "1":
        print("[patch_email_cc] skipped (HERMES_SKIP_EMAIL_CC_PATCH=1)")
        return 0

    if not os.path.exists(TARGET):
        print("[patch_email_cc] DECLINED: target not found at %s" % TARGET)
        return 0

    with open(TARGET, "r", encoding="utf-8") as fh:
        src = fh.read()

    # Idempotence: already applied on a previous boot of this same image.
    if MARKER in src:
        print("[patch_email_cc] already applied, no action")
        return 0

    # The Cc block calls _extract_email_address and logger. Both must exist in
    # this module or the injected code would NameError at send time -- a
    # runtime break that would look like an SMTP fault. Refuse instead.
    for required in ("def _extract_email_address", "logger = "):
        if required not in src:
            print("[patch_email_cc] DECLINED: missing prerequisite %r" % required)
            return 0

    # os is used by the injected guard. The module imports it at top level
    # (verified: os.getenv is already used for EMAIL_ALLOWED_USERS), but assert
    # rather than assume. Line-based, so a first-line `import os` still matches.
    _lines = [ln.strip() for ln in src.splitlines()]
    if not any(ln == "import os" or ln.startswith("import os,") for ln in _lines):
        print("[patch_email_cc] DECLINED: module does not import os")
        return 0

    # Verify EVERY anchor at its expected count BEFORE writing anything.
    # A partial application is worse than none: it would leave the fetch path
    # producing a cc key that the send path never reads, or vice versa.
    for anchor, expected, _ in PATCHES:
        found = src.count(anchor)
        if found != expected:
            print(
                "[patch_email_cc] DECLINED: anchor count mismatch "
                "(expected %d, found %d) for: %s"
                % (expected, found, anchor.strip()[:70])
            )
            return 0

    patched = src
    for anchor, expected, replacement in PATCHES:
        patched = patched.replace(anchor, replacement, expected)

    # Syntax gate. A broken email.py takes the whole gateway down, so prove the
    # result parses before it reaches disk.
    try:
        compile(patched, TARGET, "exec")
    except SyntaxError as exc:
        print("[patch_email_cc] DECLINED: patched source failed to compile: %s" % exc)
        return 0

    backup = TARGET + ".bak-pre-cc-v1"
    if not os.path.exists(backup):
        shutil.copy2(TARGET, backup)

    tmp = TARGET + ".tmp-cc-v1"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(patched)
    os.replace(tmp, TARGET)

    enabled = os.getenv("EMAIL_PRESERVE_CC", "0") == "1"
    print(
        "[patch_email_cc] applied (%d sites). EMAIL_PRESERVE_CC=%s -> Cc handling %s"
        % (
            sum(p[1] for p in PATCHES),
            os.getenv("EMAIL_PRESERVE_CC", "0"),
            "ACTIVE" if enabled else "inert",
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
