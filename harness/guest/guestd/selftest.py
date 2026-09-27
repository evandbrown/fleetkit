"""``python3 -m guestd --selftest``: prove the image without the harness.

Serves the bundled site (the smallest site with the task's selectors, home page padded
to 200 kB) on loopback, starts the daemon exactly as production does (real Chromium, the
same server and task code path, on an ephemeral loopback port), waits for ``/health``,
posts one ``/task`` and checks the answer. Exit status 0 on success.

The site server is also usable on its own (``--serve-site HOST:PORT``) to give a running
guest a target with the design's selectors, from the host, without the real fixture.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import time
from typing import Any, Dict, Optional, Tuple

from . import chromium as chromium_mod
from . import minihttp
from .log import LogRing
from .minihttp import Request, Response
from .server import Daemon

SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selftest_site")
HOME_PAGE_BYTES = 200_000

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
}

_FILLER_WORDS = (
    "lamp", "shade", "brass", "walnut", "linen", "runner", "bookend", "oiled", "stonewashed",
    "desk", "shelf", "table", "chair", "cotton", "wool", "oak", "maple", "ceramic", "glass", "steel",
)


def _filler(n_bytes: int) -> str:
    """Deterministic prose-like filler of about ``n_bytes``, hidden from the layout."""
    words = []
    size = 0
    i = 0
    while size < n_bytes:
        w = _FILLER_WORDS[(i * 7 + i // 3) % len(_FILLER_WORDS)]
        words.append(w)
        size += len(w) + 1
        i += 1
    return '<div hidden data-testid="filler">' + " ".join(words) + "</div>"


class SiteServer:
    """Static site with one templated product page; routes mirror the real fixture's."""

    def __init__(self, site_dir: str = SITE_DIR, home_bytes: int = HOME_PAGE_BYTES) -> None:
        self.site_dir = site_dir
        with open(os.path.join(site_dir, "products.json"), "r", encoding="utf-8") as f:
            self.products = json.load(f)
        self._server = None
        self.requests = 0
        self.home_bytes = home_bytes

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> Tuple[str, int]:
        self._server = await minihttp.serve(self.handle, host, port)
        return host, minihttp.bound_port(self._server)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None

    def _read(self, name: str) -> str:
        with open(os.path.join(self.site_dir, name), "r", encoding="utf-8") as f:
            return f.read()

    def render(self, path: str) -> Optional[Tuple[bytes, str]]:
        if path in ("/", "/index.html"):
            page = self._read("index.html")
            base = len(page.encode("utf-8"))
            if self.home_bytes > base:
                page = page.replace("<!--FILLER-->", _filler(self.home_bytes - base))
            return page.encode("utf-8"), CONTENT_TYPES[".html"]
        m = re.fullmatch(r"/p/([A-Za-z0-9_-]+)\.html", path)
        if m:
            product = next((p for p in self.products if p["id"] == m.group(1)), None)
            if product is None:
                return None
            page = self._read("product.html")
            for key in ("id", "title", "price", "blurb"):
                page = page.replace("{{" + key + "}}", _escape(str(product.get(key, ""))))
            return page.encode("utf-8"), CONTENT_TYPES[".html"]
        if path in ("/search.html", "/cart.html", "/app.js", "/style.css", "/products.json"):
            name = path.lstrip("/")
            with open(os.path.join(self.site_dir, name), "rb") as f:
                return f.read(), CONTENT_TYPES[os.path.splitext(name)[1]]
        return None

    async def handle(self, req: Request) -> Response:
        self.requests += 1
        if req.method not in ("GET", "HEAD"):
            return Response(405, b"method not allowed", "text/plain")
        rendered = self.render(req.path)
        if rendered is None:
            return Response(404, b"not found", "text/plain")
        body, ctype = rendered
        return Response(200, b"" if req.method == "HEAD" else body, ctype)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


