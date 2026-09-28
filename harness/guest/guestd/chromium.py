"""Chromium: launch with the exact flag list, wait until DevTools answers, keep one CDP
connection to the browser endpoint, and relaunch if the process dies.

Chromium is started as ``/usr/lib/chromium/chromium`` directly, not through Debian's
wrapper (which sources ``/etc/chromium.d/*`` and adds API keys, extensions and
background traffic). The VM (or container) is the security boundary; the sandbox is
dropped inside it.

A spec may add flags (``workload.chromium_extra_flags``, checked against
experiments/schema/chromium-flags.json before any run): they arrive in
``FLEETKIT_CHROMIUM_EXTRA_FLAGS`` (see ``decode_extra_flags``) and go after the base
flags, before the start page. With none, the flag list is exactly the base list.
``running_flags`` reads back what the running browser process was started with.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import shutil
import signal
import time
from typing import Any, Dict, List, Optional, Sequence

from . import metrics, minihttp
from .cdp import CDPClient

CHROMIUM_BIN = "/usr/lib/chromium/chromium"
DEVTOOLS_PORT = 9222
USER_DATA_DIR = "/tmp/profile"
STDERR_PATH = "/tmp/chromium.stderr.log"
START_PAGE = "about:blank"
# The spec's extra flags, from the host: the Docker backend sets it, the microVM init copies it
# from the kernel command line (fleetkit.chromium_extra_flags=...). Absent or empty: none.
EXTRA_FLAGS_ENV = "FLEETKIT_CHROMIUM_EXTRA_FLAGS"
MAX_EXTRA_FLAGS = 16
MAX_EXTRA_FLAG_CHARS = 512


def encode_extra_flags(flags: Sequence[str]) -> str:
    """The flags as one word safe on a kernel command line: unpadded URL-safe base64 of their JSON list.
    hostd writes the same encoding (hostd.model.encode_chromium_flags)."""
    raw = json.dumps(list(flags), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_extra_flags(text: Optional[str]) -> List[str]:
    """encode_extra_flags reversed, checked: a list of at most MAX_EXTRA_FLAGS strings, each a
    ``--flag``, with no line breaks or NULs, MAX_EXTRA_FLAG_CHARS at most together. Raises ValueError,
    so a guest never starts Chromium with flags other than the ones it was sent."""
    if not text:
        return []
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        flags = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError) as e:
        raise ValueError("%s is not an encoded flag list: %s" % (EXTRA_FLAGS_ENV, e)) from None
    if not isinstance(flags, list) or len(flags) > MAX_EXTRA_FLAGS:
        raise ValueError("%s must hold a list of at most %d flags" % (EXTRA_FLAGS_ENV, MAX_EXTRA_FLAGS))
    for f in flags:
        if not isinstance(f, str) or not f.startswith("--") or any(c in f for c in "\0\r\n"):
            raise ValueError("%s: %r is not a --flag" % (EXTRA_FLAGS_ENV, f))
    if sum(len(f) for f in flags) > MAX_EXTRA_FLAG_CHARS:
        raise ValueError("%s: more than %d characters of flags" % (EXTRA_FLAGS_ENV, MAX_EXTRA_FLAG_CHARS))
    return flags


def chromium_flags(port: int = DEVTOOLS_PORT, user_data_dir: str = USER_DATA_DIR,
                   extra: Sequence[str] = ()) -> List[str]:
    """The flag list from design section 2, in order, with any extra flags before the start page."""
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
        *extra,
        START_PAGE,
    ]


def read_process_flags(pid: Optional[int], proc_root: str = "/proc") -> Optional[List[str]]:
    """The arguments after argv[0] that process ``pid`` is running with, from ``/proc/<pid>/cmdline``;
    None when there is no such process or no /proc. Chromium's browser process keeps its
    NUL-separated arguments; a process that retitled itself (Chromium's children do) leaves one
    space-joined string, split on spaces here."""
    if pid is None:
        return None
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as f:
            raw = f.read()
    except OSError:
        return None
    args = raw.split(b"\0")
    while args and args[-1] == b"":  # the terminating NUL, and a retitled process's padding
        args.pop()
    if not args:
        return None  # a zombie or kernel thread
    if len(args) == 1 and b" " in args[0]:
        args = args[0].split(b" ")
    return [a.decode("utf-8", "replace") for a in args[1:]]


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
        extra_flags: Sequence[str] = (),
        proc_root: str = "/proc",
    ) -> None:
        self._log = log
        self.binary = binary
        self.extra_flags = list(extra_flags)
        self.proc_root = proc_root
        self.port = port
        self.user_data_dir = user_data_dir
        self.stderr_path = stderr_path
        self.poll_interval_s = poll_interval_s
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.client: Optional[CDPClient] = None
        self.version: Dict[str, Any] = {}
        self.launches = 0
        self.unexpected_exits = 0
        # time.monotonic() at which the current process was launched / became ready;
        # None before that and once it is gone (the daemon reports them in /health).
        self.launch_mono: Optional[float] = None
        self.ready_mono: Optional[float] = None
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

    @property
    def flags(self) -> List[str]:
        """The flag list this supervisor launches Chromium with."""
        return chromium_flags(self.port, self.user_data_dir, self.extra_flags)

    def running_flags(self) -> Optional[List[str]]:
        """The flags the running browser process was started with, read back from /proc; None while
        no process runs, or without /proc."""
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return None
        return read_process_flags(proc.pid, self.proc_root)

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
            self.launch_mono = self.ready_mono = None
            await asyncio.sleep(0.5)

    async def _launch(self) -> None:
        if not os.path.exists(self.binary):
            raise FileNotFoundError(f"chromium binary not found at {self.binary}")
        shutil.rmtree(self.user_data_dir, ignore_errors=True)
        os.makedirs(self.user_data_dir, exist_ok=True)
        argv = [self.binary] + self.flags
        env = dict(os.environ)
        env.setdefault("HOME", "/tmp")
        try:
            stderr = open(self.stderr_path, "ab", buffering=0)
        except OSError:
            stderr = asyncio.subprocess.DEVNULL  # type: ignore[assignment]
        self.launches += 1
        self.ready_mono = None
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
        self.launch_mono = t0

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
        self.ready_mono = time.monotonic()
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
        self.launch_mono = self.ready_mono = None
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
