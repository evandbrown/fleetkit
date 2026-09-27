"""Firecracker backend: one microVM per session on the fcbr0 bridge (Linux hosts).

Sections 2-4 of the design, per slot s:
  tap fc-<s> attached to fcbr0; guest IP 10.200.0.(10+s); MAC 06:00:0A:C8:00:<10+s hex>;
  kernel `ip=10.200.0.<10+s>::10.200.0.1:255.255.255.0:vm<s>:eth0:off:10.42.0.2`;
  `fleetkit.fault=<name>` on the command line when a fault is requested;
  VM config JSON with the read-only shared rootfs and the console log path;
  `systemd-run --scope --unit fc-vm<s>` with MemoryMax and CPUQuota, which gives a
  cgroup v2 leaf for memory.current, memory.peak and cpu.stat;
  SIGKILL teardown: kill the scope, delete the tap, remove the run directory.

The serial console (console=ttyS0) is Firecracker's stdout, redirected to the
per-VM console log; Firecracker's own log goes to firecracker.log next to it.
Firecracker adds `root=/dev/vda ro` itself for a root drive marked read-only.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import time
from typing import Any, Dict, List, Optional

from ..model import Session
from ..runner import CommandError, Runner
from .base import Backend, BackendError

BRIDGE = "fcbr0"
GATEWAY = "10.200.0.1"
NETMASK = "255.255.255.0"
RESOLVER = "10.42.0.2"          # the VPC resolver
GUEST_PORT = 8080
RUN_ROOT = "/run/fleetkit"
MEM_OVERHEAD_MIB = 256          # Firecracker process + page tables on top of guest memory
CGROUP_ROOT = "/sys/fs/cgroup"

DEFAULT_FIRECRACKER = "/usr/local/bin/firecracker"
DEFAULT_KERNEL = "/var/lib/fleetkit/vmlinux"
DEFAULT_ROOTFS = "/var/lib/fleetkit/guest.ext4"


class FirecrackerBackend(Backend):
    name = "firecracker"
    max_slots = 200                 # 10+s <= 209 keeps every guest inside 10.200.0.0/24
    fixture_base_url = "http://%s:8081" % GATEWAY

    def __init__(self, runner: Runner, log_dir: str, firecracker_bin: str = DEFAULT_FIRECRACKER,
                 kernel: str = DEFAULT_KERNEL, rootfs: str = DEFAULT_ROOTFS, bridge: str = BRIDGE,
                 run_root: str = RUN_ROOT, resolver: str = RESOLVER, gateway: str = GATEWAY,
                 mem_overhead_mib: int = MEM_OVERHEAD_MIB, extra_boot_args: str = ""):
        super().__init__(runner, log_dir)
        self.firecracker = firecracker_bin
        self.kernel = kernel
        self.rootfs = rootfs
        self.bridge = bridge
        self.run_root = run_root
        self.resolver = resolver
        self.gateway = gateway
        self.mem_overhead_mib = mem_overhead_mib
        self.extra_boot_args = extra_boot_args
        self.fixture_base_url = "http://%s:8081" % gateway
        self._cgroup_paths: Dict[str, str] = {}

    # --- per-slot naming (section 3) --------------------------------------
    @staticmethod
    def host_number(slot: int) -> int:
        n = 10 + slot
        if not 10 <= n <= 254:
            raise ValueError("slot %d has no address in 10.200.0.0/24" % slot)
        return n

    @classmethod
    def guest_ip(cls, slot: int) -> str:
        return "10.200.0.%d" % cls.host_number(slot)

    @classmethod
    def guest_mac(cls, slot: int) -> str:
        return "06:00:0A:C8:00:%02X" % cls.host_number(slot)

    @staticmethod
    def tap(slot: int) -> str:
        return "fc-%d" % slot

    @staticmethod
    def unit(slot: int) -> str:
        return "fc-vm%d" % slot

    def address(self, slot: int) -> str:
        return "%s:%d" % (self.guest_ip(slot), GUEST_PORT)

    def ip_arg(self, slot: int) -> str:
        return "ip=%s::%s:%s:vm%d:eth0:off:%s" % (self.guest_ip(slot), self.gateway, NETMASK, slot, self.resolver)

    def boot_args(self, session: Session) -> str:
        args = ["console=ttyS0", "reboot=k", "panic=1", "pci=off", self.ip_arg(session.slot)]
        if session.fault:
            args.append("fleetkit.fault=%s" % session.fault)
        if self.extra_boot_args:
            args.append(self.extra_boot_args)
        return " ".join(args)

    def run_dir(self, session: Session) -> str:
        return os.path.join(self.run_root, session.id)

    def console_log_path(self, session: Session) -> str:
        return os.path.join(self.log_dir, "sessions", session.id, "console.log")

    def vm_config(self, session: Session) -> Dict[str, Any]:
        """Firecracker rejects unknown keys, so only its own schema goes here."""
        return {
            "boot-source": {"kernel_image_path": self.kernel, "boot_args": self.boot_args(session)},
            "drives": [{"drive_id": "rootfs", "path_on_host": self.rootfs,
                        "is_root_device": True, "is_read_only": True}],
            "machine-config": {"vcpu_count": session.vcpus, "mem_size_mib": session.mem_mib, "smt": False},
            "network-interfaces": [{"iface_id": "eth0", "guest_mac": self.guest_mac(session.slot),
                                    "host_dev_name": self.tap(session.slot)}],
            "logger": {"log_path": self.firecracker_log_path(session), "level": "Warning",
                       "show_level": True, "show_log_origin": False},
        }

    def firecracker_log_path(self, session: Session) -> str:
        return os.path.join(self.log_dir, "sessions", session.id, "firecracker.log")

    def sidecar(self, session: Session) -> Dict[str, Any]:
        """What a human needs next to vm.json: where the console went and which slot this is."""
        return {"session_id": session.id, "slot": session.slot, "guest_ip": self.guest_ip(session.slot),
                "guest_mac": self.guest_mac(session.slot), "tap": self.tap(session.slot),
                "unit": self.unit(session.slot) + ".scope", "console_log": self.console_log_path(session),
                "firecracker_log": self.firecracker_log_path(session), "fault": session.fault}

    def scope_argv(self, session: Session) -> List[str]:
        d = self.run_dir(session)
        return ["systemd-run", "--scope", "--quiet",
                "--unit", self.unit(session.slot),
                "--description", "fleetkit session %s" % session.id,
                "-p", "MemoryMax=%dM" % (session.mem_mib + self.mem_overhead_mib),
                "-p", "MemorySwapMax=0",
                "-p", "CPUQuota=%d%%" % (session.vcpus * 100),
                self.firecracker,
                "--api-sock", os.path.join(d, "fc.sock"),
                "--config-file", os.path.join(d, "vm.json")]

    # --- lifecycle ---------------------------------------------------------
    def create(self, session: Session) -> None:
        slot = session.slot
        d = self.run_dir(session)
        tap = self.tap(slot)
        session.handle.update({"tap": tap, "unit": self.unit(slot) + ".scope", "run_dir": d})
        session.console_log = self.console_log_path(session)
        self.runner.mkdir(d)
        self.runner.mkdir(os.path.dirname(session.console_log))
        self.runner.write_file(os.path.join(d, "vm.json"), json.dumps(self.vm_config(session), indent=2) + "\n")
        self.runner.write_file(os.path.join(d, "session.json"), json.dumps(self.sidecar(session), indent=2) + "\n")
        self.runner.write_file(self.firecracker_log_path(session), "")   # Firecracker wants it to exist
        try:
            self.runner.run(["ip", "tuntap", "add", "dev", tap, "mode", "tap"], timeout=20)
            self.runner.run(["ip", "link", "set", tap, "master", self.bridge], timeout=20)
            self.runner.run(["ip", "link", "set", tap, "up"], timeout=20)
        except CommandError as e:
            raise BackendError("tap setup failed: %s" % e) from e
        handle = self.runner.popen(self.scope_argv(session), stdout_path=session.console_log)
        session.handle["process"] = handle
        session.handle["pid"] = handle.pid
        if not self.runner.dry_run:
            # A VMM that dies at once (bad config, missing kernel) shows up here, not after a poll.
            rc = handle.wait(0.2)
            if rc is not None:
                raise BackendError("firecracker exited immediately (rc=%s); see %s" % (rc, session.console_log))

    def alive(self, session: Session) -> bool:
        handle = session.handle.get("process")
        if handle is None:
            return False
        return handle.poll() is None

    def exit_info(self, session: Session) -> Optional[str]:
        handle = session.handle.get("process")
        if handle is None or self.runner.dry_run:
            return None
        rc = handle.poll()
        info = "firecracker exited rc=%s" % rc
        try:
            with open(session.console_log or "", "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 600))
                tail = f.read().decode(errors="replace").strip().splitlines()[-3:]
            if tail:
                info += " | " + " / ".join(tail)
        except OSError:
            pass
        return info[:500]

    def destroy(self, session: Session) -> None:
        slot = session.slot
        unit = self.unit(slot) + ".scope"
        # SIGKILL the scope: nothing to flush in a read-only guest (section 4).
        self.runner.run(["systemctl", "kill", "--signal=SIGKILL", unit], check=False, timeout=20)
        handle = session.handle.get("process")
        if handle is not None:
            handle.wait(5.0)
        self.runner.run(["ip", "link", "del", self.tap(slot)], check=False, timeout=20)
        self._wait_scope_gone(unit, 5.0)
        self.runner.run(["systemctl", "reset-failed", unit], check=False, timeout=20)
        self.runner.remove_tree(self.run_dir(session))
        self._cgroup_paths.pop(session.id, None)

    def _wait_scope_gone(self, unit: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            r = self.runner.run(["systemctl", "is-active", unit], check=False, timeout=10)
            if self.runner.dry_run or r.stdout.strip() in ("inactive", "failed", "") or not r.ok:
                return
            if time.monotonic() > deadline:
                return
            time.sleep(0.1)

    # --- sampling ----------------------------------------------------------
    def _cgroup_dir(self, session: Session) -> Optional[str]:
        cached = self._cgroup_paths.get(session.id)
        if cached:
            return cached
        unit = self.unit(session.slot) + ".scope"
        for candidate in (os.path.join(CGROUP_ROOT, "system.slice", unit),):
            if os.path.isdir(candidate):
                self._cgroup_paths[session.id] = candidate
                return candidate
        r = self.runner.run(["systemctl", "show", "-p", "ControlGroup", "--value", unit], check=False, timeout=10)
        cg = r.stdout.strip()
        if cg:
            path = CGROUP_ROOT + cg
            if os.path.isdir(path):
                self._cgroup_paths[session.id] = path
                return path
        return None

    @staticmethod
    def _read_int(path: str) -> Optional[int]:
        try:
            with open(path) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def sample(self, session: Session) -> Dict[str, Optional[int]]:
        out: Dict[str, Optional[int]] = {"rss_bytes": None, "cgroup_memory_current": None,
                                         "cgroup_memory_peak": None, "cpu_usage_usec": None}
        if self.runner.dry_run:
            return out
        cg = self._cgroup_dir(session)
        if not cg:
            return out
        out["cgroup_memory_current"] = self._read_int(os.path.join(cg, "memory.current"))
        out["cgroup_memory_peak"] = self._read_int(os.path.join(cg, "memory.peak"))
        try:
            with open(os.path.join(cg, "cpu.stat")) as f:
                for line in f:
                    if line.startswith("usage_usec "):
                        out["cpu_usage_usec"] = int(line.split()[1])
        except (OSError, ValueError):
            pass
        rss = 0
        found = False
        try:
            with open(os.path.join(cg, "cgroup.procs")) as f:
                pids = [p.strip() for p in f if p.strip()]
            for pid in pids:
                with open("/proc/%s/status" % pid) as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            rss += int(line.split()[1]) * 1024
                            found = True
        except (OSError, ValueError):
            pass
        out["rss_bytes"] = rss if found else None
        return out

    # --- host checks -------------------------------------------------------
    def verify_clean(self) -> List[str]:
        leftovers: List[str] = []
        if self.runner.dry_run:
            return leftovers
        r = self.runner.run(["pgrep", "-x", "firecracker"], check=False, timeout=10)
        if r.ok:
            leftovers += ["process firecracker pid %s" % p for p in r.stdout.split()]
        r = self.runner.run(["ip", "-o", "link", "show"], check=False, timeout=10)
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if len(parts) > 1:
                name = parts[1].strip().split("@")[0]
                if name.startswith("fc-"):
                    leftovers.append("tap %s" % name)
        r = self.runner.run(["systemctl", "list-units", "--all", "--plain", "--no-legend", "fc-vm*.scope"],
                            check=False, timeout=10)
        for line in r.stdout.splitlines():
            if line.strip():
                leftovers.append("scope %s" % line.split()[0])
        try:
            for entry in sorted(os.listdir(self.run_root)):
                leftovers.append("run dir %s" % os.path.join(self.run_root, entry))
        except FileNotFoundError:
            pass
        return leftovers

    def check_host(self) -> List[str]:
        problems = []
        if platform.system() != "Linux":
            problems.append("the firecracker backend needs a Linux host with /dev/kvm (use --dry-run elsewhere)")
            return problems
        if not os.path.exists("/dev/kvm"):
            problems.append("/dev/kvm is missing")
        for label, path in (("firecracker binary", self.firecracker), ("kernel", self.kernel), ("rootfs", self.rootfs)):
            if not os.path.exists(path):
                problems.append("%s not found at %s" % (label, path))
        for tool in ("ip", "systemd-run", "systemctl"):
            if shutil.which(tool) is None:
                problems.append("%s not on PATH" % tool)
        if not os.path.isdir("/sys/class/net/%s" % self.bridge):
            problems.append("bridge %s does not exist" % self.bridge)
        if os.geteuid() != 0:
            problems.append("not running as root (taps and scopes need it)")
        return problems
