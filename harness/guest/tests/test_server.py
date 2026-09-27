"""The daemon's routes, exercised over real sockets with a stub Chromium (no browser)."""

from __future__ import annotations

import asyncio
import io

import pytest

from guestd import minihttp, server
from guestd.faults import parse_fault
from guestd.log import LogRing
from guestd.task import StepRecord, TaskResult


class StubChromium:
    """Looks like guestd.chromium.Chromium to the Daemon; nothing is launched."""

    def __init__(self) -> None:
        self.ready = False
        self.product = None
        self.client = object()
        self.started = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        pass

    def rss_bytes(self):
        return 12345


class StubRunner:
    """Replaces TaskRunner: answers at once, or waits for `gate` when given."""

    gate: asyncio.Event | None = None
    result_ok = True

    def __init__(self, client, log, fault=None) -> None:
        self.log = log

    async def run(self, req, t0_ns):
        if StubRunner.gate is not None:
            await StubRunner.gate.wait()
        self.log.info("stub step", task_id=req.task_id)
        steps = [StepRecord(n, i * 1000, i * 1000 + 500, 0.0005) for i, n in enumerate(("home", "search", "open_product", "add_to_cart", "verify_cart"))]
        if StubRunner.result_ok:
            return TaskResult(req.task_id, True, "ok", steps, 4.5, 1000, 3, "AAAA")
        return TaskResult(req.task_id, False, "assertion_failed", steps[:2], 2.0, 500, 2, None, failed_step="open_product", error="boom")


def _daemon(fault=None):
    log = LogRing(stream=io.StringIO())
    chromium = StubChromium()
    return server.Daemon(log, chromium, parse_fault(fault)), chromium


GOOD = {"task_id": "t1", "fixture_base_url": "http://fixture", "product_id": "p", "query": "q", "expected_title": "T"}


def test_health_metrics_logs_and_errors(monkeypatch):
    monkeypatch.setattr(server, "TaskRunner", StubRunner)
    StubRunner.gate = None
    StubRunner.result_ok = True

    async def go():
        daemon, chromium = _daemon()
        port = await daemon.start("127.0.0.1", 0)
        try:
            assert chromium.started == 1
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
            assert status == 503 and body["ready"] is False and body["chromium_version"] is None and "uptime_s" in body
            # a task before readiness is session_not_ready, with the full failure shape
            status, body = await minihttp.request_json("127.0.0.1", port, "POST", "/task", GOOD)
            assert status == 503 and body["failure_category"] == "session_not_ready" and body["steps"] == [] and body["failed_step"] is None
            chromium.ready = True
            chromium.product = "Chrome/154.0.0.0"
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
            assert status == 200 and body == {"ready": True, "chromium_version": "Chrome/154.0.0.0", "uptime_s": body["uptime_s"]}
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", "/metrics")
            assert status == 200 and set(body) == {"mem_total", "mem_available", "cached", "chromium_rss", "tasks_run", "tasks_failed"}
            assert body["chromium_rss"] == 12345 and body["tasks_run"] == 0
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", "/logs?since=0")
            assert status == 200 and body["lines"] and body["lines"][0]["seq"] == 1 and body["next"] == body["lines"][-1]["seq"]
            last = body["next"]
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", f"/logs?since={last}")
            assert status == 200 and body["lines"] == [] and body["next"] == last
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", "/logs?since=x")
            assert status == 400
            status, _ = await minihttp.request_json("127.0.0.1", port, "GET", "/nope")
            assert status == 404
            status, _ = await minihttp.request_json("127.0.0.1", port, "POST", "/health", {})
            assert status == 405
            status, body = await minihttp.request_json("127.0.0.1", port, "POST", "/task", {"task_id": "x"})
            assert status == 400 and "missing" in body["error"]
            status, _, _ = await minihttp.request("127.0.0.1", port, "POST", "/task", b"{not json")
            assert status == 400
            # a successful task: exact response keys, traceparent echo, clock, log tail
            tp = "00-11111111111111111111111111111111-2222222222222222-01"
            status, body = await minihttp.request_json("127.0.0.1", port, "POST", "/task", GOOD, headers={"traceparent": tp})
            assert status == 200
            assert set(body) == {
                "task_id", "ok", "failure_category", "failed_step", "error", "steps", "task_ms", "bytes_received", "request_count",
                "screenshot_b64", "guest_clock_ns", "traceparent", "log_tail",
            }
            assert body["ok"] is True and body["failure_category"] == "ok" and body["traceparent"] == tp
            assert [s["name"] for s in body["steps"]] == ["home", "search", "open_product", "add_to_cart", "verify_cart"]
            assert set(body["steps"][0]) == {"name", "dispatch_ns", "settle_ns", "duration_ms"}
            assert isinstance(body["guest_clock_ns"], int) and body["guest_clock_ns"] > 1_600_000_000 * 10**9
            assert [l["msg"] for l in body["log_tail"]][0] == "task start" and body["log_tail"][-1]["msg"] == "task end"
            assert all(l["task_id"] == "t1" for l in body["log_tail"])
            # a failed task keeps the same shape, with the steps completed so far
            StubRunner.result_ok = False
            status, body = await minihttp.request_json("127.0.0.1", port, "POST", "/task", dict(GOOD, task_id="t2"))
            assert status == 200 and body["ok"] is False and body["failure_category"] == "assertion_failed"
            assert body["failed_step"] == "open_product" and body["error"] == "boom" and [s["name"] for s in body["steps"]] == ["home", "search"]
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", "/metrics")
            assert body["tasks_run"] == 2 and body["tasks_failed"] == 1
        finally:
            await daemon.stop()

    asyncio.run(go())


