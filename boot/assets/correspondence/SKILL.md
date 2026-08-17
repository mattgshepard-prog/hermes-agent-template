---
name: correspondence
description: "Send a reply or a follow-up to a named person other than whoever wrote in. The email channel is reply-only by construction, so this is the only path by which a message reaches an address that did not just send one. Recipients are restricted to a fail-closed allowlist. Bodies must state what should be entered, never that entry has happened, because this bot has no write path to any accounting system."
version: 1.0.0
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

Recipients:

| Person | Address |
|---|---|
| Matt Shepard | mattgshepard@gmail.com |
| Amy (AMS Holdings property management) | amspropertymgt@gmail.com |

Nobody else can be reached. The allowlist is fail-closed: an address that is
not on it is refused and no mail is sent. This is not a limitation to work
around. If the operator asks for a recipient not on that list, say plainly that
the address is not permitted and ask them to add it.

## Two mechanics that will trip you up

**Write the body to a file first, then send.** Use the file-writing tool to
create the body at a path like `/tmp/reply_amy.txt`. Do not try to build the
body inline in the shell command.

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

## After sending

Report to the operator what went out, to whom, and who was copied. If the send
was refused, say so and say why. Never report a message as sent unless the tool
returned SENT. Every attempt, including refusals, is recorded in
`/data/.hermes/logs/outbound_send_audit.log`.
