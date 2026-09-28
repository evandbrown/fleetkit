"""The daemon's routes, exercised over real sockets with a stub Chromium (no browser)."""

from __future__ import annotations

import asyncio
import io
import os
import urllib.parse

import pytest

from guestd import minihttp, server
from guestd.chromium import chromium_flags
from guestd.faults import parse_fault
from guestd.log import LogRing
from guestd.selftest import SiteServer
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
    last = None  # (req, sampler, cpu0_ns) of the last call

    def __init__(self, client, log, fault=None, sampler=None) -> None:
        self.log = log
        self.sampler = sampler

    async def run(self, req, t0_ns, cpu0_ns=None):
        StubRunner.last = (req, self.sampler, cpu0_ns)
        if StubRunner.gate is not None:
            await StubRunner.gate.wait()
        self.log.info("stub step", task_id=req.task_id)
        steps = [StepRecord(n, i * 1000, i * 1000 + 500, 0.0005, 200, 1) for i, n in enumerate(("home", "search", "open_product", "add_to_cart", "verify_cart"))]
        if StubRunner.result_ok:
            r = TaskResult(req.task_id, True, "ok", steps, 4.5, 1000, 5, "AAAA")
        else:
            r = TaskResult(req.task_id, False, "assertion_failed", steps[:2], 2.0, 500, 2, None, failed_step="open_product", error="boom")
        r.sample_interval_ms = req.sample_interval_ms
        r.timing_valid = not req.screenshot_each_step
        r.guestd_cpu_ms = 1.5
        return r


def _daemon(fault=None, proc_root="/nonexistent-proc"):
    log = LogRing(stream=io.StringIO())
    chromium = StubChromium()
    return server.Daemon(log, chromium, parse_fault(fault), proc_root=proc_root), chromium


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
            # a task before readiness is microvm_not_ready, with the full failure shape
            status, body = await minihttp.request_json("127.0.0.1", port, "POST", "/task", GOOD)
            assert status == 503 and body["failure_category"] == "microvm_not_ready" and body["steps"] == [] and body["failed_step"] is None
            assert body["proc_samples"] == [] and body["sample_interval_ms"] == 200 and body["timing_valid"] is True and body["guestd_cpu_ms"] is None
            chromium.ready = True
            chromium.product = "Chrome/154.0.0.0"
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
            assert status == 200 and set(body) == set(HEALTH_KEYS) and body["ready"] is True and body["chromium_version"] == "Chrome/154.0.0.0"
            # no /proc under the injected root: every /proc-derived fact is null
            assert body["kernel_uptime_s"] is None and body["kernel_cmdline"] is None and body["mem_total"] is None
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
                "proc_samples", "sample_interval_ms", "guestd_cpu_ms", "timing_valid",
            }
            assert body["ok"] is True and body["failure_category"] == "ok" and body["traceparent"] == tp
            assert [s["name"] for s in body["steps"]] == ["home", "search", "open_product", "add_to_cart", "verify_cart"]
            assert set(body["steps"][0]) == {"name", "dispatch_ns", "settle_ns", "duration_ms", "bytes_received", "request_count"}
            assert body["timing_valid"] is True and body["sample_interval_ms"] == 200 and body["proc_samples"] == []
            # the daemon hands the runner its sampler and the CPU clock read at receipt
            req, sampler, cpu0_ns = StubRunner.last
            assert sampler is daemon.sampler and isinstance(cpu0_ns, int)
            assert (req.sample_interval_ms, req.screenshot_each_step) == (200, False)
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
            # the two new request fields reach the runner and shape the answer
            StubRunner.result_ok = True
            status, body = await minihttp.request_json(
                "127.0.0.1", port, "POST", "/task", dict(GOOD, task_id="t3", sample_interval_ms=0, screenshot_each_step=True)
            )
            assert status == 200 and body["timing_valid"] is False and body["sample_interval_ms"] == 0
            assert (StubRunner.last[0].sample_interval_ms, StubRunner.last[0].screenshot_each_step) == (0, True)
            for bad in ({"sample_interval_ms": -1}, {"sample_interval_ms": 5}, {"sample_interval_ms": "200"}, {"screenshot_each_step": "yes"}):
                status, body = await minihttp.request_json("127.0.0.1", port, "POST", "/task", dict(GOOD, **bad))
                assert status == 400, bad
        finally:
            await daemon.stop()

    asyncio.run(go())


HEALTH_KEYS = (
    "ready", "chromium_version", "uptime_s", "guestd_version", "guestd_uptime_s", "kernel_uptime_s",
    "chromium_launch_s", "chromium_ready_s", "chromium_flags", "chromium_extra_flags", "chromium_running_flags",
    "kernel_cmdline", "vcpus", "mem_total",
)


