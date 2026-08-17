#!/usr/bin/env python3
"""Install the fail-closed outbound email tool onto the volume.

WHY THIS IS NOT A PATCH TO email.py
-----------------------------------
The email adapter is reply-only by construction: _dispatch_message sets
chat_id = sender_addr, and send() passes it through as to_addr. There is no
seam where an agent-chosen recipient could enter. Patching one in would mean
rewriting the dispatch contract inside a vendored package -- a large anchored
patch across code we do not own, that breaks on any upstream bump.

The existing precedent on this stack is send_digest.py: arbitrary-recipient
mail is an EXPLICIT SMTP ACTION invoked as a script, not a gateway capability.
This follows that precedent. The gateway stays reply-only. Outbound send is a
separate, auditable tool the agent runs deliberately.

This also keeps the blast radius honest. A gateway-level send capability would
apply to every message the agent emits. A script applies only when called.

FAIL-CLOSED ALLOWLIST -- THE POINT OF THIS BUILD
------------------------------------------------
EMAIL_ALLOWED_USERS (inbound) fails OPEN when empty: `if allowed_raw:` means an
unset variable disables the check and the mailbox answers the internet. That is
a known defect we live with.

EMAIL_OUTBOUND_ALLOWED does the OPPOSITE and must never be written the other
way. Unset or empty means send to NOBODY. There is no bypass flag, no wildcard,
no domain matching. Exact string match only.

ACTION-CLAIM GUARD
------------------
On 2026-08-17 Bailey emailed Amy: "Will reclassify", "Will add", "Will record",
"I'll process all corrections and provide updated property statements." Bailey
has no write path to QBO or Xero. Those were four commitments she structurally
cannot perform, addressed to the person who maintains the books.

Prose in a SKILL.md has gone roughly one for four on this stack. So the guard
is mechanical: the tool refuses to send a body containing forward-looking
action claims unless --allow-action-claims is passed explicitly. Refusing is
the default because the failure mode is silent and lands on real books.

IDEMPOTENCE
-----------
Rewrites the tool on every boot from the payload below, so the volume copy can
never drift from the branch. Nothing here reads volume state as truth.

Set HERMES_SKIP_OUTBOUND_SEND=1 to skip installation entirely.
"""

import os
import sys

DEST_DIR = "/data/.hermes/tools"
DEST = os.path.join(DEST_DIR, "send_to.py")

