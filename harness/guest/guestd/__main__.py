"""Entry point: ``python3 -m guestd [--selftest | --serve-site HOST:PORT]``.

Production has no flags: the daemon listens on 0.0.0.0:8080, reads the fault from
``FLEETKIT_FAULT`` and the spec's extra Chromium flags from ``FLEETKIT_CHROMIUM_EXTRA_FLAGS``
(a bad value is a configuration error: exit 2, so Chromium never runs other flags than it was
sent), and runs until SIGTERM (tini forwards it). The remaining options exist
for local proofs and tests.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys

from . import __version__
from . import chromium as chromium_mod
from .faults import parse_fault
from .log import LogRing
from .server import Daemon

CRASH_ON_START_EXIT_CODE = 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="guestd", description="fleetkit guest daemon")
    p.add_argument("--bind", default="0.0.0.0", help="address to listen on (default 0.0.0.0)")
    p.add_argument("--port", type=int, default=8080, help="port to listen on (default 8080)")
    p.add_argument("--chromium", default=os.environ.get("GUESTD_CHROMIUM", chromium_mod.CHROMIUM_BIN), help="chromium binary")
    p.add_argument("--devtools-port", type=int, default=int(os.environ.get("GUESTD_DEVTOOLS_PORT", chromium_mod.DEVTOOLS_PORT)))
    p.add_argument("--fault", default=None, help="override FLEETKIT_FAULT (tests)")
    p.add_argument("--selftest", action="store_true", help="serve the bundled site on loopback and run one task through the daemon")
    p.add_argument("--serve-site", metavar="HOST:PORT", default=None, help="only serve the bundled selftest site (a fixture stand-in)")
    p.add_argument("--version", action="version", version=f"guestd {__version__}")
    return p


async def run_daemon(args: argparse.Namespace, log: LogRing) -> int:
    fault = parse_fault(args.fault if args.fault is not None else os.environ.get("FLEETKIT_FAULT"))
    if fault is not None and fault.name == "crash_on_start":
        log.error("fault crash_on_start: exiting now", exit_code=CRASH_ON_START_EXIT_CODE)
        return CRASH_ON_START_EXIT_CODE
    extra = chromium_mod.decode_extra_flags(os.environ.get(chromium_mod.EXTRA_FLAGS_ENV))
    if extra:
        log.info("chromium extra flags", flags=extra)
    chromium = chromium_mod.Chromium(log, binary=args.chromium, port=args.devtools_port, extra_flags=extra)
    daemon = Daemon(log, chromium, fault)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_signal(signame: str) -> None:
        log.info("signal received, shutting down", signal=signame)
        stop.set()

    for signame in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, signame), on_signal, signame)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - non-POSIX loops
            pass
    await daemon.start(args.bind, args.port)
    try:
        await stop.wait()
    finally:
        await daemon.stop()
        log.info("guestd stopped")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    log = LogRing()
    if args.serve_site:
        from .selftest import serve_site_forever

        host, _, port = args.serve_site.rpartition(":")
        try:
            return asyncio.run(serve_site_forever(host or "127.0.0.1", int(port))) or 0
        except KeyboardInterrupt:
            return 0
    if args.selftest:
        from .selftest import run_selftest

        fault = parse_fault(args.fault if args.fault is not None else os.environ.get("FLEETKIT_FAULT"))
        return asyncio.run(run_selftest(chromium_binary=args.chromium, devtools_port=args.devtools_port, fault=fault))
    try:
        return asyncio.run(run_daemon(args, log))
    except ValueError as e:  # an unknown fault name is a configuration error
        log.error("configuration error", error=str(e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
