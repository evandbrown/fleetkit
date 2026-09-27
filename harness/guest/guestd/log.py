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
    """

    def __init__(self, capacity: int = 2000, stream=None, component: str = "guestd") -> None:
        self._buf: Deque[Dict[str, Any]] = deque(maxlen=capacity)
        self._seq = 0
        self._lock = threading.Lock()
        self._stream = stream if stream is not None else sys.stdout
        self._component = component

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
        try:
            self._stream.write(json.dumps(rec, default=str) + "\n")
            self._stream.flush()
        except (OSError, ValueError):
            pass
        return rec

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
