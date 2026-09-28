"""Retired words (D56) never appear in anything published: keys, ids or values. The same check as
site/src/lib/words.ts, which splits identifiers into words first, so every case style is caught."""
from __future__ import annotations

import re

# legacy-names:start
RETIRED = re.compile(r"\b(levels?|repeats?|factors?|conditions?|sessions?|vmms?)\b", re.IGNORECASE)
# legacy-names:end


def words(text: str) -> str:
    return re.sub(r"[_\-./]", " ", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text))


def retired_word_in(text: str) -> str | None:
    m = RETIRED.search(words(text))
    return m.group(1) if m else None


def retired_words_in_json(value, path: str = "$") -> list[str]:
    hits = []
    if isinstance(value, str):
        w = retired_word_in(value)
        if w:
            hits.append(f'{path}: "{w}"')
    elif isinstance(value, list):
        for i, v in enumerate(value):
            hits += retired_words_in_json(v, f"{path}[{i}]")
    elif isinstance(value, dict):
        for k, v in value.items():
            w = retired_word_in(k)
            if w:
                hits.append(f'{path}.{k} (key): "{w}"')
            hits += retired_words_in_json(v, f"{path}.{k}")
    return hits


def without_legacy_blocks(text: str) -> str:
    """Source text minus the marked blocks that may name old words (Python, JS and Markdown markers)."""
    return re.sub(r"(#|//|<!--) legacy-names:start[\s\S]*?(#|//|<!--) legacy-names:end", "", text)
