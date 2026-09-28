"""HTTP client for the guest daemon (section 4: /health, /task, /metrics, /logs)."""
from __future__ import annotations

import http.client
import json
import socket
import time
from typing import Any, Dict, Optional, Tuple


# The /health fields kept on the microVM as `guest_info`: what the guest is, not how it booted.
# chromium_running_flags is what the browser process runs with, read back from /proc in the guest.
GUEST_INFO_KEYS = ("guestd_version", "chromium_version", "chromium_flags", "chromium_extra_flags",
                   "chromium_running_flags", "kernel_cmdline", "vcpus", "mem_total")
# Kept only when the guest sends a value, so a microVM that doesn't use them records what it always did.
# console: the guest console mode the host passed in the environment (the docker backend; a VM's kernel
# command line, kernel_cmdline, shows its own).
GUEST_INFO_OPTIONAL_KEYS = ("console",)


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


def _seconds(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def boot_phases(resp: GuestResponse) -> Dict[str, Optional[float]]:
    """Host-clock timestamps of the guest's boot milestones, from the /health answer that found
    the microVM ready.

    t_host, the host time at which the guest produced the answer, is the send time plus half the
    round trip (the same estimate as clock_offset_ns). Uptimes count back from it:
    kernel_start_ts = t_host - kernel_uptime_s and guestd_start_ts = t_host - guestd_uptime_s.
    The guest reports Chromium's launch and ready moments on guestd's own uptime clock, so
    chromium_launch_ts = guestd_start_ts + chromium_launch_s, and likewise chromium_ready_ts.
    Each is None when one of its inputs is missing. A guest without guestd_uptime_s falls back to
    uptime_s, which is the same clock under its original name. On the docker backend
    kernel_uptime_s is the Docker VM's uptime, so kernel_start_ts means nothing there.
    """
    body = resp.body if isinstance(resp.body, dict) else {}
    t_host = resp.send_ns / 1e9 + resp.rtt_ns / 2e9
    kernel_uptime = _seconds(body.get("kernel_uptime_s"))
    guestd_uptime = _seconds(body.get("guestd_uptime_s"))
    if guestd_uptime is None:
        guestd_uptime = _seconds(body.get("uptime_s"))
    guestd_start = t_host - guestd_uptime if guestd_uptime is not None else None
    launch = _seconds(body.get("chromium_launch_s"))
    ready = _seconds(body.get("chromium_ready_s"))
    return {
        "kernel_start_ts": t_host - kernel_uptime if kernel_uptime is not None else None,
        "guestd_start_ts": guestd_start,
        "chromium_launch_ts": guestd_start + launch if guestd_start is not None and launch is not None else None,
        "chromium_ready_ts": guestd_start + ready if guestd_start is not None and ready is not None else None,
    }


def guest_info(body: Any) -> Dict[str, Any]:
    """GUEST_INFO_KEYS from a /health answer, None for each one the guest did not send; then each of
    GUEST_INFO_OPTIONAL_KEYS the guest sent a value for."""
    body = body if isinstance(body, dict) else {}
    out = {k: body.get(k) for k in GUEST_INFO_KEYS}
    out.update({k: body[k] for k in GUEST_INFO_OPTIONAL_KEYS if body.get(k) is not None})
    return out
