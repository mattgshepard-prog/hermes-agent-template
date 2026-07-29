#!/usr/bin/env python3
"""Make "I could not see this image" unmissable in the model's context.

What happened
-------------
2026-07-29 22:04 UTC, beths-bot. Seven receipts arrived by email. Image
routing fell back to text mode, `vision_analyze` failed on all six images
(task=vision resolves to OpenRouter with no credit and Nous with no auth),
and the model was handed this, six times:

    [The user sent an image but I couldn't quite see it this time (>_<)
     You can try looking at it yourself with vision_analyze using
     image_url: /data/.hermes/image_cache/img_....jpg]

The model then produced a complete, internally consistent approval sheet for
seven receipts at read confidence 95. Six of the seven rows were invented,
including three vendors that were not in the batch at all (Staples, Amazon,
Uber). The only correct row was the PDF, which is read by a deterministic
text extractor rather than by vision.

It was told it could not see the images and it filled the gap anyway.

Why the wording matters
-----------------------
The original notice reads as a soft apology with a suggested workaround. It
does not say what must not happen next. This rewrite states the constraint
in the imperative and names the failure it is preventing. That is not a
guarantee — instructions are not mechanisms — but a directive sentence is
strictly better than an apologetic one, and it costs nothing.

The real fix is `agent.image_input_mode: native` in config.yaml, wired by
start.sh, so this path is not taken at all. This patch exists for the case
where it is taken anyway.

Safety
------
Idempotent, anchored to exact strings, declines to act if upstream changes
them, and never fails the boot.
"""

import sys

TARGET = "/opt/hermes-agent/gateway/run.py"

DIRECTIVE = (
    "[VISION UNAVAILABLE. The image at {path} was NOT read. You have not seen "
    "it and you have no information about its contents. Do not describe, "
    "transcribe, summarise, or infer anything from it, and do not produce a "
    "value that would require having read it. Tell the user this file could "
    "not be read and stop.]"
)

# (anchor, replacement) — anchors are the exact literals in the failure and
# exception branches of _enrich_message_with_vision.
EDITS = [
    (
        '''                    enriched_parts.append(
                        "[The user sent an image but I couldn't quite see it "
                        "this time (>_<) You can try looking at it yourself "
                        f"with vision_analyze using image_url: {path}]"
                    )''',
        '''                    enriched_parts.append(
                        f"[VISION UNAVAILABLE. The image at {path} was NOT read. "
                        "You have not seen it and you have no information about its "
                        "contents. Do not describe, transcribe, summarise, or infer "
                        "anything from it, and do not produce a value that would "
                        "require having read it. Tell the user this file could not "
                        "be read and stop.]"
                    )''',
    ),
    (
        '''                enriched_parts.append(
                    f"[The user sent an image but something went wrong when I "
                    f"tried to look at it~ You can try examining it yourself "
                    f"with vision_analyze using image_url: {path}]"
                )''',
        '''                enriched_parts.append(
                    f"[VISION UNAVAILABLE. The image at {path} was NOT read. "
                    "You have not seen it and you have no information about its "
                    "contents. Do not describe, transcribe, summarise, or infer "
                    "anything from it, and do not produce a value that would "
                    "require having read it. Tell the user this file could not "
                    "be read and stop.]"
                )''',
    ),
]

MARKER = "VISION UNAVAILABLE. The image at"


def log(msg):
    print("[patch_vision_unavailable_notice] %s" % msg, flush=True)


def main():
    try:
        with open(TARGET, encoding="utf-8") as fh:
            src = fh.read()
    except OSError as exc:
        log("SKIP: cannot read %s (%s)" % (TARGET, exc))
        return 0

    if src.count(MARKER) >= 2:
        log("already patched, nothing to do")
        return 0

    patched = src
    applied = 0
    for anchor, replacement in EDITS:
        count = patched.count(anchor)
        if count != 1:
            log("SKIP: expected 1 match for an anchor, found %d. Upstream "
                "changed; leaving the file untouched." % count)
            return 0
        patched = patched.replace(anchor, replacement, 1)
        applied += 1

    try:
        with open(TARGET, "w", encoding="utf-8") as fh:
            fh.write(patched)
    except OSError as exc:
        log("SKIP: cannot write %s (%s)" % (TARGET, exc))
        return 0

    try:
        with open(TARGET, encoding="utf-8") as fh:
            check = fh.read()
    except OSError as exc:
        log("WARNING: wrote the patch but could not read it back (%s)" % exc)
        return 0

    if check.count(MARKER) >= 2:
        log("patched: %d vision-failure notices are now directive" % applied)
    else:
        log("WARNING: read-back did not confirm the patch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
