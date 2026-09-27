"""The CDP client against a fake DevTools websocket (websockets 10.x legacy server API)."""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from guestd.cdp import BrowserGone, CDPClient, CDPError
from guestd.clock import Deadline, DeadlineExpired


class FakeBrowser:
    """Answers commands by id; emits events on request; can drop the connection."""

    def __init__(self) -> None:
        self.received = []
        self.server = None
        self.ws = None

    async def handler(self, ws, path=None):
        self.ws = ws
        async for raw in ws:
            msg = json.loads(raw)
            self.received.append(msg)
            method = msg["method"]
            if method == "Target.attachToTarget":
                await ws.send(json.dumps({"id": msg["id"], "result": {"sessionId": "S1"}}))
            elif method == "Fake.error":
                await ws.send(json.dumps({"id": msg["id"], "error": {"code": -32000, "message": "nope"}}))
            elif method == "Fake.emit":
                # events first, then the response, so ordering is observable
                for ev in msg["params"]["events"]:
                    await ws.send(json.dumps(ev))
                await ws.send(json.dumps({"id": msg["id"], "result": {}}))
            elif method == "Fake.die":
                await ws.close()
                return
            elif method == "Fake.silent":
                pass
            else:
                await ws.send(json.dumps({"id": msg["id"], "result": {"echo": method, "sessionId": msg.get("sessionId")}}))

    async def start(self):
        self.server = await websockets.serve(self.handler, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}/devtools/browser/fake"

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()


def test_commands_sessions_events_and_errors():
    async def go():
        fake = FakeBrowser()
        url = await fake.start()
        client = CDPClient(url)
        await client.connect()
        try:
            r = await client.send("Browser.getVersion")
            assert r == {"echo": "Browser.getVersion", "sessionId": None}
            sid = (await client.send("Target.attachToTarget", {"targetId": "T", "flatten": True}))["sessionId"]
            assert sid == "S1"
            r = await client.send("Page.enable", session_id=sid)
            assert r["sessionId"] == "S1"
            assert fake.received[-1]["sessionId"] == "S1" and fake.received[-1]["params"] == {}
            with pytest.raises(CDPError) as ei:
                await client.send("Fake.error")
            assert ei.value.code == -32000 and "nope" in str(ei.value)

            # event queues: subscribe before acting, filter by session, predicate, drain
            q = client.subscribe(sid)
            other = client.subscribe("S2")
            seen = []
            client.add_listener(sid, lambda m, p: seen.append(m))
            events = [
                {"method": "Network.requestWillBeSent", "params": {"requestId": "1"}, "sessionId": "S1"},
                {"method": "Page.loadEventFired", "params": {"timestamp": 1}, "sessionId": "S2"},
                {"method": "Page.loadEventFired", "params": {"timestamp": 2}, "sessionId": "S1"},
                {"method": "Network.loadingFinished", "params": {"encodedDataLength": 10}, "sessionId": "S1"},
            ]
            await client.send("Fake.emit", {"events": events})
            dl = Deadline.after_ms(1000, "step")
            params = await q.wait_for("Page.loadEventFired", dl)
            assert params == {"timestamp": 2}
            assert seen == ["Network.requestWillBeSent", "Page.loadEventFired", "Network.loadingFinished"]
            assert q.drain() == 1  # the loadingFinished left behind
            assert (await other.wait_for("Page.loadEventFired", dl))["timestamp"] == 1
            with pytest.raises(DeadlineExpired):
                await q.wait_for("Page.loadEventFired", Deadline.after_ms(50, "step"))
            await client.send("Fake.emit", {"events": [{"method": "Page.frameNavigated", "params": {"frame": {"id": "a"}}, "sessionId": "S1"},
                                                       {"method": "Page.frameNavigated", "params": {"frame": {"id": "b"}}, "sessionId": "S1"}]})
            params = await q.wait_for("Page.frameNavigated", dl, predicate=lambda p: p["frame"]["id"] == "b")
            assert params["frame"]["id"] == "b"
            await client.send("Fake.emit", {"events": [{"method": "Inspector.targetCrashed", "params": {}, "sessionId": "S1"}]})
            with pytest.raises(BrowserGone):
                await q.wait_for("Page.loadEventFired", dl)
        finally:
            await client.close()
            await fake.stop()

    asyncio.run(go())


def test_connection_loss_fails_pending_and_queues():
    async def go():
        fake = FakeBrowser()
        url = await fake.start()
        client = CDPClient(url)
        await client.connect()
        try:
            q = client.subscribe(None)
            pending = asyncio.create_task(client.send("Fake.silent"))
            await asyncio.sleep(0.05)
            with pytest.raises(BrowserGone):
                await client.send("Fake.die")
            with pytest.raises(BrowserGone):
                await pending
            with pytest.raises(BrowserGone):
                await q.wait_for("Anything", Deadline.after_ms(1000, "step"))
            assert client.closed and "closed" in (client.close_reason or "")
            with pytest.raises(BrowserGone):
                await client.send("Browser.getVersion")
        finally:
            await client.close()
            await fake.stop()

    asyncio.run(go())


def test_deadline_cancels_inflight_command_cleanly():
    async def go():
        fake = FakeBrowser()
        url = await fake.start()
        client = CDPClient(url)
        await client.connect()
        try:
            with pytest.raises(DeadlineExpired):
                await Deadline.after_ms(50, "step").wait(client.send("Fake.silent"))
            assert client._pending == {}
            assert (await client.send("Browser.getVersion"))["echo"] == "Browser.getVersion"
        finally:
            await client.close()
            await fake.stop()

    asyncio.run(go())
