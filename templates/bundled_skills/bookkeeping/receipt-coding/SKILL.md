---
name: receipt-coding
description: "Parse receipts and invoices arriving by email or chat, extract the transaction fields, code each one to an account in the active coding scheme, and return an approval sheet the bookkeeper reviews before anything is entered. Codes against a swappable scheme file, defaulting to IRS Schedule C reference categories. Never writes to an accounting system."
version: 1.0.0
author: Matt Shepard
license: MIT
platforms: [linux, macos, windows]
prerequisites:
  files: [reference/coding_scheme.json]
triggers:
  - an inbound email carries receipt or invoice attachments
  - user sends a photo or PDF of a receipt
  - user says "code these receipts"
  - user says "categorize these"
  - user asks what account a purchase belongs to
  - user asks for an approval sheet or a coded batch
tags: [bookkeeping, receipts, coding, demo]
metadata:
  hermes:
    tags: [Bookkeeping, Receipts, Coding]
---

# Receipt Coding

Receipts arrive by email or chat. Read each one, pull the transaction fields, suggest an account, and hand back an approval sheet. The bookkeeper approves or corrects. Nothing is ever entered into an accounting system by this skill, and no accounting system is connected.

The value is a reviewed first pass, not an unattended one. A batch where six of forty rows are honestly flagged is worth more than forty rows coded with false confidence.

## The coding scheme

Load `reference/coding_scheme.json` at the start of every batch. It is data, not code. It carries the account list, the confidence policy, the fallback accounts, and the always-flag conditions.

**Only ever suggest an account name that appears in that file.** Never invent one, never abbreviate one, never merge two. If nothing fits, use a fallback account and say why in the review reason.

Entries with `auto_assign: false` must never be suggested from a receipt. They exist so the scheme matches the real form, not so the bot can route to them.

The default scheme is the IRS Schedule C reference set. If a client's own chart of accounts is supplied, it replaces this file wholesale and the rest of the skill is unchanged. Say so plainly when asked: the categories are a reference default, and a production bot codes to whatever rules the client uses.

## Steps

**Step 1 - Inventory the batch.** Count the attachments and name them. If a message says receipts are attached and none arrived, say so and stop. Do not code from the body text of an email describing a purchase unless the sender explicitly asks for that.

**Step 2 - Extract per receipt.** For each one, pull: date, vendor, description or line items, subtotal, sales tax, tip, total, payment method, and last four digits. Missing fields stay empty. Never fill a missing field with a plausible value.

Sales tax and tip are extracted as their own columns and are NOT folded into the expense amount. Getting this wrong is the first thing a bookkeeper checks.

**Step 3 - Code and score.** Match to an account using the line items first and the vendor name second. A big-box vendor sells across several categories, so the line items govern. Score confidence honestly 0-100 and apply `confidence_policy` from the scheme file:

- at or above the auto threshold: code it, no flag
- in the flag band: code it, flag it, give the reason
- below the floor: route to `Ask My Accountant` and give the reason
- unreadable: route to `Uncategorized Expense`

Apply every condition in `always_flag_for_review` regardless of confidence. A high-confidence match on a receipt that might be personal is still flagged.

Carry `deductible_pct` through to the sheet. Meals are 50 percent. Entertainment is not deductible and is never coded to meals; flag it instead.

**Step 4 - Build the approval sheet.**
`python scripts/build_approval_sheet.py --out /tmp/approval_sheet` with the rows as JSON on stdin. It writes CSV always and XLSX when the library is available, and it returns the paths it wrote.

**Step 5 - Reply.** Send the sheet as an attachment. In the body: how many receipts were read, how many coded cleanly, how many need review, and the flagged rows listed with their reasons. Keep it to what the reader needs to act.

If the sheet cannot be attached for any reason, put the table in the body and say the attachment failed. Never go silent.

## Column order for the sheet

Date, Vendor, Description, Subtotal, Sales Tax, Tip, Total, Payment Method, Last 4, Suggested Account, Schedule C Line, Deductible %, Confidence, Needs Review, Review Reason, Source File.

## Hard rules

- Never write to QuickBooks or any accounting system. This skill has no such access and must not claim to.
- Never state a total the receipt does not show. An unreadable total is unreadable.
- Never guess a date. A missing date is a flag, not a estimate.
- Never suggest an account outside the scheme file.
- Never silently drop a receipt. Every attachment received appears as a row, even if that row is entirely flags.
- Duplicates within a batch are flagged, not removed.
- If asked whether a specific expense is deductible, answer from the scheme notes and say plainly that final treatment is the bookkeeper's call. This skill codes, it does not give tax advice.
- No em dashes.

## Changelog

v1.0.0 -- initial build. Schedule C reference scheme verified against the live 2025 form, including the 27a/27b layout where 27a is the Form 7205 energy deduction and other expenses flow from Part V line 48 to 27b -- 2026-07-28
