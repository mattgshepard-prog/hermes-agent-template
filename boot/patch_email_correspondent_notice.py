#!/usr/bin/env python3
"""MECHANISM 3: a dropped correspondent reply must be loud, not silent.

THE FAILURE THIS CLOSES
-----------------------
Bailey emails Eric Ross at CRT with a question about an owner statement and
copies Matt and Amy. Eric hits Reply instead of Reply All. His answer arrives
addressed to Bailey alone.

Eric is not on EMAIL_ALLOWED_USERS, so the adapter drops him at dispatch and
returns. That is correct and must not change: a property manager cannot be
allowed to drive an agent that reads company financial records. But the drop is
currently SILENT, logged at debug level only. The answer to Bailey's question
disappears and nobody knows it arrived.

So the risk is not Bailey acting unsupervised. It is the answer being lost.

WHAT THIS DOES
--------------
At the existing drop site, before returning, check whether the sender is a
known correspondent. If so, email the principals a notice carrying the sender,
subject and body, and record it in the outbound audit log.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not dispatch the message. No MessageEvent is created, no agent turn
runs, no thread context is built. Bailey never sees the content and never acts
on it. The correspondent's words reach human eyes only. Authority still flows
only from a principal, and the authorisation step remains Matt or Amy
forwarding the answer to Bailey with an instruction.

That is what keeps the injection surface at zero. Text written by a third party
is never read by the agent, so it cannot instruct the agent.

WHY IT SENDS DIRECTLY RATHER THAN VIA send_to.py
------------------------------------------------
Two reasons, both load-bearing.

1. send_to.py's action-claim guard would refuse a notice whose QUOTED content
   happened to contain a phrase like "will add". The quoted words are Eric's,
   not Bailey's claim, so refusing would suppress exactly the notice we need.

2. If notices went through a flag on send_to.py, that flag would be reachable
   by Bailey, since the send command is on the permanent allowlist. She could
   then send guard-free mail to Amy. Keeping the notice inside the gateway
   process, fired by an inbound event, means there is no argv she can compose
   that reaches it.

The single audit log is preserved: this appends to the same file with status
NOTICE_SENT, so one file still holds every outbound message.

SAFETY PROPERTIES
-----------------
- Wrapped end to end in try/except. A notice failure can never break the
  receive loop or the drop itself. The return still happens.
- Never fires for principals (they are dispatched normally, never reach here)
  or automated senders (filtered earlier in the same function).
- Sends only to principals, computed as the outbound allowlist minus the
  correspondents file, so it cannot email a third party.
- No reply is sent to the correspondent, so there is no loop.
- Idempotent install, guarded by HERMES_SKIP_CORRESPONDENT_NOTICE=1.
- Backs up email.py once before the first write.
"""

import os
import shutil
import sys

TARGET = "/opt/hermes-agent/gateway/platforms/email.py"
BACKUP = TARGET + ".bak-pre-correspondent-notice-v1"
MARKER = "correspondent-notice-v1"

ANCHOR = """            if sender_addr.lower() not in allowed:
                logger.debug("[Email] Dropping non-allowlisted sender at dispatch: %s", sender_addr)
                return"""

REPLACEMENT = '''            if sender_addr.lower() not in allowed:
                logger.debug("[Email] Dropping non-allowlisted sender at dispatch: %s", sender_addr)
                # correspondent-notice-v1: the drop stands, but if this sender
                # is a known correspondent, tell the principals rather than
                # losing the message silently. Never dispatches.
                try:
                    _notify_correspondent_dropped(
                        sender_addr,
                        msg_data.get("subject", ""),
                        msg_data.get("body", ""),
                        self._address,
                    )
                except Exception as _exc:  # never break the receive loop
                    logger.warning("[Email] correspondent notice failed: %s", _exc)
                return'''

