"""A very small HTTP/1.1 layer on asyncio streams, enough for the daemon's four routes.

Used by the daemon's server, by the DevTools ``/json/version`` probe on loopback, by the
selftest's fixture-like site server and by the selftest's client. No keep-alive: every
response carries ``Connection: close``, which is what a proxy doing one request per
connection expects, and it keeps the parser trivial.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

MAX_HEADER_BYTES = 64 * 1024
MAX_BODY_BYTES = 8 * 1024 * 1024
HEADER_TIMEOUT_S = 30.0

REASONS = {
    200: "OK",
    204: "No Content",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Payload Too Large",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


class BadRequest(Exception):
    pass


@dataclass
class Request:
    method: str
    target: str
    path: str
    query: Dict[str, str]
    headers: Dict[str, str]
    body: bytes
    peer: Optional[Tuple[str, int]] = None

    def json(self) -> Any:
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise BadRequest(f"invalid JSON body: {e}") from None


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json"
    headers: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, status: int, obj: Any, **headers: str) -> "Response":
        data = json.dumps(obj, separators=(",", ":"), default=str).encode("utf-8")
        return cls(status, data, "application/json", dict(headers))

    def serialize(self) -> bytes:
        reason = REASONS.get(self.status, "Status")
        lines = [f"HTTP/1.1 {self.status} {reason}"]
        hdrs = {
            "Content-Type": self.content_type,
            "Content-Length": str(len(self.body)),
            "Connection": "close",
            "Cache-Control": "no-store",
        }
        hdrs.update(self.headers)
        for k, v in hdrs.items():
            lines.append(f"{k}: {v}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + self.body


Handler = Callable[[Request], Awaitable[Response]]


async def read_request(reader: asyncio.StreamReader, peer=None) -> Optional[Request]:
    """Parse one request. Returns None if the client closed before sending anything."""
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEADER_TIMEOUT_S)
    except asyncio.IncompleteReadError as e:
        if not e.partial.strip():
            return None
        raise BadRequest("truncated request head") from None
    except asyncio.LimitOverrunError:
        raise BadRequest("request head too large") from None
    except asyncio.TimeoutError:
        raise BadRequest("timeout reading request head") from None
    if len(head) > MAX_HEADER_BYTES:
        raise BadRequest("request head too large")
    try:
        text = head.decode("latin-1")
    except UnicodeDecodeError:
        raise BadRequest("undecodable request head") from None
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise BadRequest(f"bad request line: {lines[0]!r}")
    method, target = parts[0].upper(), parts[1]
    headers: Dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep:
            raise BadRequest(f"bad header line: {line!r}")
        headers[name.strip().lower()] = value.strip()
    length = 0
    if "content-length" in headers:
        try:
            length = int(headers["content-length"])
        except ValueError:
            raise BadRequest("bad Content-Length") from None
        if length < 0 or length > MAX_BODY_BYTES:
            raise BadRequest("body too large")
    elif headers.get("transfer-encoding", "").lower() == "chunked":
        raise BadRequest("chunked request bodies are not supported")
    body = b""
    if length:
        try:
            body = await asyncio.wait_for(reader.readexactly(length), HEADER_TIMEOUT_S)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            raise BadRequest("truncated request body") from None
    parsed = urllib.parse.urlsplit(target)
    query = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
    return Request(method, target, parsed.path or "/", query, headers, body, peer)


async def serve(handler: Handler, host: str, port: int, log=None) -> asyncio.base_events.Server:
    """Start serving ``handler`` and return the asyncio server (use ``.sockets`` for the port)."""

    async def on_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = None
        try:
            peer = writer.get_extra_info("peername")
        except Exception:  # pragma: no cover - defensive
            pass
        response: Optional[Response] = None
        try:
            request = await read_request(reader, peer)
            if request is None:
                return
            try:
                response = await handler(request)
            except BadRequest as e:
                response = Response.json(400, {"error": str(e)})
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - a handler bug must not kill the server
                if log is not None:
                    log.error("handler crashed", path=request.path, error=repr(e))
                response = Response.json(500, {"error": f"internal error: {e!r}"})
        except BadRequest as e:
            response = Response.json(400, {"error": str(e)})
        except (ConnectionError, asyncio.IncompleteReadError):
            response = None
        try:
            if response is not None:
                writer.write(response.serialize())
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
            except Exception:  # pragma: no cover - defensive
                pass

    return await asyncio.start_server(on_connection, host, port, limit=MAX_HEADER_BYTES, reuse_address=True)


def bound_port(server: asyncio.base_events.Server) -> int:
    for sock in server.sockets or []:
        return sock.getsockname()[1]
    raise RuntimeError("server has no sockets")


async def request(
    host: str,
    port: int,
    method: str,
    path: str,
    body: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
) -> Tuple[int, Dict[str, str], bytes]:
    """One HTTP/1.0-style request on a fresh connection; returns (status, headers, body)."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    try:
        hdrs = {"Host": f"{host}:{port}", "Connection": "close", "User-Agent": "fleetkit-guestd"}
        if body is not None:
            hdrs["Content-Length"] = str(len(body))
            hdrs.setdefault("Content-Type", "application/json")
        if headers:
            hdrs.update(headers)
        head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n"
        writer.write(head.encode("latin-1") + (body or b""))
        await writer.drain()
        # Servers that ignore Connection: close (Chromium's DevTools HTTP server does)
        # keep the socket open, so read by Content-Length or chunks, not to EOF.
        try:
            head_bytes = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
        except asyncio.IncompleteReadError:
            raise ConnectionError("no HTTP response head") from None
        lines = head_bytes.decode("latin-1").split("\r\n")
        status = int(lines[0].split(" ")[1])
        resp_headers: Dict[str, str] = {}
        for line in lines[1:]:
            name, s, value = line.partition(":")
            if s:
                resp_headers[name.strip().lower()] = value.strip()
        if "content-length" in resp_headers:
            payload = await asyncio.wait_for(reader.readexactly(int(resp_headers["content-length"])), timeout)
        elif resp_headers.get("transfer-encoding", "").lower() == "chunked":
            payload = _dechunk(await asyncio.wait_for(reader.read(), timeout))
        else:
            payload = await asyncio.wait_for(reader.read(), timeout)
    finally:
        writer.close()
    return status, resp_headers, payload


def _dechunk(data: bytes) -> bytes:
    out = bytearray()
    pos = 0
    while True:
        end = data.find(b"\r\n", pos)
        if end < 0:
            break
        size_text = data[pos:end].split(b";")[0].strip()
        try:
            size = int(size_text, 16)
        except ValueError:
            break
        if size == 0:
            break
        out += data[end + 2 : end + 2 + size]
        pos = end + 2 + size + 2
    return bytes(out)


async def request_json(host: str, port: int, method: str, path: str, obj: Any = None, **kw: Any) -> Tuple[int, Any]:
    body = None if obj is None else json.dumps(obj).encode("utf-8")
    status, _, payload = await request(host, port, method, path, body, **kw)
    try:
        return status, (json.loads(payload.decode("utf-8")) if payload else None)
    except ValueError:
        return status, payload
