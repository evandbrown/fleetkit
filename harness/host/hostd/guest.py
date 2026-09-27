"""HTTP client for the guest daemon (section 4: /health, /task, /metrics, /logs)."""
from __future__ import annotations

import http.client
import json
import socket
import time
from typing import Any, Dict, Optional, Tuple


class GuestError(Exception):
    """The guest could not be reached or did not answer in time."""


class GuestResponse:
    def __init__(self, status: int, body: Any, rtt_ns: int, send_ns: int):
        self.status = status
        self.body = body
        self.rtt_ns = rtt_ns
        self.send_ns = send_ns      # realtime ns at which the request was sent


class GuestClient:
    """Plain http.client so the proxy deadline is a real socket timeout."""

    def _request(self, address: str, method: str, path: str, body: Optional[Dict[str, Any]] = None,
                 timeout: float = 5.0, headers: Optional[Dict[str, str]] = None) -> GuestResponse:
        host, _, port = address.rpartition(":")
        hdrs = {"Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection(host, int(port), timeout=timeout)
        send_ns = time.time_ns()
        t0 = time.monotonic_ns()
        try:
            conn.request(method, path, body=data, headers=hdrs)
            resp = conn.getresponse()
            raw = resp.read()
        except (OSError, http.client.HTTPException, socket.timeout) as e:
            raise GuestError("%s %s %s: %s: %s" % (method, address, path, type(e).__name__, e)) from e
        finally:
            conn.close()
        rtt_ns = time.monotonic_ns() - t0
        try:
            parsed = json.loads(raw.decode() or "null")
        except ValueError:
            parsed = {"raw": raw[:500].decode(errors="replace")}
        return GuestResponse(resp.status, parsed, rtt_ns, send_ns)

    def health(self, address: str, timeout: float = 1.0) -> GuestResponse:
        return self._request(address, "GET", "/health", timeout=timeout)

    def task(self, address: str, body: Dict[str, Any], timeout: float, traceparent: Optional[str] = None) -> GuestResponse:
        headers = {"traceparent": traceparent} if traceparent else None
        return self._request(address, "POST", "/task", body=body, timeout=timeout, headers=headers)

    def metrics(self, address: str, timeout: float = 2.0) -> GuestResponse:
        return self._request(address, "GET", "/metrics", timeout=timeout)

    def logs(self, address: str, since: int = 0, timeout: float = 2.0) -> GuestResponse:
        return self._request(address, "GET", "/logs?since=%d" % since, timeout=timeout)
