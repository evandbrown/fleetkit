"""The daemon: the four routes on port 8080 and the one-task-at-a-time rule.

- ``GET /health``   200 ``{ready: true, chromium_version, uptime_s}`` once Chromium answers
                    ``/json/version`` (and the browser socket is connected); 503 before.
- ``POST /task``    runs the task; 409 while another task is running; 503 with
                    ``failure_category: session_not_ready`` when Chromium is not ready.
- ``GET /metrics``  ``{mem_total, mem_available, cached, chromium_rss, tasks_run, tasks_failed}``
                    (bytes; null where the platform has no /proc).
- ``GET /logs?since=<seq>``  the ring buffer of structured log lines after ``seq``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from . import __version__, metrics, minihttp
from .clock import never
from .faults import Fault
from .log import LogRing
from .minihttp import Request, Response
from .task import TaskRequest, TaskRunner

LOG_TAIL_LIMIT = 200
LOGS_PAGE_LIMIT = 1000

ALLOWED = {
    "/health": ("GET",),
    "/task": ("POST",),
    "/metrics": ("GET",),
    "/logs": ("GET",),
}


class Daemon:
    def __init__(self, log: LogRing, chromium, fault: Optional[Fault] = None) -> None:
        self.log = log
        self.chromium = chromium
        self.fault = fault
        self.started = time.monotonic()
        self.tasks_run = 0
        self.tasks_failed = 0
        self.task_busy = False
        self.current_task_id: Optional[str] = None
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
        return await self.task(req)

    def health(self) -> Response:
        body = {
            "ready": self.ready,
            "chromium_version": self.chromium.product,
            "uptime_s": round(time.monotonic() - self.started, 3),
        }
        if not self.ready:
            body["reason"] = "fault never_ready" if (self.fault and self.fault.name == "never_ready") else "chromium not ready"
        return Response.json(200 if body["ready"] else 503, body)

    def metrics(self) -> Response:
        body: Dict[str, Any] = metrics.meminfo()
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

    async def task(self, req: Request) -> Response:
        t0_ns = time.monotonic_ns()
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
                    failure_category="session_not_ready",
                    failed_step=None,
                    error="chromium is not ready",
                    steps=[],
                    task_ms=None,
                    bytes_received=0,
                    request_count=0,
                    screenshot_b64=None,
                    log_tail=[],
                ),
            )
        self.task_busy = True
        self.current_task_id = task.task_id
        seq0 = self.log.seq
        self.log.info(
            "task start",
            task_id=task.task_id,
            product_id=task.product_id,
            query=task.query,
            fixture_base_url=task.fixture_base_url,
            step_timeout_ms=task.step_timeout_ms,
            task_timeout_ms=task.task_timeout_ms,
            traceparent=traceparent,
        )
        try:
            runner = TaskRunner(self.chromium.client, self.log, self.fault)
            result = await runner.run(task, t0_ns)
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
        )
        result.extra.update(common)
        result.extra["log_tail"] = self.log.since(seq0, limit=LOG_TAIL_LIMIT)
        return Response.json(200, result.as_dict())
