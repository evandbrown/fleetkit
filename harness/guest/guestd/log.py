"""Structured logging: JSON lines to stdout (the console) and a ring buffer for /logs."""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional


class LogRing:
    """A bounded ring of structured log records with a monotonically increasing seq.

    Every record is also written as one JSON line to ``stream`` (stdout by default), so
    the same lines appear on the VM console or in ``docker logs`` and in ``GET /logs``.

    While a task runs, the server calls :meth:`hold` and later :meth:`release`: stream writes
    are queued and written after the timed region. In a microVM the stream is the serial
    console, where every byte is a trapped port write (a VM exit, and a nested one on a
    nested-virtualization host), so writing during the task would put harness I/O inside the
    measured time. The ring buffer is updated at once either way; only the console copy waits.
    """

    def __init__(self, capacity: int = 2000, stream=None, component: str = "guestd") -> None:
        self._buf: Deque[Dict[str, Any]] = deque(maxlen=capacity)
        self._seq = 0
        self._lock = threading.Lock()
        self._stream = stream if stream is not None else sys.stdout
        self._component = component
        self._held = 0
        self._pending: List[str] = []

    @property
    def seq(self) -> int:
        """Sequence number of the most recent record (0 when empty)."""
        return self._seq

    def log(self, level: str, msg: str, **fields: Any) -> Dict[str, Any]:
        with self._lock:
            self._seq += 1
            rec: Dict[str, Any] = {
                "seq": self._seq,
                "ts": time.time(),
                "level": level,
                "component": self._component,
                "msg": msg,
            }
            for key, value in fields.items():
                if value is not None:
                    rec[key] = value
            self._buf.append(rec)
            line = json.dumps(rec, default=str) + "\n"
            if self._held:
                self._pending.append(line)
                return rec
        self._write([line])
        return rec

    def hold(self) -> None:
        """Queue stream writes until the matching :meth:`release` (calls nest)."""
        with self._lock:
            self._held += 1

    def release(self) -> None:
        """End one :meth:`hold`; when none is left, write the queued lines in order."""
        with self._lock:
            self._held = max(0, self._held - 1)
            if self._held:
                return
            lines, self._pending = self._pending, []
        self._write(lines)

    def _write(self, lines: List[str]) -> None:
        if not lines:
            return
        try:
            self._stream.write("".join(lines))
            self._stream.flush()
        except (OSError, ValueError):
            pass

    def debug(self, msg: str, **fields: Any) -> Dict[str, Any]:
        return self.log("debug", msg, **fields)

    def info(self, msg: str, **fields: Any) -> Dict[str, Any]:
        return self.log("info", msg, **fields)

    def warning(self, msg: str, **fields: Any) -> Dict[str, Any]:
        return self.log("warning", msg, **fields)

    def error(self, msg: str, **fields: Any) -> Dict[str, Any]:
        return self.log("error", msg, **fields)

    def since(self, seq: int, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Records with ``seq`` greater than the given one, oldest first."""
        with self._lock:
            out = [dict(r) for r in self._buf if r["seq"] > seq]
        if limit is not None and len(out) > limit:
            out = out[-limit:]
        return out

    def tail(self, n: int) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._buf)
        return [dict(r) for r in items[-n:]] if n > 0 else []
