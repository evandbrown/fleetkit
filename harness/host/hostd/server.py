"""The host daemon's HTTP API on port 8090 (section 4). Stdlib only, threaded."""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

from .manager import ApiError, RequestContext, SessionManager
from .metrics import HostSampler
from .telemetry import Telemetry, parse_baggage, parse_traceparent

_SESSION = re.compile(r"^/sessions/([A-Za-z0-9_.-]+)$")
_TASK = re.compile(r"^/sessions/([A-Za-z0-9_.-]+)/task$")


class HostdApp:
    """Routes requests to the manager; shared by every handler thread."""

    def __init__(self, manager: SessionManager, telemetry: Telemetry, sampler: Optional[HostSampler],
                 host_id: str, dry_run: bool = False):
        self.manager = manager
        self.tel = telemetry
        self.sampler = sampler
        self.host_id = host_id
        self.dry_run = dry_run
        self.started = time.time()

    def handle(self, method: str, path: str, body: Optional[Dict[str, Any]], ctx: RequestContext) -> Tuple[int, Any]:
        path = path.split("?", 1)[0].rstrip("/") or "/"
        m = self.manager
        if method == "GET" and path == "/health":
            return 200, {"ok": True, "backend": m.backend.name, "dry_run": self.dry_run, "host_id": self.host_id,
                         "uptime_s": time.time() - self.started, "sessions": m.counts(),
                         "fixture_base_url": m.backend.fixture_base_url}
        if path == "/sessions":
            if method == "GET":
                return 200, m.list_records()
            if method == "POST":
                return 200, m.create_sessions(body or {}, ctx)
        mt = _TASK.match(path)
        if mt and method == "POST":
            return m.run_task(mt.group(1), body or {}, ctx)
        ms = _SESSION.match(path)
        if ms:
            if method == "GET":
                return 200, m.get(ms.group(1)).record()
            if method == "DELETE":
                return 200, m.destroy(ms.group(1))
        if method == "GET" and path == "/host/metrics":
            if self.sampler is None:
                return 503, {"error": "metrics sampler disabled"}
            latest = self.sampler.latest or self.sampler.sample_once()
            return 200, latest
        if method == "GET" and path == "/host/verify-clean":
            leftovers = m.backend.verify_clean()
            return 200, {"clean": not leftovers, "leftovers": leftovers, "backend": m.backend.name}
        raise ApiError(404, "no route %s %s" % (method, path))


def make_handler(app: HostdApp):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "hostd/0.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet; we log ourselves
            pass

        def _ctx(self) -> RequestContext:
            baggage = parse_baggage(self.headers.get("baggage"))
            # The driver sends these as plain headers; W3C baggage carries the same keys.
            for header, key in (("X-Fleetkit-Run-Id", "fleetkit.run_id"), ("X-Fleetkit-Trial-Id", "fleetkit.trial_id"),
                                ("X-Fleetkit-Task-Id", "fleetkit.task_id")):
                value = self.headers.get(header)
                if value and key not in baggage:
                    baggage[key] = value
            return RequestContext(trace=parse_traceparent(self.headers.get("traceparent")), baggage=baggage)

        def _body(self) -> Optional[Dict[str, Any]]:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return None
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode())
            except ValueError:
                raise ApiError(400, "body is not JSON")
            if not isinstance(parsed, dict):
                raise ApiError(400, "body must be a JSON object")
            return parsed

        def _send(self, status: int, payload: Any) -> None:
            data = json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _dispatch(self, method: str) -> None:
            t0 = time.monotonic()
            status = 500
            try:
                body = self._body() if method in ("POST", "PUT") else None
                status, payload = app.handle(method, self.path, body, self._ctx())
            except ApiError as e:
                status, payload = e.status, {"error": e.message, **e.extra}
            except Exception as e:  # pragma: no cover - last resort
                app.tel.log("unhandled error on %s %s: %s: %s" % (method, self.path, type(e).__name__, e), severity="ERROR")
                status, payload = 500, {"error": "%s: %s" % (type(e).__name__, e)}
            try:
                self._send(status, payload)
            except (BrokenPipeError, ConnectionResetError):
                pass
            app.tel.log("%s %s -> %d (%.0f ms)" % (method, self.path, status, (time.monotonic() - t0) * 1000),
                        severity="DEBUG" if self.path.startswith("/host/") or self.path == "/health" else "INFO",
                        attrs={"http.method": method, "http.path": self.path, "http.status": status})

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

    return Handler


class Server:
    def __init__(self, app: HostdApp, bind: str = "127.0.0.1", port: int = 8090):
        self.httpd = ThreadingHTTPServer((bind, port), make_handler(app))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread: Optional[threading.Thread] = None

    def serve_forever(self) -> None:
        self.httpd.serve_forever(poll_interval=0.25)

    def start_background(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, name="http", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
