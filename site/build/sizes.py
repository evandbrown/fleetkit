"""The size budget for the published dataset (gzip), the same limits as site/scripts/size-check.mjs plus the
explorer's per-image limits."""
from __future__ import annotations

import gzip
from pathlib import Path

KB = 1024
BUDGET = {"index": 5 * KB, "doc": 80 * KB, "thumb": 10 * KB, "full": 60 * KB, "data": 8192 * KB}  # the whole committed dataset; pages load it lazily, one document at a time


def gz(data: bytes) -> int:
    return len(gzip.compress(data, compresslevel=9, mtime=0))


def check(root: Path) -> tuple[dict, list[str]]:
    """Totals and every file over its limit."""
    problems, totals = [], {"files": 0, "raw": 0, "gz": 0, "json_gz": 0, "img_gz": 0, "largest_doc": ("", 0)}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        data = p.read_bytes()
        size = gz(data)
        totals["files"] += 1
        totals["raw"] += len(data)
        totals["gz"] += size
        if rel == "index.json":
            limit = BUDGET["index"]
        elif rel.endswith(".json"):
            limit = BUDGET["doc"]
            if size > totals["largest_doc"][1]:
                totals["largest_doc"] = (rel, size)
        elif rel.endswith(".t.webp"):
            limit = BUDGET["thumb"]
        elif rel.endswith(".f.webp"):
            limit = BUDGET["full"]
        else:
            limit = None
        totals["json_gz" if rel.endswith(".json") else "img_gz"] += size
        if limit is not None and size > limit:
            problems.append(f"{rel}: {size:,} B gzipped > {limit:,} B")
    if totals["gz"] > BUDGET["data"]:
        problems.append(f"dataset: {totals['gz']:,} B gzipped > {BUDGET['data']:,} B")
    return totals, problems
