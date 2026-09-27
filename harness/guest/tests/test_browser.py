"""End to end with a real browser: skipped unless Chromium or Chrome is installed.

Runs the same code path as ``python3 -m guestd --selftest`` (the daemon over HTTP, the
bundled site on loopback) plus the failure categories the design defines.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from conftest import free_port
from guestd import chromium as chromium_mod
from guestd import minihttp
from guestd.faults import parse_fault
from guestd.log import LogRing
from guestd.selftest import SiteServer, run_selftest
from guestd.server import Daemon



def _chromium(log, binary):
    tmp = tempfile.mkdtemp(prefix="guestd-test-")
    return chromium_mod.Chromium(
        log, binary=binary, port=free_port(), user_data_dir=os.path.join(tmp, "profile"), stderr_path=os.path.join(tmp, "chromium.log")
    )


def test_selftest_passes(chromium_binary):
    assert asyncio.run(run_selftest(chromium_binary=chromium_binary, devtools_port=free_port())) == 0


def test_failure_categories(chromium_binary):
    async def go():
        log = LogRing(stream=open(os.devnull, "w"))
        site = SiteServer()
        host, site_port = await site.start("127.0.0.1", 0)
        base = f"http://{host}:{site_port}"
        chromium = _chromium(log, chromium_binary)
        daemon = Daemon(log, chromium, None)
        port = await daemon.start("127.0.0.1", 0)
        try:
            assert await chromium.wait_ready(60)
            product = site.products[0]
            good = {"task_id": "ok", "fixture_base_url": base, "product_id": product["id"], "query": product["query"], "expected_title": product["title"]}

            status, r = await minihttp.request_json("127.0.0.1", port, "POST", "/task", dict(good, expected_title="Wrong"), timeout=60)
            assert status == 200 and r["failure_category"] == "assertion_failed" and r["failed_step"] == "open_product"
            assert [s["name"] for s in r["steps"]] == ["home", "search"] and r["screenshot_b64"]

            status, r = await minihttp.request_json("127.0.0.1", port, "POST", "/task", dict(good, product_id="sku-nope"), timeout=60)
            assert r["failure_category"] == "assertion_failed" and r["failed_step"] == "search"

            status, r = await minihttp.request_json("127.0.0.1", port, "POST", "/task", dict(good, fixture_base_url=f"http://127.0.0.1:{free_port()}"), timeout=60)
            assert r["failure_category"] == "navigation_error" and r["failed_step"] == "home" and "net::" in r["error"]

            # a fresh browser context per task: the second good task sees an empty cart
            for i in range(2):
                status, r = await minihttp.request_json("127.0.0.1", port, "POST", "/task", dict(good, task_id=f"ok{i}"), timeout=60)
                assert status == 200 and r["ok"] is True, r
                assert r["bytes_received"] >= 200_000 and r["request_count"] >= 5
                names = [s["name"] for s in r["steps"]]
                assert names == ["home", "search", "open_product", "add_to_cart", "verify_cart"]
                assert abs(r["task_ms"] - r["steps"][-1]["settle_ns"] / 1e6) < 0.01

            # step and task timeouts through the same deadline mechanism
            daemon.fault = parse_fault("slow_step:3000")
            status, r = await minihttp.request_json("127.0.0.1", port, "POST", "/task", dict(good, step_timeout_ms=500, task_timeout_ms=10000), timeout=60)
            assert r["failure_category"] == "step_timeout" and r["failed_step"] == "search" and [s["name"] for s in r["steps"]] == ["home"]
            status, r = await minihttp.request_json("127.0.0.1", port, "POST", "/task", dict(good, step_timeout_ms=5000, task_timeout_ms=700), timeout=60)
            assert r["failure_category"] == "task_timeout" and r["failed_step"] == "search"
            daemon.fault = parse_fault("hang_step")
            status, r = await minihttp.request_json("127.0.0.1", port, "POST", "/task", dict(good, step_timeout_ms=400), timeout=60)
            assert r["failure_category"] == "step_timeout" and r["failed_step"] == "search"
            daemon.fault = None
            status, h = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
            assert status == 200 and h["ready"] is True
            status, m = await minihttp.request_json("127.0.0.1", port, "GET", "/metrics")
            assert m["tasks_run"] == 8 and m["tasks_failed"] == 6
        finally:
            await daemon.stop()
            await site.stop()

    asyncio.run(go())


def test_browser_crash_is_categorized_and_recovered(chromium_binary):
    async def go():
        log = LogRing(stream=open(os.devnull, "w"))
        site = SiteServer()
        host, site_port = await site.start("127.0.0.1", 0)
        chromium = _chromium(log, chromium_binary)
        daemon = Daemon(log, chromium, None)
        port = await daemon.start("127.0.0.1", 0)
        try:
            assert await chromium.wait_ready(60)
            product = site.products[0]
            good = {"task_id": "crash", "fixture_base_url": f"http://{host}:{site_port}", "product_id": product["id"], "query": product["query"], "expected_title": product["title"]}
            pid = chromium.pid

            async def kill_soon():
                await asyncio.sleep(0.05)
                os.kill(pid, 9)

            asyncio.create_task(kill_soon())
            status, r = await minihttp.request_json("127.0.0.1", port, "POST", "/task", good, timeout=60)
            assert status == 200 and r["ok"] is False and r["failure_category"] == "browser_crashed", r
            # the supervisor relaunches; /health goes 503 then 200 again
            assert await chromium.wait_ready(60)
            assert chromium.unexpected_exits == 1 and chromium.pid != pid
            status, r = await minihttp.request_json("127.0.0.1", port, "POST", "/task", dict(good, task_id="after"), timeout=60)
            assert status == 200 and r["ok"] is True, r
        finally:
            await daemon.stop()
            await site.stop()

    asyncio.run(go())
