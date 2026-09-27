"""HTTP client for the host daemon (design section 4, port 8090). Standard library only."""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_TIMEOUT_S = 10.0


class HostError(Exception):
    def __init__(self, message: str, status: int | None = None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


class HostUnreachable(HostError):
    """Transport-level failure: connection refused, reset, or the client timeout fired."""


@dataclass
class Reply:
    status: int
    body: object
    send_ts: float
    recv_ts: float
    wall_ms: float
    transport_error: str | None = None


class HostClient:
    def __init__(self, base_url: str, run_id: str | None = None, timeout_s: float = DEFAULT_TIMEOUT_S):
        self.base_url = base_url.rstrip("/")
        self.run_id = run_id
        self.timeout_s = timeout_s

    # ---- transport ---------------------------------------------------------------------
    def request(self, method: str, path: str, body=None, timeout_s: float | None = None,
                headers: dict | None = None, raise_on_error: bool = True) -> Reply:
        data = None
        hdrs = {"Accept": "application/json"}
        if self.run_id:
            hdrs["X-Fleetkit-Run-Id"] = self.run_id
        if headers:
            hdrs.update({k: v for k, v in headers.items() if v})
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=data, headers=hdrs, method=method)
        send_ts = time.time()
        t0 = time.monotonic()
        status, parsed, terr = 0, None, None
        try:
            with urllib.request.urlopen(req, timeout=timeout_s or self.timeout_s) as resp:
                status = resp.status
                parsed = _parse(resp.read())
        except urllib.error.HTTPError as exc:
            status = exc.code
            parsed = _parse(exc.read())
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
            terr = f"{exc.__class__.__name__}: {getattr(exc, 'reason', exc)}"
        recv_ts = time.time()
        reply = Reply(status, parsed, send_ts, recv_ts, (time.monotonic() - t0) * 1000.0, terr)
        if raise_on_error:
            if terr:
                raise HostUnreachable(f"{method} {path}: {terr}")
            if status >= 400:
                raise HostError(f"{method} {path}: HTTP {status}: {_short(parsed)}", status, parsed)
        return reply

    # ---- API ---------------------------------------------------------------------------
    def health(self) -> dict:
        return self.request("GET", "/health").body

    def create_microvms(self, create_request: dict, trace_headers: dict | None = None) -> list[dict]:
        body = self.request("POST", "/microvms", body=create_request, headers=trace_headers, timeout_s=30.0).body
        if isinstance(body, dict) and "microvms" in body:
            body = body["microvms"]
        if not isinstance(body, list):
            raise HostError(f"POST /microvms: unexpected body {_short(body)}", body=body)
        return body

    def list_microvms(self) -> list[dict]:
        body = self.request("GET", "/microvms").body
        if isinstance(body, dict) and "microvms" in body:
            body = body["microvms"]
        return body if isinstance(body, list) else []

    def get_microvm(self, microvm_id: str) -> dict:
        return self.request("GET", f"/microvms/{microvm_id}").body

    def run_task(self, microvm_id: str, payload: dict, timeout_s: float,
                 trace_headers: dict | None = None) -> Reply:
        """Never raises on HTTP status; transport errors come back in ``Reply.transport_error``."""
        return self.request("POST", f"/microvms/{microvm_id}/task", body=payload, timeout_s=timeout_s,
                            headers=trace_headers, raise_on_error=False)

    def delete_microvm(self, microvm_id: str, trace_headers: dict | None = None) -> Reply:
        return self.request("DELETE", f"/microvms/{microvm_id}", headers=trace_headers,
                            timeout_s=30.0, raise_on_error=False)

    def host_metrics(self, timeout_s: float = 1.0) -> dict:
        return self.request("GET", "/host/metrics", timeout_s=timeout_s).body

    def host_info(self, timeout_s: float = 10.0) -> dict | None:
        """GET /host/info (static host facts); None when the daemon lacks it or does not answer."""
        reply = self.request("GET", "/host/info", timeout_s=timeout_s, raise_on_error=False)
        if reply.transport_error or reply.status != 200 or not isinstance(reply.body, dict):
            return None
        return reply.body

    def verify_clean(self) -> dict:
        body = self.request("GET", "/host/verify-clean", timeout_s=30.0).body
        if not isinstance(body, dict):
            raise HostError(f"GET /host/verify-clean: unexpected body {_short(body)}", body=body)
        return body


def _parse(raw: bytes):
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")


def _short(obj, n: int = 200) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return s if len(s) <= n else s[:n] + "..."