TOOL = r'''#!/usr/bin/env python3
"""Send an email to an explicitly allowlisted recipient. Fail-closed.

Usage:
  python3 /data/.hermes/tools/send_to.py --to ADDR --subject SUBJ --body-file PATH
  python3 /data/.hermes/tools/send_to.py --to ADDR --subject SUBJ --body-file PATH --allow-action-claims

Exit codes:
  0  sent
  2  refused: recipient not on EMAIL_OUTBOUND_ALLOWED (or list empty/unset)
  3  refused: body contains unsubstantiated action claims
  4  configuration error (missing SMTP settings or body file)
  5  SMTP failure

Every attempt, allowed or refused, is appended to the audit log at
/data/.hermes/logs/outbound_send_audit.log. The audit line is written BEFORE
the send is attempted, so a crash mid-send still leaves a record.
"""

import argparse
import os
import smtplib
import sys
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate

AUDIT = "/data/.hermes/logs/outbound_send_audit.log"

# Forward-looking claims of bookkeeping action. Bailey has no write path to any
# accounting system; a sentence like these tells the reader the books changed.
ACTION_PATTERNS = [
    "will reclassify", "will re-classify", "will add", "will record",
    "will remove", "will update", "will post", "will enter", "will correct",
    "will process", "i'll process", "i'll reclassify", "i'll add",
    "i'll record", "i'll remove", "i'll update", "i'll post", "i'll enter",
    "i have updated", "i've updated", "i have entered", "i've entered",
    "has been posted", "have been posted", "has been recorded",
    "have been recorded", "updated property statements",
    "corrections have been made",
]


def audit(status, to_addr, subject, detail=""):
    try:
        os.makedirs(os.path.dirname(AUDIT), exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        with open(AUDIT, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\tto=%s\tsubject=%s\t%s\n"
                     % (stamp, status, to_addr, subject, detail))
    except Exception:
        pass  # auditing must never be the reason a send fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--body-file", required=True,
                    help="Path to a UTF-8 text file holding the body. Not an "
                         "inline string: bodies contain quotes and newlines "
                         "that do not survive a shell argument.")
    ap.add_argument("--cc", default="")
    ap.add_argument("--allow-action-claims", action="store_true",
                    help="Permit forward-looking claims of bookkeeping action. "
                         "Only pass this when the claim is true and the action "
                         "has actually been performed by a human.")
    args = ap.parse_args()

    to_addr = args.to.strip().lower()

    # --- FAIL-CLOSED ALLOWLIST -------------------------------------------
    # Note the inversion versus EMAIL_ALLOWED_USERS: empty means DENY ALL.
    raw = os.getenv("EMAIL_OUTBOUND_ALLOWED", "").strip()
    allowed = {a.strip().lower() for a in raw.split(",") if a.strip()}
    if not allowed:
        audit("REFUSED_NO_ALLOWLIST", to_addr, args.subject,
              "EMAIL_OUTBOUND_ALLOWED unset or empty")
        print("REFUSED: EMAIL_OUTBOUND_ALLOWED is unset or empty. "
              "Outbound send is disabled. No mail was sent.")
        return 2
    if to_addr not in allowed:
        audit("REFUSED_NOT_ALLOWLISTED", to_addr, args.subject,
              "%d addresses on list" % len(allowed))
        print("REFUSED: %s is not on the outbound allowlist. No mail was sent."
              % to_addr)
        return 2

    cc_out = []
    for part in args.cc.split(","):
        a = part.strip().lower()
        if not a or a == to_addr:
            continue
        if a not in allowed:
            audit("REFUSED_CC_NOT_ALLOWLISTED", to_addr, args.subject, "cc=%s" % a)
            print("REFUSED: Cc address %s is not on the outbound allowlist. "
                  "No mail was sent." % a)
            return 2
        cc_out.append(a)

    if not os.path.exists(args.body_file):
        print("ERROR: body file not found: %s" % args.body_file)
        return 4
    with open(args.body_file, "r", encoding="utf-8") as fh:
        body = fh.read()

    # --- ACTION-CLAIM GUARD ----------------------------------------------
    if not args.allow_action_claims:
        low = body.lower()
        hits = [p for p in ACTION_PATTERNS if p in low]
        if hits:
            audit("REFUSED_ACTION_CLAIM", to_addr, args.subject,
                  "patterns=%s" % ";".join(hits[:6]))
            print("REFUSED: body claims bookkeeping action that has not been "
                  "performed. No mail was sent.")
            print("Matched: %s" % ", ".join(hits[:6]))
            print("")
            print("This bot has no write path to QuickBooks or Xero. Rewrite "
                  "the body to describe what SHOULD be entered rather than "
                  "what will be done, for example 'Recommended correction: "
                  "reclassify the $150 to Leasing Fee'. If a human has "
                  "actually performed the action, re-run with "
                  "--allow-action-claims.")
            return 3

    address = os.getenv("EMAIL_ADDRESS", "")
    password = os.getenv("EMAIL_PASSWORD", "")
    host = os.getenv("EMAIL_SMTP_HOST", "")
    port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    if not (address and password and host):
        print("ERROR: EMAIL_ADDRESS, EMAIL_PASSWORD and EMAIL_SMTP_HOST must "
              "all be set in the process environment.")
        return 4

    msg = MIMEMultipart()
    msg["From"] = address
    msg["To"] = to_addr
    if cc_out:
        msg["Cc"] = ", ".join(cc_out)
    msg["Subject"] = args.subject
    msg["Date"] = formatdate(localtime=True)
    msg_id = "<hermes-out-%s@%s>" % (uuid.uuid4().hex[:12], address.split("@")[1])
    msg["Message-ID"] = msg_id
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Audit BEFORE the wire call, so a crash mid-send still leaves a record.
    audit("SENDING", to_addr, args.subject,
          "cc=%s msgid=%s" % (",".join(cc_out) or "-", msg_id))

    try:
        smtp = smtplib.SMTP(host, port, timeout=30)
        try:
            smtp.starttls()
            smtp.login(address, password)
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                smtp.close()
    except Exception as exc:
        audit("SMTP_FAILED", to_addr, args.subject, str(exc)[:200])
        print("ERROR: SMTP send failed: %s" % exc)
        return 5

    audit("SENT", to_addr, args.subject, "msgid=%s" % msg_id)
    print("SENT to %s (cc: %s) message-id %s"
          % (to_addr, ", ".join(cc_out) or "none", msg_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def main() -> int:
    if os.getenv("HERMES_SKIP_OUTBOUND_SEND", "0") == "1":
        print("[install_outbound_send] skipped (HERMES_SKIP_OUTBOUND_SEND=1)")
        return 0

    try:
        compile(TOOL, DEST, "exec")
    except SyntaxError as exc:
        print("[install_outbound_send] DECLINED: payload failed to compile: %s" % exc)
        return 0

    try:
        os.makedirs(DEST_DIR, exist_ok=True)
        tmp = DEST + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(TOOL)
        os.replace(tmp, DEST)
        os.chmod(DEST, 0o755)
    except Exception as exc:
        print("[install_outbound_send] WARNING: install failed, continuing boot: %s" % exc)
        return 0

    raw = os.getenv("EMAIL_OUTBOUND_ALLOWED", "").strip()
    n = len([a for a in raw.split(",") if a.strip()])
    print("[install_outbound_send] installed %s. EMAIL_OUTBOUND_ALLOWED has %d "
          "address(es); %s" % (DEST, n,
          "sends permitted to those only" if n else
          "OUTBOUND SEND IS DISABLED (fail-closed)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
