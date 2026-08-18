---
name: correspondence
description: "Send email to Amy or Matt by name. Addresses known."
version: 1.3.0
author: Matt Shepard
license: MIT
platforms: [linux]
prerequisites:
  files: [/data/.hermes/tools/send_to.py]
triggers:
  - operator says "send this to <person>"
  - operator says "reply to <person> and copy me"
  - operator says "send it back to her" or "send it to him"
  - operator names a recipient who is not the person who wrote in
  - a reply needs to reach someone who is not the sender of the inbound mail
tags: [email, correspondence, bookkeeping]
metadata:
  hermes:
    tags: [Email, Correspondence]
---

# Correspondence

## Never ask who Amy is, or for anyone's address

**Amy always means Amy Moore.** She is Matt's spouse and partner, and she runs
property management for AMS Holdings. Her address is
`amspropertymgt@gmail.com`. It is written below and it is already on the
allowlist.

There are exactly two people this bot can email and both are named in this
file. **Do not ask the operator for an address, and do not ask who a first name
refers to.** If the operator says "send this to Amy", that is a complete
instruction. Send it.

The only thing worth asking about is *content*, and only when the instruction
genuinely does not say what to send. "Send this to Amy" in a thread almost
always means the substance of the current exchange, rewritten as a message
addressed to her. Prefer drafting it and saying what was sent over asking which
part was meant.

## When this applies, and when it does not

The email channel replies to whoever wrote in. That is structural: the adapter
takes the inbound sender address and uses it as the reply address. There is no
way to change it from inside a normal reply.

**Do not use this skill for an ordinary reply.** If Matt writes and expects an
answer, just answer. The reply goes back to him automatically, and if he copied
someone, they are copied automatically too.

**Use this skill only when the operator names a recipient who is not the person
who wrote in.** "Send this to Amy." "Reply to Amy and copy me." "Send it back
to her."

## The tool

    python3 /data/.hermes/tools/send_to.py --to ADDR --subject SUBJ --body-file PATH [--cc ADDR]

Recipients. This table is the complete list of people who exist:

| Name | Who they are | Address |
|---|---|---|
| Matt Shepard | The operator. Owner of the books. | mattgshepard@gmail.com |
| Amy Moore ("Amy") | Matt's spouse and partner. Runs AMS Holdings property management and maintains the books. | amspropertymgt@gmail.com |

Nobody else can be reached. The allowlist is fail-closed: an address that is
not on it is refused and no mail is sent. This is not a limitation to work
around. If the operator asks for a recipient not on that list, say plainly that
the address is not permitted and ask them to add it.

When sending to Amy on Matt's instruction, copy Matt unless told otherwise. He
asked for it and it keeps the three-person loop intact.

## Two mechanics that will trip you up

**Write the body to a file first, then send.** Use the file-writing tool to
create the body at a path like `/tmp/reply_amy.txt`. Do not try to build the
body inline in the shell command.

**Your session will not contain the email settings. This is expected.**
`EMAIL_ADDRESS`, `EMAIL_PASSWORD` and `EMAIL_SMTP_HOST` are stripped from every
sandboxed command by the framework, deliberately. If you inspect your
environment you will not find them. That is not a fault and not a blocker:
`send_to.py` reads them from the volume itself when it runs.

**Do not take that on faith. Check it.** If you doubt the tool can send, run:

    python3 /data/.hermes/tools/send_to.py --check

It reports which settings resolved and where each came from, prints no values,
and needs no approval. `READY: yes` means the send will work. Use this instead
of inspecting the environment or the `.env` file yourself.

**Run the send command directly. Never wrap it.** No `bash -c`, no `sh -c`, no
`set -a`, no `source /data/.hermes/.env`. A wrapper turns the command into a
compound shell command, which trips the approval gate and stops the send. The
wrapper is also pointless: the script already loads what it needs. If you find
yourself about to source the environment file, run `--check` instead.

**Body files must sit under `/tmp`.** The script refuses a body file anywhere
else.

**Run the send as one clean command with no shell operators.** No `>`, no `|`,
no `&&`, no `;`, no backticks. A command containing any of those is treated as
compound and will stop and ask for approval instead of going out. That is why
the body goes into a file in a separate step rather than being piped or
redirected into the command.

Correct:

    python3 /data/.hermes/tools/send_to.py --to amspropertymgt@gmail.com --subject "April 2026 corrections" --body-file /tmp/reply_amy.txt --cc mattgshepard@gmail.com

## How to write the body. This is the part that matters.

**This bot has no write path to QuickBooks or Xero.** Nothing is entered
anywhere by this bot. A human enters every transaction.

So the body must describe what **should be entered**, never that entry has
happened or is about to happen. This is not a style preference. On 2026-08-17
this bot emailed Amy saying "Will reclassify", "Will add", "Will record" and
"I'll process all corrections and provide updated property statements." None of
that was possible. Amy maintains the books and would reasonably have concluded
they were being corrected.

The tool enforces this mechanically. A body containing forward-looking claims
of bookkeeping action is **refused and no mail is sent**. If that happens, the
refusal lists the phrases it matched. Rewrite and re-send. Do not try to evade
the check by rephrasing around it; fix the actual claim.

Wrong:

> Will reclassify the $150 expense from "Renewal Fee" to "Leasing Fee".
> Once you clarify, I'll process all corrections and provide updated statements.

Right:

> Recommended correction: the $150 CRT charge on 04/14 should be categorized as
> Leasing Fee rather than Renewal Fee.
>
> Two items need your input before the rest can be finalized. Nothing has been
> entered.

Say what is recommended. Say what is still open. Say plainly that nothing has
been entered. Sign as Bailey.

## Open questions belong in the email

If something cannot be resolved, ask it in the message rather than guessing.
A recommendation set with two honest open questions is worth more than one that
looks complete and is wrong. The same principle governs receipt coding: a batch
with six flagged rows beats forty coded with false confidence.

Note the difference between this and the rule at the top. Asking Amy a
substantive bookkeeping question inside the email is correct. Asking Matt who
Amy is, or what her address is, before sending anything, is not.

## After sending

Report to the operator what went out, to whom, and who was copied. If the send
was refused, say so and say why. Never report a message as sent unless the tool
returned SENT. Every attempt, including refusals, is recorded in
`/data/.hermes/logs/outbound_send_audit.log`.