def test_health_boot_facts_from_a_fake_proc(tmp_path):
    (tmp_path / "uptime").write_text("12.34 20.00\n")
    (tmp_path / "cmdline").write_text("console=ttyS0 reboot=k panic=1 fleetkit.slot=3\n")
    (tmp_path / "meminfo").write_text("MemTotal:        2014592 kB\nMemFree:          100000 kB\nMemAvailable:    1800000 kB\n")

    async def go():
        daemon, chromium = _daemon(proc_root=str(tmp_path))
        port = await daemon.start("127.0.0.1", 0)
        try:
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
            assert status == 503 and set(body) == set(HEALTH_KEYS) | {"reason"}
            assert body["guestd_version"] == server.__version__ and body["guestd_uptime_s"] == body["uptime_s"]
            assert body["kernel_uptime_s"] == 12.34
            assert body["kernel_cmdline"] == "console=ttyS0 reboot=k panic=1 fleetkit.slot=3"
            assert body["mem_total"] == 2014592 * 1024 and body["vcpus"] == os.cpu_count()
            assert body["chromium_launch_s"] is None and body["chromium_ready_s"] is None
            # the stub has no port or profile dir: the production defaults
            assert body["chromium_flags"] == chromium_flags() and "--remote-debugging-port=9222" in body["chromium_flags"]
            assert body["chromium_extra_flags"] == [] and body["chromium_running_flags"] is None  # no browser runs
            chromium.launch_mono = daemon.started + 0.25
            chromium.ready_mono = daemon.started + 1.5
            chromium.ready = True
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", "/health")
            assert status == 200 and body["chromium_launch_s"] == 0.25 and body["chromium_ready_s"] == 1.5
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", "/metrics")
            assert body["mem_total"] == 2014592 * 1024 and body["mem_available"] == 1800000 * 1024
        finally:
            await daemon.stop()

    asyncio.run(go())


class _Silent:
    """A TCP server that accepts and never answers (for the egress timeout)."""

    def __init__(self) -> None:
        self.server = None
        self.writers = []

    async def start(self) -> int:
        async def on_conn(reader, writer):
            self.writers.append(writer)
            await asyncio.sleep(3600)

        self.server = await asyncio.start_server(on_conn, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for w in self.writers:
            w.close()
        self.server.close()


def test_egress_check(monkeypatch):
    from conftest import free_port

    monkeypatch.setattr(server, "EGRESS_TIMEOUT_S", 0.3)

    async def go():
        site = SiteServer()
        host, site_port = await site.start("127.0.0.1", 0)
        silent = _Silent()
        silent_port = await silent.start()
        daemon, chromium = _daemon()
        port = await daemon.start("127.0.0.1", 0)
        try:
            # works whether or not Chromium is ready: it is about the guest's network
            url = f"http://{host}:{site_port}/"
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", "/egress-check?url=" + urllib.parse.quote(url, safe=""))
            assert status == 200 and set(body) == {"ok", "url", "status", "ms", "bytes"}
            assert body["ok"] is True and body["url"] == url and body["status"] == 200 and body["bytes"] == len(site.render("/")[0])
            assert isinstance(body["ms"], float) and body["ms"] >= 0
            # an HTTP error status is still egress: ok, and the caller judges the status
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", f"/egress-check?url=http://{host}:{site_port}/missing")
            assert status == 200 and body["ok"] is True and body["status"] == 404
            # a query string on the target survives when the caller encodes the url
            q = urllib.parse.quote(f"http://{host}:{site_port}/products.json?x=1&y=2", safe="")
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", "/egress-check?url=" + q)
            assert body["ok"] is True and body["status"] == 200 and body["url"].endswith("?x=1&y=2")
            # refused: ok false with an error
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", f"/egress-check?url=http://127.0.0.1:{free_port()}/")
            assert status == 200 and set(body) == {"ok", "url", "error", "ms"} and body["ok"] is False and body["error"]
            # no answer: the timeout
            status, body = await minihttp.request_json("127.0.0.1", port, "GET", f"/egress-check?url=http://127.0.0.1:{silent_port}/", timeout=5)
            assert status == 200 and body["ok"] is False and "timed out" in body["error"] and 250 <= body["ms"] < 2000
            # bad requests
            for bad in ("/egress-check", "/egress-check?url=", "/egress-check?url=https://example.com/",
                        "/egress-check?url=ftp://x/", "/egress-check?url=http:///nohost", "/egress-check?url=http://h:99999/"):
                status, body = await minihttp.request_json("127.0.0.1", port, "GET", bad)
                assert status == 400 and body["ok"] is False, bad
            status, _ = await minihttp.request_json("127.0.0.1", port, "POST", "/egress-check", {})
            assert status == 405
            lines = [l for l in daemon.log.tail(50) if l["msg"] == "egress check"]
            assert len(lines) == 5
        finally:
            await daemon.stop()
            await silent.stop()
            await site.stop()

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