def test_second_concurrent_task_gets_409(monkeypatch):
    monkeypatch.setattr(server, "TaskRunner", StubRunner)
    StubRunner.result_ok = True

    async def go():
        StubRunner.gate = asyncio.Event()
        daemon, chromium = _daemon()
        chromium.ready = True
        port = await daemon.start("127.0.0.1", 0)
        try:
            first = asyncio.create_task(minihttp.request_json("127.0.0.1", port, "POST", "/task", GOOD))
            await asyncio.sleep(0.1)
            status, body = await minihttp.request_json("127.0.0.1", port, "POST", "/task", dict(GOOD, task_id="t2"))
            assert status == 409 and body["ok"] is False and "t1" in body["error"] and "guest_clock_ns" in body
            status, _ = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
            assert status == 200
            StubRunner.gate.set()
            status, body = await first
            assert status == 200 and body["ok"] is True
        finally:
            StubRunner.gate = None
            await daemon.stop()

    asyncio.run(go())


def test_never_ready_fault_keeps_503_and_skips_chromium():
    async def go():
        daemon, chromium = _daemon("never_ready")
        port = await daemon.start("127.0.0.1", 0)
        try:
            assert chromium.started == 0
            chromium.ready = True  # even a ready browser is hidden by the fault
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
            assert status == 503 and body["ready"] is False and body["reason"] == "fault never_ready"
        finally:
            await daemon.stop()

    asyncio.run(go())


def test_hang_task_fault_never_answers_but_health_does(monkeypatch):
    monkeypatch.setattr(server, "TaskRunner", StubRunner)

    async def go():
        daemon, chromium = _daemon("hang_task")
        chromium.ready = True
        port = await daemon.start("127.0.0.1", 0)
        try:
            with pytest.raises(asyncio.TimeoutError):
                await minihttp.request_json("127.0.0.1", port, "POST", "/task", GOOD, timeout=0.5)
            status, _ = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
            assert status == 200
        finally:
            await daemon.stop()

    asyncio.run(go())


def test_crash_on_start_exits_nonzero():
    from guestd.__main__ import CRASH_ON_START_EXIT_CODE, main

    assert main(["--fault", "crash_on_start", "--port", "0"]) == CRASH_ON_START_EXIT_CODE
    assert main(["--fault", "bogus", "--port", "0"]) == 2
