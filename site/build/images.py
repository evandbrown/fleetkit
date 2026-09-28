"""Screenshots as WebP, named by the first 8 hex digits of the JPEG's SHA-256 and deduplicated by that hash: a
320 px thumbnail of every one, and a full-size copy (at most 1,280 px) only of those a page shows at full size
(DATA.md, rule 10). No metadata is written: no EXIF, ICC or XMP."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image

THUMB_PX, FULL_PX = 320, 1280
THUMB_MAX, FULL_MAX = 10 * 1024, 60 * 1024      # bytes, per the explorer's size budget
THUMB_QUALITY, FULL_QUALITY = 70, 60             # starting points; stepped down until the image fits. Full size
                                                 # at 60, not 75: 15% smaller, no difference visible at 1:1


class Images:
    def __init__(self):
        self.by_sha8: dict[str, str] = {}   # sha8 -> full SHA-256
        self.src: dict[str, Path] = {}      # sha8 -> source JPEG
        self.full: set[str] = set()         # sha8s a page shows at full size (rule 10)

    def add(self, path: Path) -> str | None:
        """Registers a screenshot (its thumbnail) and returns its sha8, or None if the file is missing."""
        if not path.is_file():
            return None
        full = hashlib.sha256(path.read_bytes()).hexdigest()
        sha8 = full[:8]
        if self.by_sha8.setdefault(sha8, full) != full:
            raise ValueError(f"two different screenshots share the short hash {sha8}")
        self.src.setdefault(sha8, path)
        return sha8

    def full_size(self, sha8: str) -> None:
        """Publishes a registered screenshot at full size too."""
        if sha8 not in self.src:
            raise ValueError(f"screenshot {sha8} was never registered")
        self.full.add(sha8)

    def write(self, out_dir: Path) -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        sizes = {"thumbs": 0, "fulls": 0, "count": 0, "full_count": 0}
        for sha8, src in sorted(self.src.items()):
            with Image.open(src) as im:
                im.load()
                rgb = im.convert("RGB")
            t = encode(rgb, THUMB_PX, THUMB_QUALITY, THUMB_MAX)
            (out_dir / f"{sha8}.t.webp").write_bytes(t)
            sizes["thumbs"] += len(t)
            sizes["count"] += 1
            if sha8 in self.full:
                f = encode(rgb, FULL_PX, FULL_QUALITY, FULL_MAX)
                (out_dir / f"{sha8}.f.webp").write_bytes(f)
                sizes["fulls"] += len(f)
                sizes["full_count"] += 1
        return sizes


def encode(rgb: Image.Image, width: int, quality: int, max_bytes: int) -> bytes:
    """WebP at ``width`` (never upscaled), stepping quality down by 5 until it fits ``max_bytes``."""
    w = min(width, rgb.width)
    h = max(1, round(rgb.height * w / rgb.width))
    small = rgb.resize((w, h), Image.LANCZOS) if w != rgb.width else rgb.copy()
    small.info = {}          # drop anything carried from the JPEG (ICC profile, EXIF)
    q = quality
    while True:
        buf = io.BytesIO()
        small.save(buf, "WEBP", quality=q, method=6, exif=b"", icc_profile="")
        data = buf.getvalue()
        if len(data) <= max_bytes or q <= 30:
            return data
        q -= 5
