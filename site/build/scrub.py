"""Scrubbing and the gate (DATA.md, "What the builder never ships").

Every output object is built field by field, so nothing from a run directory is copied through. On top of
that, two passes:

- ``Scrubber`` rewrites every string in every document before it's written: account ids, ARNs, IP addresses,
  email addresses and EC2 instance and image ids become short aliases (``account-1``, ``ip-2``). An alias is
  stable within a build and across builds of the same inputs: aliases are numbered in the order the builder
  meets the values, and the builder's order is deterministic. The mapping is never written anywhere.
- ``gate`` scans every published byte afterwards, images included, and fails the build on a match. A
  legitimate match has to be listed in the catalog's ``gate_allow`` with a reason.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
IPV4 = rf"(?<![\d.]){_OCTET}(?:\.{_OCTET}){{3}}(?![\d.])"

# Rewritten to aliases, in this order (an ARN contains an account id, so ARNs go first).
ALIASED = [
    # "arn:" and "aws" are written apart here and in GATE so the repository's own leak check passes this file.
    ("arn", re.compile(r"\barn:" r"aws[a-z-]*:[^\s\"',;]*")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("account", re.compile(r"(?<!\d)\d{12}(?!\d)")),
    ("instance", re.compile(r"\bi-[0-9a-f]{8}(?:[0-9a-f]{9})?\b")),
    ("image", re.compile(r"\bami-[0-9a-f]{8}(?:[0-9a-f]{9})?\b")),
    ("ip", re.compile(IPV4)),
]

# The gate: none of these may appear in any published byte.
GATE = [
    ("instance or resource id", re.compile(rb"\b(?:i|ami|vol|snap|sg|subnet|vpc|eni|igw|rtb)-[0-9a-f]{8,17}\b")),
    ("ARN", re.compile(rb"\barn:" rb"aws")),
    ("12-digit number (account id)", re.compile(rb"(?<!\d)\d{12}(?!\d)")),
    ("IPv4 address", re.compile(IPV4.encode())),
    ("email address", re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("AWS hostname", re.compile(rb"amazonaws|\.internal\b|\bip-\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3}\b")),
    ("S3 URL", re.compile(rb"s3://")),
    ("URL", re.compile(rb"\bhttps?://")),
    ("availability zone", re.compile(rb"\b(?:us|eu|ap|sa|ca|me|af|il|mx)-[a-z]+-\d[a-f]\b")),
]
# Image chunks that carry metadata (DATA.md: never EXIF, ICC or XMP).
WEBP_ALLOWED_CHUNKS = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF"}


class GateError(Exception):
    pass


class Scrubber:
    def __init__(self):
        self.aliases: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    def alias(self, kind: str, value: str) -> str:
        if value not in self.aliases:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            self.aliases[value] = f"{kind}-{self.counts[kind]}"
        return self.aliases[value]

    def text(self, s: str) -> str:
        for kind, rx in ALIASED:
            s = rx.sub(lambda m, k=kind: self.alias(k, m.group(0)), s)
        return s

    def value(self, v):
        """A copy of a JSON value with every string (keys included) scrubbed."""
        if isinstance(v, str):
            return self.text(v)
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if isinstance(v, dict):
            return {self.text(k): self.value(x) for k, x in v.items()}
        return v


def webp_chunks(data: bytes) -> list[bytes]:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise GateError("not a WebP file")
    out, i = [], 12
    while i + 8 <= len(data):
        tag, size = data[i:i + 4], struct.unpack("<I", data[i + 4:i + 8])[0]
        out.append(tag)
        i += 8 + size + (size & 1)
    return out


def gate(root: Path, allow: list[dict] | None = None) -> list[str]:
    """Every problem in the files under ``root``; an empty list means the gate passes. ``allow``: the catalog's
    ``gate_allow`` entries, each ``{"text": ..., "reason": ...}``, exempting that exact text."""
    allowed = {a["text"].encode() for a in allow or [] if a.get("text") and a.get("reason")}
    problems = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        data = p.read_bytes()
        if p.suffix == ".webp":
            try:
                extra = [c.decode("latin-1") for c in webp_chunks(data) if c not in WEBP_ALLOWED_CHUNKS]
            except GateError as e:
                extra = [str(e)]
            if extra:
                problems.append(f"{rel}: image metadata chunks {extra}")
            if len(data) > 30 and data[12:16] == b"VP8X" and data[20] & 0b0010_1100:
                problems.append(f"{rel}: VP8X header flags ICC, EXIF or XMP")
        elif p.suffix != ".json":
            problems.append(f"{rel}: only JSON and WebP files are published")
        for label, rx in GATE:
            for m in rx.finditer(data):
                if m.group(0) not in allowed:
                    problems.append(f"{rel}: {label} at byte {m.start()}")
                    break
    return problems
