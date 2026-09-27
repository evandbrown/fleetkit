"""Chromium: launch with the exact flag list, wait until DevTools answers, keep one CDP
connection to the browser endpoint, and relaunch if the process dies.

Chromium is started as ``/usr/lib/chromium/chromium`` directly, not through Debian's
wrapper (which sources ``/etc/chromium.d/*`` and adds API keys, extensions and
background traffic). The VM (or container) is the security boundary; the sandbox is
dropped inside it.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import time
from typing import Any, Dict, List, Optional

from . import metrics, minihttp
from .cdp import CDPClient

CHROMIUM_BIN = "/usr/lib/chromium/chromium"
DEVTOOLS_PORT = 9222
USER_DATA_DIR = "/tmp/profile"
STDERR_PATH = "/tmp/chromium.stderr.log"


def chromium_flags(port: int = DEVTOOLS_PORT, user_data_dir: str = USER_DATA_DIR) -> List[str]:
    """The flag list from design section 2, in order."""
    return [
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--disable-default-apps",
        "--window-size=1280,800",
        "about:blank",
    ]


class Chromium:
    """Supervises one Chromium process and its browser-level DevTools connection."""

    def __init__(
        self,
        log,
        binary: str = CHROMIUM_BIN,
        port: int = DEVTOOLS_PORT,
        user_data_dir: str = USER_DATA_DIR,
        stderr_path: str = STDERR_PATH,
        poll_interval_s: float = 0.1,
    ) -> None:
        self._log = log
        self.binary = binary
        self.port = port
        self.user_data_dir = user_data_dir
        self.stderr_path = stderr_path
        self.poll_interval_s = poll_interval_s
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.client: Optional[CDPClient] = None
        self.version: Dict[str, Any] = {}
        self.launches = 0
        self.unexpected_exits = 0
        self._stopping = False
        self._supervisor: Optional[asyncio.Task] = None
        self._ready = asyncio.Event()

    # -- state ----------------------------------------------------------------------

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self.client is not None and not self.client.closed

    @property
    def product(self) -> Optional[str]:
        return self.version.get("Browser")

    @property
    def pid(self) -> Optional[int]:
        return self.proc.pid if self.proc is not None else None

    def rss_bytes(self) -> Optional[int]:
        return metrics.rss_of_processes(self.binary)

    async def wait_ready(self, timeout: Optional[float] = None) -> bool:
        """Wait until Chromium is ready; a stale flag (connection just died) is cleared here."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                await asyncio.wait_for(self._ready.wait(), remaining)
            except asyncio.TimeoutError:
                return False
            if self.ready:
                return True
            self._ready.clear()

    # -- lifecycle ------------------------------------------------------------------

    async def start(self) -> None:
        """Launch and supervise. Returns once the supervisor task is running (not when ready)."""
        if self._supervisor is not None:
            return
        self._stopping = False
        self._supervisor = asyncio.get_running_loop().create_task(self._supervise(), name="chromium-supervisor")

    async def stop(self) -> None:
        self._stopping = True
        self._ready.clear()
        sup, self._supervisor = self._supervisor, None
        if sup is not None and not sup.done():
            sup.cancel()
            try:
                await sup
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self._teardown_connection()
        await self._kill_process()

    async def _supervise(self) -> None:
        while not self._stopping:
            try:
                await self._launch()
                await self._await_devtools()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._log.error("chromium launch failed", error=repr(e))
                await self._teardown_connection()
                await self._kill_process()
                if self._stopping:
                    return
                await asyncio.sleep(1.0)
                continue
            # Wake on whichever comes first: the process exiting or the DevTools
            # connection dropping (the browser can be gone before wait() notices).
            proc, client = self.proc, self.client
            assert proc is not None and client is not None
            exited = asyncio.ensure_future(proc.wait())
            dropped = asyncio.ensure_future(client.wait_closed())
            try:
                await asyncio.wait({exited, dropped}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for fut in (exited, dropped):
                    if not fut.done():
                        fut.cancel()
            self._ready.clear()
            if self._stopping:
                return
            self.unexpected_exits += 1
            if proc.returncode is None:
                self._log.error("devtools connection dropped with chromium still running; killing it", pid=proc.pid, reason=client.close_reason)
                await self._kill_process()
            rc = proc.returncode
            self._log.error("chromium exited unexpectedly", returncode=rc, stderr_tail=self._stderr_tail(20))
            await self._teardown_connection()
            self.proc = None
            await asyncio.sleep(0.5)

    async def _launch(self) -> None:
        if not os.path.exists(self.binary):
            raise FileNotFoundError(f"chromium binary not found at {self.binary}")
        shutil.rmtree(self.user_data_dir, ignore_errors=True)
        os.makedirs(self.user_data_dir, exist_ok=True)
        argv = [self.binary] + chromium_flags(self.port, self.user_data_dir)
        env = dict(os.environ)
        env.setdefault("HOME", "/tmp")
        try:
            stderr = open(self.stderr_path, "ab", buffering=0)
        except OSError:
            stderr = asyncio.subprocess.DEVNULL  # type: ignore[assignment]
        self.launches += 1
        t0 = time.monotonic()
        self.proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=stderr,
            stderr=stderr,
            env=env,
            start_new_session=True,
        )
        if hasattr(stderr, "close"):
            stderr.close()
        self._log.info("chromium launched", pid=self.proc.pid, launch=self.launches, argv=argv)
        self._launch_t0 = t0

    async def _await_devtools(self) -> None:
        """Poll /json/version on loopback until it answers, then connect the browser socket."""
        proc = self.proc
        assert proc is not None
        while True:
            if proc.returncode is not None:
                raise RuntimeError(f"chromium exited with {proc.returncode} before devtools answered")
            try:
                status, _, payload = await minihttp.request("127.0.0.1", self.port, "GET", "/json/version", timeout=2.0)
                if status == 200:
                    self.version = json.loads(payload.decode("utf-8"))
                    break
            except (OSError, ConnectionError, ValueError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(self.poll_interval_s)
        ws_url = self.version.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("devtools /json/version had no webSocketDebuggerUrl")
        client = CDPClient(ws_url, self._log)
        await client.connect()
        info = await asyncio.wait_for(client.send("Browser.getVersion"), 10.0)
        self.version.setdefault("Browser", info.get("product"))
        self.client = client
        self._ready.set()
        self._log.info(
            "chromium ready",
            pid=proc.pid,
            product=info.get("product"),
            protocol=info.get("protocolVersion"),
            devtools_ms=round((time.monotonic() - self._launch_t0) * 1000, 1),
        )

    async def _teardown_connection(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            await client.close()

    async def _kill_process(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(proc.wait(), 5.0)
        except asyncio.TimeoutError:
            self._log.warning("chromium did not exit after SIGKILL", pid=proc.pid)

    def _stderr_tail(self, n: int) -> List[str]:
        try:
            with open(self.stderr_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 16384))
                lines = f.read().decode("utf-8", "replace").splitlines()
            return lines[-n:]
        except OSError:
            return []
