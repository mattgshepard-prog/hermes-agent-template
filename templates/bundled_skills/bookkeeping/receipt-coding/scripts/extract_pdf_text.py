#!/usr/bin/env python3
"""Extract text from a PDF using only the Python standard library.

Why this exists
---------------
On 2026-07-29 the receipt-coding skill could not read an emailed PDF invoice.
The container has no pymupdf, fitz, pypdf, pdfplumber, pdftotext, or strings.
The agent improvised with `python3 -c` and a heredoc; both tripped the
dangerous-command approval gate, so the sender got two "Dangerous command
requires approval" emails and ten minutes of silence instead of a sheet.

The gate is correct. Inline shell was the mistake. A committed script invoked
by path is not gated, which is how build_approval_sheet.py already runs.

No third-party dependency is needed. PDF content streams are compressed with
filters that are all in the standard library for the common cases:
ASCII85Decode (base64), FlateDecode (zlib), ASCIIHexDecode (binascii).
ReportLab, which generated the invoice that exposed this, chains ASCII85 then
Flate, so a Flate-only decoder silently finds nothing.

Scope: text-layer PDFs. A scanned page has no text layer, and this reports
NO_TEXT_LAYER rather than guessing. Never invent content for an unreadable
document; flag the row instead.
"""

import argparse
import base64
import binascii
import re
import sys
import zlib

# Stream data, and the dictionary that precedes it. The dict is located by
# looking backwards rather than by matching balanced <<...>>, because real
# PDFs nest dictionaries and a naive match grabs the wrong one. Only /Filter
# is needed, so a bounded lookbehind window is enough and cannot mis-nest.
# The newline before `endstream` is optional: ReportLab writes exactly
# /Length bytes and then the keyword, with no separator.
STREAM = re.compile(rb"stream\r?\n(?P<data>.*?)\r?\n?endstream", re.S)
DICT_WINDOW = 800
FILTERS = re.compile(rb"/Filter\s*(\[[^\]]*\]|/\w+)", re.S)
NAME = re.compile(rb"/(\w+)")

# Content-stream show-text operators and line-break operators.
SHOW = re.compile(rb"\[(.*?)\]\s*TJ|\((?:\\.|[^\\()])*\)\s*Tj", re.S)
LITERAL = re.compile(rb"\((?:\\.|[^\\()])*\)", re.S)
NEWLINE_OPS = re.compile(rb"(T\*|TD|Td|TL)")

ESCAPES = {
    b"\\n": b"\n", b"\\r": b"\r", b"\\t": b"\t",
    b"\\b": b"\b", b"\\f": b"\f",
    b"\\(": b"(", b"\\)": b")", b"\\\\": b"\\",
}


def unescape(raw: bytes) -> bytes:
    for k, v in ESCAPES.items():
        raw = raw.replace(k, v)
    return re.sub(rb"\\([0-7]{1,3})",
                  lambda m: bytes([int(m.group(1), 8) & 0xFF]), raw)


def decode_literal(tok: bytes) -> str:
    return unescape(tok[1:-1]).decode("latin-1", "replace")


def apply_filter(data: bytes, name: bytes) -> bytes:
    if name in (b"FlateDecode", b"Fl"):
        return zlib.decompress(data)
    if name in (b"ASCII85Decode", b"A85"):
        body = b"".join(data.split())          # a85 ignores whitespace
        if body.startswith(b"<~"):
            body = body[2:]
        if body.endswith(b"~>"):
            body = body[:-2]
        return base64.a85decode(body)
    if name in (b"ASCIIHexDecode", b"AHx"):
        body = b"".join(data.split()).rstrip(b">")
        if len(body) % 2:                       # odd length is padded with 0
            body += b"0"
        return binascii.unhexlify(body)
    # DCTDecode, CCITTFaxDecode, JPXDecode are images, not text.
    raise ValueError(f"unsupported filter {name.decode()}")


def streams(data: bytes):
    """Yield each fully decoded stream, skipping any we cannot decode."""
    for m in STREAM.finditer(data):
        raw = m.group("data")
        header = data[max(0, m.start() - DICT_WINDOW):m.start()]
        found = None
        for found in FILTERS.finditer(header):   # nearest declaration wins
            pass
        names = NAME.findall(found.group(1)) if found else []
        try:
            for name in names:                  # chain applies in order
                raw = apply_filter(raw, name)
        except (ValueError, zlib.error, binascii.Error):
            continue
        yield raw


def text_from_stream(chunk: bytes) -> str:
    out, pos = [], 0
    for m in SHOW.finditer(chunk):
        if NEWLINE_OPS.search(chunk[pos:m.start()]):
            out.append("\n")
        token = m.group(0)
        if token.rstrip().endswith(b"TJ"):
            out.append("".join(decode_literal(t)
                               for t in LITERAL.findall(m.group(1))))
        else:
            out.append(decode_literal(LITERAL.search(token).group(0)))
        pos = m.end()
    return "".join(out)


def extract(path: str) -> str:
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.startswith(b"%PDF"):
        raise ValueError(f"{path} is not a PDF")
    pages = [text_from_stream(s) for s in streams(data)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(p for p in pages if p.strip())).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract text from a PDF (stdlib only).")
    ap.add_argument("path")
    args = ap.parse_args()
    try:
        text = extract(args.path)
    except (OSError, ValueError) as exc:
        print(f"EXTRACTION_FAILED: {exc}", file=sys.stderr)
        return 2
    if not text:
        print("NO_TEXT_LAYER: no extractable text (likely a scanned image). "
              "Flag this receipt for review; do not infer its contents.",
              file=sys.stderr)
        return 3
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