async def serve_site_forever(host: str, port: int) -> None:
    site = SiteServer()
    bound_host, bound_port = await site.start(host, port)
    print(json.dumps({"msg": "selftest site listening", "host": bound_host, "port": bound_port, "products": [p["id"] for p in site.products]}), flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await site.stop()


async def run_selftest(
    chromium_binary: str = chromium_mod.CHROMIUM_BIN,
    devtools_port: int = chromium_mod.DEVTOOLS_PORT,
    ready_timeout_s: float = 60.0,
    step_timeout_ms: int = 10000,
    task_timeout_ms: int = 45000,
    fault=None,
) -> int:
    log = LogRing(component="guestd-selftest")
    site = SiteServer()
    site_host, site_port = await site.start("127.0.0.1", 0)
    fixture_base_url = f"http://{site_host}:{site_port}"
    log.info("selftest site listening", url=fixture_base_url)

    user_data_dir = os.path.join(os.environ.get("TMPDIR", "/tmp").rstrip("/"), "fleetkit-selftest-profile")
    stderr_path = os.path.join(os.environ.get("TMPDIR", "/tmp").rstrip("/"), "fleetkit-selftest-chromium.log")
    chromium = chromium_mod.Chromium(log, binary=chromium_binary, port=devtools_port, user_data_dir=user_data_dir, stderr_path=stderr_path)
    daemon = Daemon(log, chromium, fault)
    failures = []
    started = time.monotonic()
    try:
        port = await daemon.start("127.0.0.1", 0)
        # Readiness: poll /health like the host daemon does (250 ms, outside any timed region).
        health: Dict[str, Any] = {}
        while True:
            status, health = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
            if status == 200:
                break
            if time.monotonic() - started > ready_timeout_s:
                failures.append(f"/health did not return 200 within {ready_timeout_s} s: {health}")
                return _finish(log, failures, {})
            await asyncio.sleep(0.25)
        log.info("selftest: /health ready", health=health, after_ms=round((time.monotonic() - started) * 1000, 1))
        if not health.get("chromium_version"):
            failures.append("/health has no chromium_version")

        product = site.products[0]
        body = {
            "task_id": "selftest-1",
            "fixture_base_url": fixture_base_url,
            "product_id": product["id"],
            "query": product["query"],
            "expected_title": product["title"],
            "step_timeout_ms": step_timeout_ms,
            "task_timeout_ms": task_timeout_ms,
        }
        traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        status, result = await minihttp.request_json(
            "127.0.0.1", port, "POST", "/task", body, headers={"traceparent": traceparent}, timeout=task_timeout_ms / 1000 + 20
        )
        if status != 200:
            failures.append(f"POST /task returned {status}: {result}")
            return _finish(log, failures, result if isinstance(result, dict) else {})
        _check_task_result(result, product, traceparent, failures)

        status, m = await minihttp.request_json("127.0.0.1", port, "GET", "/metrics")
        for key in ("mem_total", "mem_available", "cached", "chromium_rss", "tasks_run", "tasks_failed"):
            if status != 200 or key not in m:
                failures.append(f"/metrics missing {key}")
        if isinstance(m, dict) and m.get("tasks_run") != 1:
            failures.append(f"/metrics tasks_run is {m.get('tasks_run')}, expected 1")
        status, logs = await minihttp.request_json("127.0.0.1", port, "GET", "/logs?since=0")
        if status != 200 or not isinstance(logs, dict) or not logs.get("lines"):
            failures.append("/logs?since=0 returned no lines")
        status, second = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
        if status != 200:
            failures.append(f"/health after the task returned {status}: {second}")
        return _finish(log, failures, result, chromium=chromium)
    finally:
        await daemon.stop()
        await site.stop()


def _check_task_result(result: Any, product: Dict[str, Any], traceparent: str, failures: list) -> None:
    if not isinstance(result, dict):
        failures.append("task result is not an object")
        return
    if result.get("ok") is not True or result.get("failure_category") != "ok":
        failures.append(f"task not ok: {result.get('failure_category')} at {result.get('failed_step')}: {result.get('error')}")
    names = [s.get("name") for s in result.get("steps", [])]
    if names != ["home", "search", "open_product", "add_to_cart", "verify_cart"]:
        failures.append(f"steps are {names}")
    for s in result.get("steps", []):
        if not (isinstance(s.get("dispatch_ns"), int) and isinstance(s.get("settle_ns"), int) and s["settle_ns"] >= s["dispatch_ns"]):
            failures.append(f"step {s.get('name')} has bad timestamps: {s}")
    if not isinstance(result.get("task_ms"), (int, float)) or result["task_ms"] <= 0:
        failures.append(f"task_ms is {result.get('task_ms')}")
    if result.get("bytes_received", 0) < HOME_PAGE_BYTES:
        failures.append(f"bytes_received {result.get('bytes_received')} < {HOME_PAGE_BYTES}")
    if result.get("request_count", 0) < 5:
        failures.append(f"request_count {result.get('request_count')} < 5")
    shot = result.get("screenshot_b64")
    try:
        raw = base64.b64decode(shot or "")
    except ValueError:
        raw = b""
    if len(raw) < 1000 or raw[:2] != b"\xff\xd8":
        failures.append("screenshot_b64 is not a JPEG")
    if result.get("traceparent") != traceparent:
        failures.append(f"traceparent not echoed: {result.get('traceparent')!r}")
    if not isinstance(result.get("guest_clock_ns"), int):
        failures.append("guest_clock_ns missing")
    if not isinstance(result.get("log_tail"), list) or not result["log_tail"]:
        failures.append("log_tail empty")


def _finish(log: LogRing, failures: list, result: Dict[str, Any], chromium=None) -> int:
    summary = {
        "selftest": "pass" if not failures else "fail",
        "failures": failures,
        "chromium": chromium.product if chromium is not None else None,
        "task_ms": result.get("task_ms"),
        "steps": [(s.get("name"), s.get("duration_ms")) for s in result.get("steps", [])],
        "bytes_received": result.get("bytes_received"),
        "request_count": result.get("request_count"),
        "screenshot_bytes": len(result.get("screenshot_b64") or "") * 3 // 4,
    }
    log.info("selftest result", **summary)
    print("SELFTEST " + ("PASS" if not failures else "FAIL") + ": " + json.dumps(summary, default=str), file=sys.stderr, flush=True)
    return 0 if not failures else 1
