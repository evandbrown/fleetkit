"""Boot one microVM with a hypervisor backend, exactly as hostd would, and check it end to end.

    python3 -m hostd.backends.bootcheck [--hypervisor '{"name": "cloud-hypervisor"}'] [--slot 99]
        [--vcpus 2] [--mem-mib 2048] [--console quiet] [--memory-pages thp]
        [--egress-url http://10.200.0.1:8081/index.html] [--out DIR]

Creates the microVM (tap, scope, hypervisor), waits for the guest daemon's /health, optionally
has the guest fetch a URL through /egress-check, samples the scope's cgroup and threads,
destroys it and runs verify-clean. Prints one NAME_RESULT=PASS|FAIL line per check (the
format images/host/hostcheck.sh prints) and exits non-zero if any failed. Run as root on a
Linux host with /dev/kvm, the bridge, the kernel and the rootfs in place.

--hypervisor is the spec's hypervisor section as JSON; options it leaves out take the
backend's defaults. --console and --memory-pages are the spec's microvm.console and
microvm.memory_pages; with a console other than verbose, CONSOLE_ARGS checks the guest's own
kernel command line carries its words, and with thp, THP_PAGES checks the host backs some of the
guest's memory with transparent huge pages. --out keeps the console log, /health and the sample there.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.parse
from typing import Any, Dict, List, Optional

from ..guest import GuestClient, GuestError
from ..model import CONSOLE_BOOT_ARGS, CONSOLES, DEFAULT_CONSOLE, DEFAULT_MEMORY_PAGES, MEMORY_PAGES, MicroVM
from ..runner import Runner
from . import make_hypervisor_backend
from .hypervisor import DEFAULT_KERNEL, DEFAULT_ROOTFS, RUN_ROOT


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="bootcheck", description=__doc__.split("\n\n")[0])
    p.add_argument("--hypervisor", default='{"name": "firecracker"}', help="the spec's hypervisor section (JSON)")
    p.add_argument("--binary", default=None, help="the hypervisor binary (default: its /usr/local/bin path)")
    p.add_argument("--kernel", default=DEFAULT_KERNEL)
    p.add_argument("--rootfs", default=DEFAULT_ROOTFS)
    p.add_argument("--run-root", default=RUN_ROOT)
    p.add_argument("--log-dir", default="/var/log/fleetkit/bootcheck")
    p.add_argument("--slot", type=int, default=99)
    p.add_argument("--vcpus", type=int, default=2)
    p.add_argument("--mem-mib", type=int, default=2048)
    p.add_argument("--console", choices=CONSOLES, default=DEFAULT_CONSOLE, help="the spec's microvm.console")
    p.add_argument("--memory-pages", choices=MEMORY_PAGES, default=DEFAULT_MEMORY_PAGES,
                   help="the spec's microvm.memory_pages")
    p.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for /health")
    p.add_argument("--egress-url", default=None, help="a URL the guest fetches itself (PASS needs HTTP 200)")
    p.add_argument("--hold", type=float, default=0.0, help="seconds to keep the ready microVM before sampling")
    p.add_argument("--fault", default=None, help="a guest fault (crash_on_start shows how a dead guest is seen)")
    p.add_argument("--out", default=None, help="directory for console.log, health.json, sample.json")
    args = p.parse_args(argv)

    hypervisor: Dict[str, Any] = json.loads(args.hypervisor)
    name = hypervisor.get("name", "firecracker")
    kwargs: Dict[str, Any] = {"kernel": args.kernel, "rootfs": args.rootfs, "run_root": args.run_root}
    if args.binary:
        kwargs["binary"] = args.binary
    backend = make_hypervisor_backend(name, Runner(), args.log_dir, **kwargs)
    results: Dict[str, str] = {}

    def res(check: str, ok: bool) -> None:
        results[check] = "PASS" if ok else "FAIL"
        print("%s_RESULT=%s" % (check, results[check]), flush=True)

    problems = backend.check_host()
    for prob in problems:
        print("host check: %s" % prob)
    res("HYPERVISOR_HOST", not problems)
    print("hypervisor: %s (%s)" % (name, backend.version()))
    if problems:
        return 1

    vm = MicroVM(id=MicroVM.new_id(args.slot), slot=args.slot, backend=backend.name,
                 address=backend.address(args.slot), vcpus=args.vcpus, mem_mib=args.mem_mib, fault=args.fault,
                 ready_timeout_s=args.timeout, max_lifetime_s=0, idle_timeout_s=0,
                 fixture_base_url=backend.fixture_base_url, hypervisor=dict(hypervisor, name=name),
                 console=args.console, memory_pages=args.memory_pages)
    print("microVM %s: %s console=%s memory_pages=%s" % (vm.id, json.dumps(backend.options(vm)), vm.console,
                                                       vm.memory_pages))
    print("argv: %s" % " ".join(backend.scope_argv(vm)))
    guest = GuestClient()
    health: Any = None
    sample: Dict[str, Any] = {}
    t0 = time.monotonic()
    try:
        backend.create(vm)
        while time.monotonic() - t0 < args.timeout:
            try:
                r = guest.health(vm.address, timeout=2.0)
                if r.status == 200 and isinstance(r.body, dict) and r.body.get("ready"):
                    health = r.body
                    break
            except GuestError:
                pass
            if not backend.alive(vm):
                print("exited during boot after %.1f s: %s" % (time.monotonic() - t0, backend.exit_info(vm)))
                break
            time.sleep(0.5)
        ready_s = time.monotonic() - t0
        print("guest /health after %.1f s: %s" % (ready_s, json.dumps(health)[:600] if health else None))
        res("GUEST_HEALTH", health is not None)
        if health is not None and vm.console != DEFAULT_CONSOLE:
            cmdline = str(health.get("kernel_cmdline") or "")
            print("guest kernel command line: %s" % cmdline)
            res("CONSOLE_ARGS", all(w in cmdline.split() for w in CONSOLE_BOOT_ARGS[vm.console]))
        if health is not None and args.egress_url:
            path = "/egress-check?" + urllib.parse.urlencode({"url": args.egress_url})
            try:
                r = guest._request(vm.address, "GET", path, timeout=20.0)
                body = r.body
            except GuestError as e:
                body = {"error": str(e)}
            print("guest egress-check: %s" % json.dumps(body)[:400])
            res("GUEST_EGRESS", isinstance(body, dict) and body.get("ok") is True and body.get("status") == 200)
        if health is not None:
            time.sleep(args.hold)
            sample = backend.sample(vm)
            print("sample: %s" % json.dumps(sample))
            res("SAMPLE", all(sample.get(k) is not None for k in ("cgroup_memory_current", "cpu_usage_usec",
                                                                    "cpu_vcpu_usec", "cpu_hypervisor_usec")))
            if vm.memory_pages == "thp":
                # guest memory the host backs with transparent huge pages (the scope's memory.stat anon_thp)
                res("THP_PAGES", (sample.get("anon_thp_bytes") or 0) > 0)
    except Exception as e:
        print("error: %s: %s" % (type(e).__name__, e))
        res("GUEST_HEALTH", False)
    finally:
        backend.destroy(vm)
        time.sleep(0.5)
        leftovers = backend.verify_clean()
        print("verify-clean: %s" % ("clean" if not leftovers else ", ".join(leftovers)))
        res("NO_LEFTOVER_VM", not leftovers)
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            if vm.console_log and os.path.exists(vm.console_log):
                shutil.copyfile(vm.console_log, os.path.join(args.out, "console.log"))
            log = backend.hypervisor_log_path(vm)
            if os.path.exists(log):
                shutil.copyfile(log, os.path.join(args.out, os.path.basename(log)))
            with open(os.path.join(args.out, "bootcheck.json"), "w") as f:
                json.dump({"hypervisor": backend.options(vm), "name": name, "version": backend.version(),
                           "console": vm.console, "memory_pages": vm.memory_pages,
                           "health": health, "sample": sample, "results": results}, f, indent=2)
    return 0 if all(v == "PASS" for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
