#!/usr/bin/env python3
"""Stop image routing from silently degrading to text mode.

The defect
----------
`gateway/run.py::_decide_image_input_mode` ends:

    except Exception as exc:
        logger.debug("image_routing: decision failed, falling back to text — %s", exc)
        return "text"

Two problems, and they compound.

1. ANY exception selects text mode. The operator may have explicitly set
   `agent.image_input_mode: native` in config.yaml; if the decision machinery
   throws before it reads that (loading config, resolving the active
   provider/model), the explicit instruction is discarded in favour of a
   guess.

2. It is logged at DEBUG. The one component that knows something went wrong
   reports it at a level nobody reads in production.

Why it matters here
-------------------
Text mode pre-analyses attachments with `vision_analyze`, which resolves
task=vision to OpenRouter and Nous. On a bot funded for neither, that returns
nothing, and the model is handed "I could not see this image" notices.

On 2026-07-29 beths-bot did that on a fresh session and returned a complete
seven-row bookkeeping approval sheet at read confidence 95 in which six rows
were invented, including three vendors that were never in the batch. The
routing had been alternating native/text run to run with no deploy between,
which is this except block firing intermittently and saying so invisibly.

The fix
-------
On failure, read `agent.image_input_mode` straight from config.yaml and
honour it. Fall back to text only when the operator has expressed no
preference, and say so at WARNING either way.

This does not force native. A genuinely non-vision model must still route to
text, and an operator who set `text` gets text. It makes an explicit choice
survive the failure of the machinery that was supposed to read it.

Safety
------
Idempotent, anchored to the exact two lines, declines to act if upstream
changes them, never fails the boot.
"""

import sys

TARGET = "/opt/hermes-agent/gateway/run.py"

ANCHOR = '''        except Exception as exc:
            logger.debug("image_routing: decision failed, falling back to text — %s", exc)
            return "text"'''

REPLACEMENT = '''        except Exception as exc:
            # Patched: honour an explicit agent.image_input_mode when the
            # decision machinery fails, and report the failure at WARNING.
            # Previously any exception returned "text" and logged at DEBUG,
            # which routed attachments through vision_analyze. Where that
            # provider is unfunded the model receives no image content at all.
            _explicit = ""
            try:
                import yaml as _yaml
                with open("/data/.hermes/config.yaml", encoding="utf-8") as _fh:
                    _cfg_raw = _yaml.safe_load(_fh) or {}
                _agent_cfg = _cfg_raw.get("agent") or {}
                _explicit = str(_agent_cfg.get("image_input_mode") or "").strip().lower()
            except Exception:
                _explicit = ""
            if _explicit in ("native", "text"):
                logger.warning(
                    "image_routing: decision failed (%s); honouring explicit "
                    "agent.image_input_mode=%s", exc, _explicit,
                )
                return _explicit
            logger.warning(
                "image_routing: decision failed (%s) and no explicit "
                "agent.image_input_mode is set. Falling back to text mode: "
                "attachments will be pre-analysed via vision_analyze and may "
                "reach the model with no image content.", exc,
            )
            return "text"'''

MARKER = "honouring explicit"


def log(msg):
    print("[patch_image_routing_fallback] %s" % msg, flush=True)


def main():
    try:
        with open(TARGET, encoding="utf-8") as fh:
            src = fh.read()
    except OSError as exc:
        log("SKIP: cannot read %s (%s)" % (TARGET, exc))
        return 0

    if MARKER in src:
        log("already patched, nothing to do")
        return 0

    count = src.count(ANCHOR)
    if count != 1:
        log("SKIP: expected 1 anchor, found %d. Upstream changed; image "
            "routing may still degrade silently. Re-verify by hand." % count)
        return 0

    patched = src.replace(ANCHOR, REPLACEMENT, 1)

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

    log("patched: routing failures now honour agent.image_input_mode and log at WARNING"
        if MARKER in check else "WARNING: read-back did not confirm the patch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
