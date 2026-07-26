#!/usr/bin/env python3
"""
Send the self-learning digest to the operator over SMTP.

Why this exists: the cron scheduler cannot deliver to email. It only delivers
to platforms that declare a cron_deliver_env_var, and the email platform
declares none. So the agent has to send the digest itself, as an explicit
step, and this is the only sanctioned way to do it.

Credentials are read from HERMES_HOME/.env directly, NOT from os.environ. The
terminal tool scrubs the environment of subprocesses it spawns, so anything
depending on os.environ fails on a cron run while appearing fine by hand.
That already cost two days of misdiagnosis once. Do not "simplify" this back
to os.environ.

Usage:
  send_digest.py check
      Print whether sending is configured, exit 0 if yes, 2 if not, with a
      "reason" field. Quote that reason verbatim; never guess one.

  send_digest.py send --file PATH [--subject TEXT]
      Send the contents of PATH as the digest body. Prefer --file. Piping into
      the interpreter is refused by the command scanner and a cron run has no
      operator present to approve it.

Required in HERMES_HOME/.env:
  EMAIL_ADDRESS      the sending account
  EMAIL_PASSWORD     app password for that account
  EMAIL_SMTP_HOST    e.g. smtp.gmail.com
  DIGEST_EMAIL_TO    where the digest goes (the operator)

Exit 0 on success, 1 on send failure, 2 when not configured.
"""
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage

REQUIRED = ["EMAIL_ADDRESS", "EMAIL_PASSWORD", "EMAIL_SMTP_HOST",
            "DIGEST_EMAIL_TO"]


def _home():
    return os.environ.get("HERMES_HOME") or "/data/.hermes"


def _load_env():
    path = os.path.join(_home(), ".env")
    env = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except OSError as e:
        return None, f"cannot read {path}: {e}"
    missing = [k for k in REQUIRED if not env.get(k)]
    if missing:
        return None, "missing from .env: " + ", ".join(missing)
    return env, None


def cmd_check():
    env, err = _load_env()
    if err:
        print(json.dumps({"configured": False, "reason": err}))
        return 2
    print(json.dumps({
        "configured": True,
        "from": env["EMAIL_ADDRESS"],
        "to": env["DIGEST_EMAIL_TO"],
        "smtp_host": env["EMAIL_SMTP_HOST"],
    }))
    return 0


def cmd_send(path, subject):
    env, err = _load_env()
    if err:
        print(json.dumps({"sent": False, "reason": err}))
        return 2
    if not path:
        print(json.dumps({"sent": False,
                          "reason": "send needs --file PATH"}))
        return 1
    try:
        with open(path) as fh:
            body = fh.read()
    except OSError as e:
        print(json.dumps({"sent": False, "reason": f"cannot read {path}: {e}"}))
        return 1
    if not body.strip():
        print(json.dumps({"sent": False, "reason": "digest body is empty"}))
        return 1

    msg = EmailMessage()
    msg["From"] = env["EMAIL_ADDRESS"]
    msg["To"] = env["DIGEST_EMAIL_TO"]
    msg["Subject"] = subject or "Self-Learning Digest"
    msg.set_content(body)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(env["EMAIL_SMTP_HOST"], 587, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(env["EMAIL_ADDRESS"], env["EMAIL_PASSWORD"])
            s.send_message(msg)
    except Exception as e:
        print(json.dumps({"sent": False,
                          "reason": f"{type(e).__name__}: {e}"}))
        return 1

    print(json.dumps({
        "sent": True,
        "to": env["DIGEST_EMAIL_TO"],
        "subject": msg["Subject"],
        "bytes": len(body),
    }))
    return 0


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "check":
        return cmd_check()
    if cmd == "send":
        path = None
        subject = None
        if "--file" in args:
            i = args.index("--file")
            if i + 1 >= len(args):
                print(json.dumps({"sent": False,
                                  "reason": "--file needs a path"}))
                return 1
            path = args[i + 1]
        if "--subject" in args:
            i = args.index("--subject")
            if i + 1 < len(args):
                subject = args[i + 1]
        return cmd_send(path, subject)
    print(json.dumps({
        "error": "usage: send_digest.py check | send --file PATH "
                 "[--subject TEXT]"
    }))
    return 1


if __name__ == "__main__":
    sys.exit(main())
