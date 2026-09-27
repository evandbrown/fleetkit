"""The daemon: the five routes on port 8080 and the one-task-at-a-time rule.

- ``GET /health``   200 ``{ready: true, chromium_version, uptime_s, ...boot facts}`` once
                    Chromium answers ``/json/version`` (and the browser socket is connected);
                    503 before, with the same fields.
- ``POST /task``    runs the task; 409 while another task is running; 503 with
                    ``failure_category: microvm_not_ready`` when Chromium is not ready.
- ``GET /metrics``  ``{mem_total, mem_available, cached, chromium_rss, tasks_run, tasks_failed}``
                    (bytes; null where the platform has no /proc).
- ``GET /logs?since=<seq>``  the ring buffer of structured log lines after ``seq``.
- ``GET /egress-check?url=<http URL>``  one plain HTTP GET from inside the guest, to prove
                    egress (through the host's NAT on the Firecracker backend).

The boot facts in ``/health``: ``guestd_version``; ``guestd_uptime_s`` (= ``uptime_s``,
seconds on the monotonic clock since the daemon started); ``kernel_uptime_s`` (the first
field of /proc/uptime); ``chromium_launch_s`` and ``chromium_ready_s`` (guestd uptime at
which the current Chromium process was launched and became ready; null before that and
while it is being relaunched); ``chromium_flags``; ``kernel_cmdline``; ``vcpus``
(``os.cpu_count()``); ``mem_total`` (bytes). Each /proc-derived one is null without /proc.
"""

from __future__ import annotations

import asyncio
import os
import time
import urllib.parse
from typing import Any, Dict, Optional

from . import __version__, metrics, minihttp
from . import chromium as chromium_mod
from .clock import never
from .faults import Fault
from .log import LogRing
from .minihttp import Request, Response
from .procstat import ProcSampler
from .task import TaskRequest, TaskRunner

LOG_TAIL_LIMIT = 200
# Delay before queued console lines are written after a task's response.
RELEASE_CONSOLE_AFTER_S = 0.1
LOGS_PAGE_LIMIT = 1000
EGRESS_TIMEOUT_S = 5.0

ALLOWED = {
    "/health": ("GET",),
    "/task": ("POST",),
    "/metrics": ("GET",),
    "/logs": ("GET",),
    "/egress-check": ("GET",),
}


