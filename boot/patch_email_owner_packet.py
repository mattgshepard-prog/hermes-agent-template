#!/usr/bin/env python3
"""patch_email_owner_packet.py -- route CRT owner packets around the agent.

Follows the shape of patch_email_slash_commands.py, patch_email_cc.py and
patch_email_correspondent_notice.py: idempotent, anchored to one exact line,
declines rather than guesses if upstream moves, never fails the boot.

WHAT AND WHY
------------
gateway/platforms/email.py `_dispatch_message` builds a MessageEvent and hands
it to the agent. For a CRT owner packet that has now failed four times running
(April, May, June, and the June retry on 2026-08-24): the model hand-built
/tmp/receipt_rows.json instead of calling the documented entry point, and twice
compacted context mid-packet and continued from a lossy self-summary.

SKILL.md said the right thing every time. Instruction is not the mechanism.
Nor is command_allowlist on its own, because a blocked command and an
uninvoked one both produce nothing. So the packet never reaches the model.

The hook is inserted immediately before `await self.handle_message(event)`,
which is:
  - AFTER the EMAIL_ALLOWED_USERS guard, so it inherits the allowlist
  - AFTER self._thread_context is set, so the reply threads correctly
  - BEFORE any agent turn exists, so there is nothing to compact

The hook itself does nothing but load
/data/.hermes/skills/bookkeeping/receipt-coding/scripts/owner_packet_dispatch.py
from the VOLUME and call it. All behaviour lives there, so iterating on the
dispatcher never requires touching the image again.

If the module is absent, unloadable, or does not claim the message, the mail
falls through to the agent exactly as before. Receipt images and ordinary mail
are untouched.

HERMES_SKIP_OWNER_PACKET_DISPATCH=1 opts out.
"""

import os
import sys

MARKER = "bailey-owner-packet-dispatch-v1"

ANCHOR = '        logger.info("[Email] New message from %s: %s", sender_addr, subject)'

BLOCK = '''        # --- bailey-owner-packet-dispatch-v1 ---
        # A CRT owner packet is handled by the toolchain, not by the agent.
        # Inserted at boot by /app/boot/patch_email_owner_packet.py.
        if os.getenv("HERMES_SKIP_OWNER_PACKET_DISPATCH", "0") != "1":
            try:
                import importlib.util as _ilu
                _opd_path = os.getenv(
                    "OWNER_PACKET_DISPATCH",
                    "/data/.hermes/skills/bookkeeping/receipt-coding"
                    "/scripts/owner_packet_dispatch.py")
                if os.path.isfile(_opd_path):
                    _spec = _ilu.spec_from_file_location(
                        "owner_packet_dispatch", _opd_path)
                    _opd = _ilu.module_from_spec(_spec)
                    _spec.loader.exec_module(_opd)
                    if await _opd.try_dispatch(
                            attachments=attachments,
                            sender_addr=sender_addr,
                            subject=subject,
                            adapter=self,
                            logger=logger):
                        return
            except Exception as _opd_exc:
                # Loading failed, so nothing was claimed. Fall through.
                logger.error("[Email] owner-packet dispatch unavailable, "
                             "falling through to the agent: %s", _opd_exc)
        # --- end bailey-owner-packet-dispatch-v1 ---
'''


def find_target():
    try:
        import gateway.platforms.email as mod
        return mod.__file__
    except Exception:
        for base in sys.path + ["/opt/hermes-agent"]:
            cand = os.path.join(base, "gateway", "platforms", "email.py")
            if os.path.isfile(cand):
                return cand
    return None


def main():
    path = find_target()
    if not path:
        print("[owner-packet-patch] email.py not found. Declining.")
        return 0

    try:
        src = open(path, encoding="utf-8").read()
    except OSError as exc:
        print("[owner-packet-patch] cannot read %s: %s. Declining." % (path, exc))
        return 0

    if MARKER in src:
        print("[owner-packet-patch] already applied. Nothing to do.")
        return 0

    n = src.count(ANCHOR)
    if n != 1:
        print("[owner-packet-patch] anchor matched %d times, expected 1. "
              "Upstream moved. Declining rather than guessing." % n)
        return 0

    if "async def _dispatch_message" not in src:
        print("[owner-packet-patch] _dispatch_message is not async on this "
              "build. The hook awaits. Declining.")
        return 0

    patched = src.replace(ANCHOR, BLOCK + ANCHOR, 1)

    import ast
    try:
        ast.parse(patched)
    except SyntaxError as exc:
        print("[owner-packet-patch] patched source does not parse (%s). "
              "Declining, nothing written." % exc)
        return 0

    bak = path + ".bak-pre-owner-packet-v1"
    try:
        if not os.path.exists(bak):
            with open(bak, "w", encoding="utf-8") as fh:
                fh.write(src)
        tmp = path + ".tmp-owner-packet"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(patched)
        os.replace(tmp, path)
    except OSError as exc:
        print("[owner-packet-patch] write failed: %s. Declining." % exc)
        return 0

    # Read back independently. A write call's own return is not confirmation.
    try:
        after = open(path, encoding="utf-8").read()
    except OSError as exc:
        print("[owner-packet-patch] read-back failed: %s" % exc)
        return 0
    if MARKER not in after or after.count(ANCHOR) != 1:
        print("[owner-packet-patch] read-back does not show the hook. "
              "Something else is writing this file.")
        return 0

    print("[owner-packet-patch] applied to %s (backup %s)"
          % (path, os.path.basename(bak)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never block the boot
        print("[owner-packet-patch] unexpected error, declining: %s" % exc)
        sys.exit(0)
