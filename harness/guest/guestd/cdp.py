"""Raw Chrome DevTools Protocol over one websocket, with flat CDP sessions.

One connection to the browser endpoint, one reader task. Commands are matched to
responses by id; events are fanned out by ``sessionId`` to listeners and event queues.
Tabs are attached with ``Target.attachToTarget {flatten: true}`` so every command and
event carries its ``sessionId`` at the top level of the message and there is no nesting
of ``Target.sendMessageToTarget``.

Built on Debian bookworm's ``python3-websockets`` 10.x (the legacy asyncio API:
``websockets.connect`` and ``async for message in ws``).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

import websockets
import websockets.exceptions

from .clock import Deadline

Event = Tuple[str, Dict[str, Any]]
Listener = Callable[[str, Dict[str, Any]], None]


class CDPError(Exception):
    """The browser answered a command with an error."""

    def __init__(self, method: str, code: Any, message: str, data: Any = None) -> None:
        self.method = method
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"{method}: {message} (code {code})" + (f": {data}" if data else ""))


class BrowserGone(Exception):
    """The DevTools connection closed, or the target crashed, while we needed it."""


class EventQueue:
    """Events of one CDP session (one attached target), in arrival order. A ``None`` item
    means the connection died."""

    def __init__(self, client: "CDPClient", session_id: Optional[str]) -> None:
        self._client = client
        self._session_id = session_id
        self._queue: "asyncio.Queue[Optional[Event]]" = asyncio.Queue()
        self._dead = False

    def _put(self, item: Optional[Event]) -> None:
        if item is None:
            self._dead = True
        self._queue.put_nowait(item)

    def drain(self) -> int:
        """Drop everything queued so far; call before dispatching an action."""
        n = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return n
            if item is None:
                self._dead = True
                self._queue.put_nowait(None)
                return n
            n += 1

    async def wait_for(
        self,
        method: str,
        deadline: Deadline,
        predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> Dict[str, Any]:
        """Consume events until one matches ``method`` (and ``predicate``); bounded by ``deadline``."""
        while True:
            item = await deadline.wait(self._queue.get(), f"waiting for {method}")
            if item is None:
                self._dead = True
                self._queue.put_nowait(None)
                raise BrowserGone("devtools connection closed while waiting for " + method)
            m, params = item
            if m == "Inspector.targetCrashed":
                raise BrowserGone("target crashed while waiting for " + method)
            if m == method and (predicate is None or predicate(params)):
                return params

    def close(self) -> None:
        self._client.unsubscribe(self._session_id, self)


class CDPClient:
    def __init__(self, ws_url: str, log=None) -> None:
        self._url = ws_url
        self._log = log
        self._ws = None
        self._reader_task: Optional[asyncio.Task] = None
        self._next_id = 0
        self._pending: Dict[int, "asyncio.Future[Dict[str, Any]]"] = {}
        self._queues: Dict[Optional[str], List[EventQueue]] = {}
        self._listeners: Dict[Optional[str], List[Listener]] = {}
        self._closed = False
        self._close_reason: Optional[str] = None
        self._done = asyncio.Event()
        self.events_seen = 0

    # -- connection -----------------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def close_reason(self) -> Optional[str]:
        return self._close_reason

    async def wait_closed(self) -> None:
        """Returns once the connection is gone (the reader exited), for supervisors."""
        await self._done.wait()

    async def connect(self, timeout: float = 10.0) -> None:
        self._ws = await asyncio.wait_for(
            websockets.connect(self._url, max_size=None, ping_interval=None, close_timeout=2),
            timeout,
        )
        self._reader_task = asyncio.get_running_loop().create_task(self._read_loop(), name="cdp-reader")

    async def close(self) -> None:
        self._closed = True
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), 3.0)
            except Exception:  # noqa: BLE001 - closing is best effort
                pass
        task, self._reader_task = self._reader_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._fail_all("devtools connection closed")

    # -- commands -------------------------------------------------------------------

    async def send(self, method: str, params: Optional[Dict[str, Any]] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Send one command and return its ``result``; raises CDPError or BrowserGone."""
        if self._closed or self._ws is None:
            raise BrowserGone(self._close_reason or "devtools connection is closed")
        self._next_id += 1
        mid = self._next_id
        msg: Dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
        if session_id is not None:
            msg["sessionId"] = session_id
        fut: "asyncio.Future[Dict[str, Any]]" = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        try:
            await self._ws.send(json.dumps(msg))
        except websockets.exceptions.ConnectionClosed as e:
            self._pending.pop(mid, None)
            raise BrowserGone(f"devtools connection closed: {e}") from None
        try:
            return await fut
        finally:
            self._pending.pop(mid, None)

    # -- events ---------------------------------------------------------------------

    def subscribe(self, session_id: Optional[str]) -> EventQueue:
        q = EventQueue(self, session_id)
        self._queues.setdefault(session_id, []).append(q)
        if self._closed:
            q._put(None)
        return q

    def unsubscribe(self, session_id: Optional[str], q: EventQueue) -> None:
        lst = self._queues.get(session_id)
        if lst and q in lst:
            lst.remove(q)
            if not lst:
                del self._queues[session_id]

    def add_listener(self, session_id: Optional[str], fn: Listener) -> None:
        self._listeners.setdefault(session_id, []).append(fn)

    def remove_listener(self, session_id: Optional[str], fn: Listener) -> None:
        lst = self._listeners.get(session_id)
        if lst and fn in lst:
            lst.remove(fn)
            if not lst:
                del self._listeners[session_id]

    # -- the reader -----------------------------------------------------------------

    async def _read_loop(self) -> None:
        reason = "devtools connection closed by the browser"
        try:
            async for raw in self._ws:  # type: ignore[union-attr]
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if "id" in msg:
                    self._on_response(msg)
                else:
                    self._on_event(msg)
        except websockets.exceptions.ConnectionClosed as e:
            reason = f"devtools connection closed: code={e.code} reason={e.reason!r}"
        except asyncio.CancelledError:
            reason = "reader cancelled"
            raise
        except Exception as e:  # noqa: BLE001
            reason = f"devtools reader failed: {e!r}"
            if self._log is not None:
                self._log.error("cdp reader failed", error=repr(e))
        finally:
            self._closed = True
            self._fail_all(reason)

    def _on_response(self, msg: Dict[str, Any]) -> None:
        fut = self._pending.pop(msg["id"], None)
        if fut is None or fut.done():
            return
        if "error" in msg:
            err = msg["error"] or {}
            fut.set_exception(CDPError(msg.get("method", "?"), err.get("code"), err.get("message", "error"), err.get("data")))
        else:
            fut.set_result(msg.get("result") or {})

    def _on_event(self, msg: Dict[str, Any]) -> None:
        self.events_seen += 1
        method = msg.get("method")
        if not method:
            return
        params = msg.get("params") or {}
        session_id = msg.get("sessionId")
        for fn in list(self._listeners.get(session_id, ())):
            try:
                fn(method, params)
            except Exception as e:  # noqa: BLE001 - a listener bug must not kill the reader
                if self._log is not None:
                    self._log.error("cdp listener failed", method=method, error=repr(e))
        for q in list(self._queues.get(session_id, ())):
            q._put((method, params))

    def _fail_all(self, reason: str) -> None:
        if self._close_reason is None:
            self._close_reason = reason
        self._closed = True
        self._done.set()
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(BrowserGone(reason))
        self._pending.clear()
        for lst in list(self._queues.values()):
            for q in lst:
                q._put(None)