HELPER = '''

# --- correspondent-notice-v1 ------------------------------------------------

def _cn_load_correspondents():
    """Addresses Bailey may ask questions of. Fail-closed on any error."""
    import json
    try:
        with open("/data/.hermes/correspondents.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)
        out = {}
        for row in data.get("correspondents", []):
            addr = (row.get("address") or "").strip().lower()
            if addr:
                out[addr] = row
        return out
    except Exception:
        return {}


def _cn_audit(status, to_addr, subject, detail=""):
    from datetime import datetime, timezone
    try:
        path = "/data/.hermes/logs/outbound_send_audit.log"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s\\t%s\\tto=%s\\tsubject=%s\\t%s\\n"
                     % (datetime.now(timezone.utc).isoformat(), status,
                        to_addr, subject, detail))
    except Exception:
        pass


def _notify_correspondent_dropped(sender_addr, subject, body, from_address):
    """Tell the principals that a correspondent wrote in and was NOT processed.

    Does not dispatch, does not reply to the sender, does not let the content
    reach the agent. Human eyes only.
    """
    import smtplib
    import uuid
    from email.mime.text import MIMEText
    from email.utils import formatdate

    correspondents = _cn_load_correspondents()
    row = correspondents.get((sender_addr or "").strip().lower())
    if not row:
        return  # not a known correspondent; ordinary silent drop stands

    raw = os.getenv("EMAIL_OUTBOUND_ALLOWED", "").strip()
    allowed = {a.strip().lower() for a in raw.split(",") if a.strip()}
    principals = sorted(allowed - set(correspondents.keys()))
    if not principals:
        _cn_audit("NOTICE_NO_PRINCIPAL", sender_addr, subject, "nobody to notify")
        return

    password = os.getenv("EMAIL_PASSWORD", "")
    host = os.getenv("EMAIL_SMTP_HOST", "")
    port = int(os.getenv("EMAIL_SMTP_PORT", "587") or "587")
    if not (from_address and password and host):
        _cn_audit("NOTICE_NO_TRANSPORT", sender_addr, subject, "smtp settings missing")
        return

    who = "%s (%s)" % (row.get("name", sender_addr), row.get("organization", ""))
    text = (
        "Automated notice. No action has been taken.\\n\\n"
        "%s replied to Bailey directly, without Matt or Amy on the message.\\n"
        "Bailey did NOT read this and did NOT act on it. She cannot: a\\n"
        "correspondent has no authority to instruct her. It is reproduced\\n"
        "below so the answer is not lost.\\n\\n"
        "If you want Bailey to act on it, forward it to her with an\\n"
        "instruction. That forward is the authorisation.\\n\\n"
        "----------------------------------------------------------------\\n"
        "From:    %s\\n"
        "Address: %s\\n"
        "Subject: %s\\n"
        "----------------------------------------------------------------\\n\\n"
        "%s\\n"
    ) % (who, who, sender_addr, subject, (body or "").strip())

    msg = MIMEText(text, "plain", "utf-8")
    msg["From"] = from_address
    msg["To"] = ", ".join(principals)
    msg["Subject"] = "[Bailey] %s replied without you copied: %s" % (
        row.get("name", sender_addr), subject or "(no subject)")
    msg["Date"] = formatdate(localtime=True)
    msg_id = "<hermes-notice-%s@%s>" % (uuid.uuid4().hex[:12], from_address.split("@")[1])
    msg["Message-ID"] = msg_id

    _cn_audit("NOTICE_SENDING", ",".join(principals), subject,
              "correspondent=%s msgid=%s" % (sender_addr, msg_id))

    smtp = smtplib.SMTP(host, port, timeout=30)
    try:
        smtp.starttls()
        smtp.login(from_address, password)
        smtp.send_message(msg)
    finally:
        try:
            smtp.quit()
        except Exception:
            smtp.close()

    _cn_audit("NOTICE_SENT", ",".join(principals), subject,
              "correspondent=%s msgid=%s" % (sender_addr, msg_id))
    logger.info("[Email] Correspondent %s dropped at dispatch; principals notified.",
                sender_addr)
'''


def main():
    if os.getenv("HERMES_SKIP_CORRESPONDENT_NOTICE", "0") == "1":
        print("[correspondent-notice] skipped by env flag")
        return 0

    if not os.path.exists(TARGET):
        print("[correspondent-notice] target missing, skipping: %s" % TARGET)
        return 0

    with open(TARGET, "r", encoding="utf-8") as fh:
        src = fh.read()

    if MARKER in src:
        print("[correspondent-notice] already applied")
        return 0

    if ANCHOR not in src:
        print("[correspondent-notice] WARNING: anchor not found, adapter left "
              "unchanged. The silent drop stands. Do not assume this applied.")
        return 0

    if not os.path.exists(BACKUP):
        shutil.copy2(TARGET, BACKUP)

    patched = src.replace(ANCHOR, REPLACEMENT, 1) + HELPER

    import ast
    try:
        ast.parse(patched)
    except SyntaxError as exc:
        print("[correspondent-notice] FATAL: patched file does not parse (%s). "
              "Adapter left unchanged." % exc)
        return 1

    tmp = TARGET + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(patched)
    os.replace(tmp, TARGET)
    print("[correspondent-notice] applied to %s" % TARGET)
    return 0


if __name__ == "__main__":
    sys.exit(main())
