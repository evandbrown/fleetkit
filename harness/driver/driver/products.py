"""The fixture check and the task product list (fixture/dist/task-products.json).

Products are assigned to sessions as ``list[slot mod len]`` (design section 4).
The list may be a JSON array or ``{"products": [...]}``; each entry needs a product id, a search
query and the expected title. Accepted keys: ``product_id``/``id``, ``query``, ``expected_title``/``title``.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PRODUCTS_FILENAME = "task-products.json"
REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL_PRODUCTS_PATH = REPO_ROOT / "fixture" / "dist" / PRODUCTS_FILENAME


class FixtureCheckError(Exception):
    pass


@dataclass(frozen=True)
class Product:
    product_id: str
    query: str
    expected_title: str


def normalize(raw) -> list[Product]:
    if isinstance(raw, dict):
        raw = raw.get("products") or raw.get("items") or []
    out = []
    for i, e in enumerate(raw or []):
        if not isinstance(e, dict):
            raise FixtureCheckError(f"product entry {i} is not an object")
        pid = e.get("product_id", e.get("id"))
        query = e.get("query")
        title = e.get("expected_title", e.get("title"))
        if pid is None or not query or not title:
            raise FixtureCheckError(f"product entry {i} lacks product_id/query/expected_title: {e}")
        out.append(Product(str(pid), str(query), str(title)))
    if not out:
        raise FixtureCheckError("product list is empty")
    return out


def _fetch(url: str, timeout_s: float = 5.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:
        raise FixtureCheckError(f"GET {url}: {exc.__class__.__name__}: {getattr(exc, 'reason', exc)}")


def fixture_check(check_url: str, timeout_s: float = 5.0) -> dict:
    """GET the fixture check URL; anything but 2xx fails the check."""
    status, body = _fetch(check_url.rstrip("/") + "/", timeout_s)
    if not (200 <= status < 300):
        raise FixtureCheckError(f"fixture check {check_url}: HTTP {status}")
    return {"url": check_url, "status": status, "bytes": len(body)}


def load_products(source: str | None, check_url: str | None) -> tuple[list[Product], str]:
    """Load from an explicit URL/path, else <check_url>/task-products.json, else fixture/dist/."""
    candidates = []
    if source:
        candidates.append(source)
    else:
        if check_url:
            candidates.append(check_url.rstrip("/") + "/" + PRODUCTS_FILENAME)
        candidates.append(str(LOCAL_PRODUCTS_PATH))
    errors = []
    for c in candidates:
        try:
            if c.startswith("http://") or c.startswith("https://"):
                status, body = _fetch(c)
                if not (200 <= status < 300):
                    raise FixtureCheckError(f"HTTP {status}")
                return normalize(json.loads(body.decode("utf-8"))), c
            p = Path(c)
            if not p.exists():
                raise FixtureCheckError("no such file")
            return normalize(json.loads(p.read_text(encoding="utf-8"))), str(p)
        except (FixtureCheckError, ValueError, OSError) as exc:
            errors.append(f"{c}: {exc}")
    raise FixtureCheckError("no usable product list: " + "; ".join(errors))


def assign(products: list[Product], slot: int) -> Product:
    return products[int(slot) % len(products)]
