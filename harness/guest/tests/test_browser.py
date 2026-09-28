"""End to end with a real browser: skipped unless Chromium or Chrome is installed.

Runs the same code path as ``python3 -m guestd --selftest`` (the daemon over HTTP, the
bundled site on loopback) plus the failure categories the design defines.
"""

from __future__ import annotations

import asyncio
import base64
import os
import tempfile

import pytest

from conftest import free_port
from guestd import chromium as chromium_mod
from guestd import minihttp
from guestd.faults import parse_fault
from guestd.log import LogRing
from guestd.procstat import GROUPS
from guestd.selftest import HOME_PAGE_BYTES, SiteServer, run_selftest
from guestd.server import Daemon
from guestd.task import STEP_NAMES


def _chromium(log, binary):
    tmp = tempfile.mkdtemp(prefix="guestd-test-")
    return chromium_mod.Chromium(
        log, binary=binary, port=free_port(), user_data_dir=os.path.join(tmp, "profile"), stderr_path=os.path.join(tmp, "chromium.log")
    )


def test_extra_flags_reach_the_running_browser(chromium_binary):
    async def go():
        log = LogRing(stream=open(os.devnull, "w"))
        tmp = tempfile.mkdtemp(prefix="guestd-test-")
        chromium = chromium_mod.Chromium(log, binary=chromium_binary, port=free_port(), user_data_dir=os.path.join(tmp, "profile"),
                                         stderr_path=os.path.join(tmp, "chromium.log"), extra_flags=["--renderer-process-limit=1"])
        daemon = Daemon(log, chromium, None)
        port = await daemon.start("127.0.0.1", 0)
        try:
            assert await chromium.wait_ready(60)
            status, h = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
            assert status == 200 and h["chromium_extra_flags"] == ["--renderer-process-limit=1"]
            assert h["chromium_flags"][-2:] == ["--renderer-process-limit=1", "about:blank"]
            if os.path.isdir("/proc/self"):
                assert h["chromium_running_flags"] == h["chromium_flags"]
        finally:
            await daemon.stop()

    asyncio.run(go())


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


def _assert_step_counters_sum(r):
    steps = r["steps"]
    assert [s["name"] for s in steps] == list(STEP_NAMES)
    for s in steps:
        assert isinstance(s["bytes_received"], int) and s["bytes_received"] >= 0, s
        assert isinstance(s["request_count"], int) and s["request_count"] >= 0, s
    assert sum(s["bytes_received"] for s in steps) == r["bytes_received"]
    assert sum(s["request_count"] for s in steps) == r["request_count"]
    # the padded home page and the tab's creation belong to home
    assert steps[0]["bytes_received"] >= HOME_PAGE_BYTES and steps[0]["request_count"] >= 1


def test_health_boot_facts_step_counters_samples_and_filmstrip(chromium_binary):
    async def go():
        log = LogRing(stream=open(os.devnull, "w"))
        site = SiteServer()
        host, site_port = await site.start("127.0.0.1", 0)
        chromium = _chromium(log, chromium_binary)
        daemon = Daemon(log, chromium, None)
        port = await daemon.start("127.0.0.1", 0)
        try:
            assert await chromium.wait_ready(60)
            status, h = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
            assert status == 200 and h["guestd_version"] and h["guestd_uptime_s"] == h["uptime_s"]
            assert 0 <= h["chromium_launch_s"] <= h["chromium_ready_s"] <= h["uptime_s"]
            assert h["chromium_flags"] == chromium_mod.chromium_flags(chromium.port, chromium.user_data_dir)
            # read back from the running browser process: Linux has /proc, macOS doesn't
            assert h["chromium_running_flags"] == (h["chromium_flags"] if os.path.isdir("/proc/self") else None)
            assert h["vcpus"] == os.cpu_count()

            product = site.products[0]
            good = {"task_id": "counters", "fixture_base_url": f"http://{host}:{site_port}", "product_id": product["id"], "query": product["query"], "expected_title": product["title"]}
            status, r = await minihttp.request_json("127.0.0.1", port, "POST", "/task", good, timeout=60)
            assert status == 200 and r["ok"] is True, r
            _assert_step_counters_sum(r)
            assert r["timing_valid"] is True and r["sample_interval_ms"] == 200 and "step_screenshots" not in r
            assert isinstance(r["guestd_cpu_ms"], float) and r["guestd_cpu_ms"] > 0
            if os.path.isfile("/proc/stat"):  # Linux: real samples of the real process tree
                samples = r["proc_samples"]
                assert samples and samples[-1]["t_ns"] >= r["steps"][-1]["settle_ns"]
                assert all(set(x["groups"]) == set(GROUPS) for x in samples)
                assert samples[-1]["groups"]["browser"]["procs"] == 1 and samples[-1]["groups"]["renderer"]["procs"] >= 1
            else:  # macOS: no /proc, no samples
                assert r["proc_samples"] == []

            # the filmstrip: one JPEG per step, inside the wall time, so timing is not valid
            status, r = await minihttp.request_json("127.0.0.1", port, "POST", "/task", dict(good, task_id="film", screenshot_each_step=True, sample_interval_ms=0), timeout=60)
            assert status == 200 and r["ok"] is True, r
            assert r["timing_valid"] is False and r["proc_samples"] == [] and r["sample_interval_ms"] == 0
            _assert_step_counters_sum(r)
            shots = r["step_screenshots"]
            assert [x["step"] for x in shots] == list(STEP_NAMES)
            for x in shots:
                raw = base64.b64decode(x["b64"])
                assert raw[:2] == b"\xff\xd8" and len(raw) > 1000, x["step"]
            assert base64.b64decode(r["screenshot_b64"])[:2] == b"\xff\xd8"  # the final screenshot is unchanged

            # a failed task still splits its counters over the steps it completed
            status, r = await minihttp.request_json("127.0.0.1", port, "POST", "/task", dict(good, task_id="bad", expected_title="Wrong", screenshot_each_step=True), timeout=60)
            assert r["failure_category"] == "assertion_failed" and r["failed_step"] == "open_product"
            assert [x["step"] for x in r["step_screenshots"]] == ["home", "search"]
            assert sum(s["bytes_received"] for s in r["steps"]) <= r["bytes_received"]
        finally:
            await daemon.stop()
            await site.stop()

    asyncio.run(go())