class Daemon:
    def __init__(self, log: LogRing, chromium, fault: Optional[Fault] = None, proc_root: str = "/proc") -> None:
        self.log = log
        self.chromium = chromium
        self.fault = fault
        self.proc_root = proc_root
        self.started = time.monotonic()
        self.sampler = ProcSampler(proc_root, chromium_binary=getattr(chromium, "binary", chromium_mod.CHROMIUM_BIN), self_pid=os.getpid())
        self._static: Optional[Dict[str, Any]] = None
        self.tasks_run = 0
        self.tasks_failed = 0
        self.task_busy = False
        self.current_task_id: Optional[str] = None
        self._released: set = set()
        self._server = None

    # -- lifecycle ------------------------------------------------------------------

    async def start(self, host: str, port: int) -> int:
        self._server = await minihttp.serve(self.handle, host, port, self.log)
        bound = minihttp.bound_port(self._server)
        self.log.info("guestd listening", bind=host, port=bound, version=__version__, fault=str(self.fault) if self.fault else None)
        if self.fault is not None and self.fault.name == "never_ready":
            self.log.warning("fault never_ready: chromium will not be started; /health stays 503")
        else:
            await self.chromium.start()
        return bound

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 2.0)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
            self._server = None
        await self.chromium.stop()

    @property
    def ready(self) -> bool:
        if self.fault is not None and self.fault.name == "never_ready":
            return False
        return bool(self.chromium.ready)

    # -- routing --------------------------------------------------------------------

    async def handle(self, req: Request) -> Response:
        methods = ALLOWED.get(req.path)
        if methods is None:
            return Response.json(404, {"error": f"no route for {req.path}"})
        if req.method not in methods:
            return Response.json(405, {"error": f"{req.method} not allowed on {req.path}"}, Allow=", ".join(methods))
        if req.path == "/health":
            return self.health()
        if req.path == "/metrics":
            return self.metrics()
        if req.path == "/logs":
            return self.logs(req)
        if req.path == "/egress-check":
            return await self.egress_check(req)
        return await self.task(req)

    def _since_start(self, mono: Optional[float]) -> Optional[float]:
        return None if mono is None else round(mono - self.started, 3)

    def health(self) -> Response:
        if self._static is None:
            # Facts that do not change while the daemon runs, read once.
            self._static = {
                "kernel_cmdline": metrics.kernel_cmdline(self.proc_root),
                "vcpus": os.cpu_count(),
                "mem_total": metrics.meminfo(self.proc_root)["mem_total"],
            }
        c = self.chromium
        flags = getattr(c, "flags", None)
        if flags is None:
            flags = chromium_mod.chromium_flags(getattr(c, "port", chromium_mod.DEVTOOLS_PORT), getattr(c, "user_data_dir", chromium_mod.USER_DATA_DIR))
        uptime_s = round(time.monotonic() - self.started, 3)
        body = {
            "ready": self.ready,
            "chromium_version": c.product,
            "uptime_s": uptime_s,
            "guestd_version": __version__,
            "guestd_uptime_s": uptime_s,
            "kernel_uptime_s": metrics.kernel_uptime_s(self.proc_root),
            "chromium_launch_s": self._since_start(getattr(c, "launch_mono", None)),
            "chromium_ready_s": self._since_start(getattr(c, "ready_mono", None)),
            "chromium_flags": list(flags),
        }
        body.update(self._static)
        if not self.ready:
            body["reason"] = "fault never_ready" if (self.fault and self.fault.name == "never_ready") else "chromium not ready"
        return Response.json(200 if body["ready"] else 503, body)

    def metrics(self) -> Response:
        body: Dict[str, Any] = metrics.meminfo(self.proc_root)
        body["chromium_rss"] = self.chromium.rss_bytes()
        body["tasks_run"] = self.tasks_run
        body["tasks_failed"] = self.tasks_failed
        return Response.json(200, body)

    def logs(self, req: Request) -> Response:
        since_text = req.query.get("since", "0")
        try:
            since = int(since_text)
        except ValueError:
            return Response.json(400, {"error": "since must be an integer sequence number"})
        lines = self.log.since(since, limit=LOGS_PAGE_LIMIT)
        return Response.json(200, {"since": since, "lines": lines, "next": (lines[-1]["seq"] if lines else max(since, 0)), "latest": self.log.seq})

    async def egress_check(self, req: Request) -> Response:
        """One plain HTTP GET of ``url`` from inside the guest, bounded by EGRESS_TIMEOUT_S.

        ``ok`` means an HTTP response arrived (any status; the caller judges ``status``);
        a connection error or the timeout is ``ok: false`` with ``error``. Always 200 except
        for a missing or non-http ``url`` (400).
        """
        url = req.query.get("url")
        if not url:
            return Response.json(400, {"ok": False, "error": "url query parameter is required"})
        try:
            parts = urllib.parse.urlsplit(url)
            port = parts.port or 80
        except ValueError as e:
            return Response.json(400, {"ok": False, "url": url, "error": f"bad url: {e}"})
        if parts.scheme != "http" or not parts.hostname:
            return Response.json(400, {"ok": False, "url": url, "error": "url must be an http:// URL with a host"})
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        t0 = time.monotonic()
        try:
            status, _, payload = await asyncio.wait_for(
                minihttp.request(parts.hostname, port, "GET", path, timeout=EGRESS_TIMEOUT_S), EGRESS_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            body: Dict[str, Any] = {"ok": False, "url": url, "error": f"timed out after {EGRESS_TIMEOUT_S:g} s"}
        except Exception as e:  # noqa: BLE001 - any failure to fetch is the answer, not a crash
            body = {"ok": False, "url": url, "error": f"{type(e).__name__}: {e}"}
        else:
            body = {"ok": True, "url": url, "status": status, "bytes": len(payload)}
        body["ms"] = round((time.monotonic() - t0) * 1000, 3)
        self.log.info("egress check", **body)
        return Response.json(200, body)

    async def task(self, req: Request) -> Response:
        t0_ns = time.monotonic_ns()
        cpu0_ns = time.process_time_ns()
        guest_clock_ns = time.time_ns()
        traceparent = req.headers.get("traceparent")
        if self.fault is not None and self.fault.name == "hang_task":
            self.log.warning("fault hang_task: this request will never be answered")
            await never()
        try:
            task = TaskRequest.from_dict(req.json())
        except (ValueError, minihttp.BadRequest) as e:
            return Response.json(400, {"ok": False, "error": str(e)})
        common = {"guest_clock_ns": guest_clock_ns, "traceparent": traceparent}
        if self.task_busy:
            self.log.warning("task rejected: busy", task_id=task.task_id, running=self.current_task_id)
            return Response.json(409, dict(common, ok=False, task_id=task.task_id, error=f"task {self.current_task_id} is still running"))
        if not self.ready:
            return Response.json(
                503,
                dict(
                    common,
                    ok=False,
                    task_id=task.task_id,
                    failure_category="microvm_not_ready",
                    failed_step=None,
                    error="chromium is not ready",
                    steps=[],
                    task_ms=None,
                    bytes_received=0,
                    request_count=0,
                    screenshot_b64=None,
                    proc_samples=[],
                    sample_interval_ms=task.sample_interval_ms,
                    guestd_cpu_ms=None,
                    timing_valid=not task.screenshot_each_step,
                    log_tail=[],
                ),
            )
        self.task_busy = True
        self.current_task_id = task.task_id
        # Console writes wait until the response has gone out (see LogRing.hold).
        self.log.hold()
        asyncio.get_running_loop().call_later(RELEASE_CONSOLE_AFTER_S + task.task_timeout_ms / 1000.0, self._release_log_once, task.task_id)
        seq0 = self.log.seq
        self.log.info(
            "task start",
            task_id=task.task_id,
            product_id=task.product_id,
            query=task.query,
            fixture_base_url=task.fixture_base_url,
            step_timeout_ms=task.step_timeout_ms,
            task_timeout_ms=task.task_timeout_ms,
            sample_interval_ms=task.sample_interval_ms,
            screenshot_each_step=task.screenshot_each_step or None,
            traceparent=traceparent,
        )
        try:
            runner = TaskRunner(self.chromium.client, self.log, self.fault, sampler=self.sampler)
            result = await runner.run(task, t0_ns, cpu0_ns=cpu0_ns)
        finally:
            self.task_busy = False
            self.current_task_id = None
        self.tasks_run += 1
        if not result.ok:
            self.tasks_failed += 1
        self.log.info(
            "task end",
            task_id=task.task_id,
            ok=result.ok,
            failure_category=result.failure_category,
            failed_step=result.failed_step,
            task_ms=result.task_ms,
            bytes_received=result.bytes_received,
            request_count=result.request_count,
            guestd_cpu_ms=result.guestd_cpu_ms,
            proc_samples=len(result.proc_samples),
        )
        result.extra.update(common)
        result.extra["log_tail"] = self.log.since(seq0, limit=LOG_TAIL_LIMIT)
        # Release shortly after the handler returns, so the response is written first.
        asyncio.get_running_loop().call_later(RELEASE_CONSOLE_AFTER_S, self._release_log_once, task.task_id)
        return Response.json(200, result.as_dict())

    def _release_log_once(self, task_id: str) -> None:
        """Release the console hold taken for ``task_id``, once (a backstop timer may also fire)."""
        if task_id in self._released:
            self._released.discard(task_id)
            return
        self._released.add(task_id)
        self.log.release()
