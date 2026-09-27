"""python3 -m hostd --backend docker|firecracker --port 8090 [--metrics-period 1.0] [--dry-run] [--render ...]"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from typing import List, Optional

from . import __version__
from .backends import BACKENDS
from .backends.docker import DockerBackend
from .backends.firecracker import DEFAULT_FIRECRACKER, DEFAULT_KERNEL, DEFAULT_ROOTFS, FirecrackerBackend
from .manager import SessionManager
from .metrics import HostSampler
from .model import Defaults, Session, validate_fault
from .runner import Runner
from .server import HostdApp, Server
from .telemetry import Telemetry


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hostd", description="fleetkit host daemon: session lifecycle for one host")
    p.add_argument("--backend", choices=sorted(BACKENDS), required=True)
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--bind", default="127.0.0.1", help="listen address (default 127.0.0.1)")
    p.add_argument("--log-dir", default=os.environ.get("FLEETKIT_HOSTD_LOG_DIR", "results/dev/hostd"),
                   help="hostd.log, spans.jsonl, logs.jsonl, host_metrics.jsonl and sessions/<id>/console.log")
    p.add_argument("--otlp-endpoint", default=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318"),
                   help="OTLP/HTTP base URL; export is best-effort with a 2 s timeout")
    p.add_argument("--no-otlp", action="store_true", help="write JSONL only, never export")
    p.add_argument("--host-id", default=os.environ.get("FLEETKIT_HOST_ID") or "local",
                   help="fleetkit.host_id correlation key on every record (default: $FLEETKIT_HOST_ID, else 'local'; "
                        "never the hostname, which can carry the operator's name into a shared bundle)")
    p.add_argument("--dry-run", action="store_true",
                   help="render every command and config instead of executing; sessions become ready at once")
    p.add_argument("--no-metrics", action="store_true", help="disable the host metrics sampler")
    p.add_argument("--metrics-period", type=float, default=Defaults.METRICS_PERIOD_S,
                   help="seconds between host metrics samples (default %(default)s; 0.2 gives 5 Hz)")
    # docker
    p.add_argument("--image", default=os.environ.get("FLEETKIT_GUEST_IMAGE", "fleetkit-guest:dev"))
    p.add_argument("--network", default="fleetkit")
    # firecracker
    p.add_argument("--firecracker", default=os.environ.get("FLEETKIT_FIRECRACKER", DEFAULT_FIRECRACKER))
    p.add_argument("--kernel", default=os.environ.get("FLEETKIT_KERNEL", DEFAULT_KERNEL))
    p.add_argument("--rootfs", default=os.environ.get("FLEETKIT_ROOTFS", DEFAULT_ROOTFS))
    p.add_argument("--run-root", default="/run/fleetkit")
    # render one session's plan and exit
    p.add_argument("--render", action="store_true", help="print the commands and config for one session, then exit")
    p.add_argument("--slot", type=int, default=0)
    p.add_argument("--vcpus", type=int, default=2)
    p.add_argument("--mem-mib", type=int, default=2048)
    p.add_argument("--fault", default=None)
    return p


def make_backend(args: argparse.Namespace, runner: Runner):
    if args.backend == "docker":
        return DockerBackend(runner, args.log_dir, image=args.image, network=args.network)
    return FirecrackerBackend(runner, args.log_dir, firecracker_bin=args.firecracker, kernel=args.kernel,
                              rootfs=args.rootfs, run_root=args.run_root)


def render(args: argparse.Namespace) -> int:
    runner = Runner(dry_run=True)
    backend = make_backend(args, runner)
    fault = validate_fault(args.fault)
    s = Session(id=Session.new_id(args.slot), slot=args.slot, backend=backend.name, address=backend.address(args.slot),
                vcpus=args.vcpus, mem_mib=args.mem_mib, fault=fault, ready_timeout_s=60, max_lifetime_s=600,
                idle_timeout_s=120, fixture_base_url=backend.fixture_base_url)
    sys.stdout.write("# %s backend, slot %d, session %s, address %s\n" % (backend.name, s.slot, s.id, s.address))
    sys.stdout.write("# --- create ---\n")
    backend.create(s)
    created = runner.render_text()
    runner.rendered.clear()
    sys.stdout.write(created)
    sys.stdout.write("# --- destroy ---\n")
    backend.destroy(s)
    sys.stdout.write(runner.render_text())
    return 0


def display_path(path: str) -> str:
    """A form of ``path`` safe for logs that are bundled: relative to the working directory when
    it lies inside it, else its basename. Never the absolute path (it names the operator)."""
    cwd = os.path.abspath(os.getcwd())
    full = os.path.abspath(path)
    if full == cwd or full.startswith(cwd + os.sep):
        return os.path.relpath(full, cwd)
    return os.path.basename(full.rstrip(os.sep)) or path


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.metrics_period > 0:
        parser.error("--metrics-period must be greater than 0")
    if args.render:
        return render(args)

    os.makedirs(args.log_dir, exist_ok=True)
    tel = Telemetry("hostd", args.log_dir, otlp_endpoint=None if args.no_otlp else args.otlp_endpoint,
                    resource={"fleetkit.host_id": args.host_id, "fleetkit.backend": args.backend,
                              "service.version": __version__})
    runner = Runner(dry_run=args.dry_run, log=(lambda text: tel.log(text, severity="INFO", attrs={"dry_run": True}))
                    if args.dry_run else None)
    backend = make_backend(args, runner)
    if not args.dry_run:
        problems = backend.check_host()
        for prob in problems:
            tel.log("host check: %s" % prob, severity="WARN")
        fatal = [p for p in problems if args.backend == "firecracker" or "not reachable" in p]
        if fatal:
            tel.log("refusing to start: %s" % "; ".join(fatal), severity="ERROR")
            tel.close()
            return 2

    manager = SessionManager(backend, tel, dry_run=args.dry_run, host_id=args.host_id)
    sampler = None if args.no_metrics else HostSampler(manager, tel, period=args.metrics_period, host_id=args.host_id)
    app = HostdApp(manager, tel, sampler, args.host_id, dry_run=args.dry_run)
    try:
        server = Server(app, bind=args.bind, port=args.port)
    except OSError as e:
        tel.log("cannot listen on %s:%d: %s" % (args.bind, args.port, e), severity="ERROR")
        tel.close()
        return 2

    manager.start_reaper()
    if sampler:
        sampler.start()
    # hostd.log and logs.jsonl end up in the evidence bundle, so the log dir is shown relative to
    # the working directory (its basename when it lies outside): an absolute --log-dir would
    # otherwise carry the operator's home directory into a bundle that gets shared.
    tel.log("hostd %s listening on http://%s:%d backend=%s dry_run=%s log_dir=%s otlp=%s metrics_period=%s" % (
        __version__, args.bind, server.port, args.backend, args.dry_run, display_path(args.log_dir),
        "off" if args.no_otlp else args.otlp_endpoint, "off" if sampler is None else "%gs" % sampler.period))

    stop = threading.Event()

    def on_signal(signum: int, _frame) -> None:
        tel.log("signal %d: destroying live sessions and exiting" % signum, severity="WARN")
        stop.set()

    def warm_host_info() -> None:
        # GET /host/info is cached; computing it early keeps the IMDS lookup (up to 1 s off
        # EC2) and `firecracker --version` off the first request's latency.
        try:
            info = app.host_info()
            tel.log("host info cached: cpu_count=%s kvm=%s ec2=%s" % (
                info.get("cpu_count"), info.get("kvm"), "yes" if info.get("ec2") else "no"))
        except Exception as e:
            tel.log("host info failed: %s: %s" % (type(e).__name__, e), severity="WARN")

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    server.start_background()
    threading.Thread(target=warm_host_info, name="host-info", daemon=True).start()
    try:
        while not stop.wait(0.5):
            pass
    finally:
        manager.stop_reaper()
        if sampler:
            sampler.stop()
        manager.destroy_all()
        server.shutdown()
        leftovers = backend.verify_clean() if not args.dry_run else []
        tel.log("exit: verify-clean %s%s" % ("clean" if not leftovers else "leftovers", "" if not leftovers else ": " + ", ".join(leftovers)),
                severity="INFO" if not leftovers else "WARN")
        tel.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
