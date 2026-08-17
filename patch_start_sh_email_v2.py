#!/usr/bin/env python3
"""Wire the two new email boot scripts into start.sh, and register their vars.

Run from the repo root with bailey-release checked out:

    cd "C:\\Data\\AI Training\\Garry AI EA\\hermes-agent-template"
    git checkout bailey-release
    python patch_start_sh_email_v2.py

TWO SEPARATE CHANGES, BOTH REQUIRED
-----------------------------------
1. HERMES_ENV_KEYS gains EMAIL_PRESERVE_CC, EMAIL_OUTBOUND_ALLOWED and
   EMAIL_SMTP_PORT.

   This is not optional bookkeeping. A Railway variable whose key is absent
   from HERMES_ENV_KEYS is SILENTLY DROPPED and never reaches the process.
   That trap is live on this stack right now: HONCHO_WORKSPACE was set on
   Bailey for twelve days and read as unset, because start.sh at 5205fa9 did
   not list it. Precedent before that: DIGEST_EMAIL_TO on 2026-07-26.

   If EMAIL_OUTBOUND_ALLOWED is dropped, send_to.py sees an empty allowlist
   and refuses every send. Fail-closed, so the failure is loud rather than
   dangerous, but it would look like a broken tool.

2. Both boot scripts are invoked before the gateway starts, alongside the
   existing patch_email_slash_commands.py.

The script asserts every anchor before writing and refuses as a whole on any
mismatch. It is idempotent: re-running is a no-op.
"""

import os
import sys

START = "start.sh"
MARKER = "email-cc-and-outbound-v1"

ENV_ANCHOR = "EMAIL_IMAP_HOST EMAIL_SMTP_HOST EMAIL_ALLOWED_USERS EMAIL_HOME_ADDRESS \\\n"
ENV_REPLACE = (
    "EMAIL_IMAP_HOST EMAIL_SMTP_HOST EMAIL_ALLOWED_USERS EMAIL_HOME_ADDRESS \\\n"
    "EMAIL_SMTP_PORT EMAIL_PRESERVE_CC EMAIL_OUTBOUND_ALLOWED \\\n"
)

WIRE_ANCHOR = (
    "  echo \"[start.sh] Email slash-command patch skipped "
    "(HERMES_SKIP_EMAIL_SLASH_PATCH=1).\"\n"
    "fi\n"
)
WIRE_REPLACE = (
    WIRE_ANCHOR
    + "\n"
    "# \u2500\u2500 Cc preservation and outbound send (" + MARKER + ") \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "# patch_email_cc.py teaches the vendored adapter to read Cc inbound and\n"
    "# carry it on the reply. Inert unless EMAIL_PRESERVE_CC=1.\n"
    "#\n"
    "# install_outbound_send.py writes /data/.hermes/tools/send_to.py, the only\n"
    "# path by which this agent can mail an address it did not receive mail\n"
    "# from. Its allowlist FAILS CLOSED: empty or unset means send to nobody.\n"
    "# Note this is the opposite of EMAIL_ALLOWED_USERS, which fails OPEN.\n"
    "#\n"
    "# Both decline rather than break if upstream moves. Both are `|| true` so\n"
    "# a refusal cannot block gateway boot.\n"
    "if [ \"${HERMES_SKIP_EMAIL_CC_PATCH:-0}\" != \"1\" ]; then\n"
    "  python /app/boot/patch_email_cc.py || true\n"
    "fi\n"
    "\n"
    "if [ \"${HERMES_SKIP_OUTBOUND_SEND:-0}\" != \"1\" ]; then\n"
    "  python /app/boot/install_outbound_send.py || true\n"
    "fi\n"
)


def fail(msg):
    print("REFUSED: " + msg)
    print("No changes written.")
    return 1


def main():
    if not os.path.exists(START):
        return fail("start.sh not found. Run from the repo root.")

    # start.sh is CRLF in the Windows working tree (git normalizes to LF in the
    # blob via autocrlf). Anchors are written LF-only, so normalize for matching
    # and restore the original convention on write. Getting this wrong is how
    # the first run of this script refused: 662 CRLF, 0 bare LF, no anchor hit.
    raw = open(START, "rb").read()
    crlf = raw.count(b"\r\n") > 0
    src = raw.replace(b"\r\n", b"\n").decode("utf-8")

    if MARKER in src:
        print("Already applied (%s present). No action." % MARKER)
        return 0

    # Assert both anchors at exactly one occurrence BEFORE touching anything.
    for label, anchor in (("HERMES_ENV_KEYS", ENV_ANCHOR), ("boot wiring", WIRE_ANCHOR)):
        n = src.count(anchor)
        if n != 1:
            return fail(
                "%s anchor found %d times, expected exactly 1.\n"
                "  Anchor: %r\n"
                "  start.sh has drifted from the version this was written "
                "against. Re-read it and re-cut the anchor rather than "
                "loosening this check." % (label, n, anchor.strip()[:80])
            )

    # The two boot scripts must be in place, or start.sh would reference files
    # that do not exist in the image.
    for f in ("boot/patch_email_cc.py", "boot/install_outbound_send.py"):
        if not os.path.exists(f):
            return fail("%s is missing from the repo. Copy both boot scripts "
                        "in before running this." % f)

    out = src.replace(ENV_ANCHOR, ENV_REPLACE, 1)
    out = out.replace(WIRE_ANCHOR, WIRE_REPLACE, 1)

    # Structural check, not a count check. The first cut of this script used
    # `if`/`fi` counts and PASSED while producing broken shell: it inserted a
    # `fi` mid-block, which orphaned the slash-patch's `else` onto the new
    # outbound `if`. It parsed cleanly and was semantically wrong. Counts
    # cannot see that. Verify the actual block shape instead.
    if "  python /app/boot/patch_email_slash_commands.py || true\nfi\n" in out:
        return fail("the slash-patch block was truncated: its else branch is "
                    "gone. Refusing.")
    for opener, body in (
        ("HERMES_SKIP_EMAIL_CC_PATCH", "  python /app/boot/patch_email_cc.py || true\nfi\n"),
        ("HERMES_SKIP_OUTBOUND_SEND", "  python /app/boot/install_outbound_send.py || true\nfi\n"),
    ):
        block = "if [ \"${%s:-0}\" != \"1\" ]; then\n%s" % (opener, body)
        if out.count(block) != 1:
            return fail("new %s block is not well-formed (expected exactly one "
                        "if/body/fi). Refusing." % opener)
    if out.count("\nfi\n") != src.count("\nfi\n") + 2:
        return fail("fi-count did not increase by exactly 2. Refusing rather "
                    "than shipping unbalanced shell.")

    out_bytes = out.encode("utf-8")
    if crlf:
        out_bytes = out_bytes.replace(b"\n", b"\r\n")

    with open(START + ".bak-pre-" + MARKER, "wb") as fh:
        fh.write(raw)
    with open(START, "wb") as fh:
        fh.write(out_bytes)

    print("Applied. (line endings: %s, preserved)" % ("CRLF" if crlf else "LF"))
    print("  HERMES_ENV_KEYS += EMAIL_SMTP_PORT EMAIL_PRESERVE_CC EMAIL_OUTBOUND_ALLOWED")
    print("  wired boot/patch_email_cc.py")
    print("  wired boot/install_outbound_send.py")
    print("  backup: %s.bak-pre-%s" % (START, MARKER))
    print("")
    print("NEXT: verify the shell parses before committing. WSL bash is not")
    print("installed on this machine, so use git-bash:")
    print("  \"C:\\\\Program Files\\\\Git\\\\bin\\\\bash.exe\" -n start.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
