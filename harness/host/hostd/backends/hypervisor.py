"""What the two hypervisor backends share: one microVM per slot on the fcbr0 bridge (Linux, /dev/kvm).

Sections 2-4 of the design, per slot s:
  a tap attached to fcbr0; guest IP 10.200.0.(10+s); MAC 06:00:0A:C8:00:<10+s hex>;
  kernel `ip=10.200.0.<10+s>::10.200.0.1:255.255.255.0:vm<s>:eth0:off:10.42.0.2`;
  `fleetkit.fault=<name>` on the command line when a fault is requested, and
  `fleetkit.chromium_extra_flags=<word>` when the spec adds Chromium flags (model.encode_chromium_flags);
  the same guest kernel and read-only rootfs, the same vCPUs and memory, whichever hypervisor runs it;
  `systemd-run --scope` with MemoryMax and CPUQuota around the hypervisor process, which gives a
  cgroup v2 leaf for memory.current, memory.peak, cpu.stat and the pressure files;
  SIGKILL teardown: kill the scope, delete the tap, remove the run directory.

A subclass says how its hypervisor starts (its command line and any config file), which
options of the spec's hypervisor section it can carry out, what its vCPU threads are called
and how its process and per-slot names look.

CPU split: the vCPU figure is the utime+stime of the hypervisor's vCPU threads; the
hypervisor's own figure is the scope's cgroup usage minus that. Measured this way, a thread
that has already exited (Cloud Hypervisor's kernel loader) still counts as the hypervisor's.
For Firecracker, whose threads all live as long as the microVM and whose scope holds only the
Firecracker process, this equals the sum of its non-vCPU threads, to within a clock tick.

The serial console is the hypervisor's stdout, redirected to the per-microVM console log; the
hypervisor's own log goes next to it. The proc and cgroup roots are constructor arguments so
the readers can be tested against a fake tree.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

from ..model import MicroVM, encode_chromium_flags
from ..procfs import (PROC_ROOT, cgroup_pids, parse_flat_keyed, parse_pressure, read_int, read_text,
                      status_rss_bytes, thread_cpu_split)
from ..runner import CommandError, Runner
from .base import Backend, BackendError, empty_sample, public_path

BRIDGE = "fcbr0"
GATEWAY = "10.200.0.1"
NETMASK = "255.255.255.0"
RESOLVER = "10.42.0.2"          # the VPC resolver
GUEST_PORT = 8080
RUN_ROOT = "/run/fleetkit"
MEM_OVERHEAD_MIB = 256          # hypervisor process + page tables on top of guest memory
CGROUP_ROOT = "/sys/fs/cgroup"
CPU_QUOTA_PCT_PER_VCPU = 100    # CPUQuota = vcpus x 100%: one host CPU's worth per vCPU
SMT = False                     # one thread per core in the guest

DEFAULT_KERNEL = "/var/lib/fleetkit/vmlinux"
DEFAULT_ROOTFS = "/var/lib/fleetkit/guest.ext4"

# Every hypervisor's leftovers, so either backend's verify-clean sees the other's too:
# (process name as pgrep -x sees it, i.e. comm cut to 15 characters; tap prefix; scope prefix).
LEFTOVER_NAMES: Tuple[Tuple[str, str, str], ...] = (
    ("firecracker", "fc-", "fc-vm"),
    ("cloud-hyperviso", "ch-", "ch-vm"),
)


class HypervisorBackend(Backend):
    name = "hypervisor"
    max_slots = 200                 # 10+s <= 209 keeps every guest inside 10.200.0.0/24
    fixture_base_url = "http://%s:8081" % GATEWAY

    # Set by each subclass.
    default_binary = ""
    tap_prefix = ""
    unit_prefix = ""
    vcpu_thread_prefix = ""         # comm of a vCPU thread starts with this
    log_name = ""                   # the hypervisor's own log, next to console.log
    # {option: [values it can carry out]}; the first value is used when a microVM names none.
    hypervisor_options: Dict[str, List[Any]] = {}

    def __init__(self, runner: Runner, log_dir: str, binary: Optional[str] = None,
                 kernel: str = DEFAULT_KERNEL, rootfs: str = DEFAULT_ROOTFS, bridge: str = BRIDGE,
                 run_root: str = RUN_ROOT, resolver: str = RESOLVER, gateway: str = GATEWAY,
                 mem_overhead_mib: int = MEM_OVERHEAD_MIB, extra_boot_args: str = "",
                 cgroup_root: str = CGROUP_ROOT, proc_root: str = PROC_ROOT, arch: Optional[str] = None):
        super().__init__(runner, log_dir)
        self.binary = binary or self.default_binary
        self.kernel = kernel
        self.rootfs = rootfs
        self.bridge = bridge
        self.run_root = run_root
        self.resolver = resolver
        self.gateway = gateway
        self.mem_overhead_mib = mem_overhead_mib
        self.extra_boot_args = extra_boot_args
        self.cgroup_root = cgroup_root
        self.proc_root = proc_root
        self.arch = arch or platform.machine()
        self.fixture_base_url = "http://%s:8081" % gateway
        self._cgroup_paths: Dict[str, str] = {}

    # --- the spec's hypervisor section --------------------------------------
    def options(self, microvm: MicroVM) -> Dict[str, Any]:
        """This microVM's hypervisor options: what it names, the backend's first offer otherwise.
        Raises BackendError for a value this backend can't carry out (the manager refuses those
        before create(); this keeps a direct caller from running something else than it asked)."""
        asked = dict(microvm.hypervisor or {})
        name = asked.pop("name", self.name)
        if name != self.name:
            raise BackendError("this backend runs %s, not %s" % (self.name, name))
        out = {}
        for option, offered in self.hypervisor_options.items():
            value = asked.pop(option, offered[0])
            if not any(value == v and type(value) is type(v) for v in offered):
                raise BackendError("%s can't carry out %s = %r" % (self.name, option, value))
            out[option] = value
        if asked:
            raise BackendError("%s has no option %s" % (self.name, ", ".join(sorted(asked))))
        return out

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

    @classmethod
    def tap(cls, slot: int) -> str:
        return "%s%d" % (cls.tap_prefix, slot)

    @classmethod
    def unit(cls, slot: int) -> str:
        return "%s%d" % (cls.unit_prefix, slot)

    def address(self, slot: int) -> str:
        return "%s:%d" % (self.guest_ip(slot), GUEST_PORT)

    def ip_arg(self, slot: int) -> str:
        return "ip=%s::%s:%s:vm%d:eth0:off:%s" % (self.guest_ip(slot), self.gateway, NETMASK, slot, self.resolver)

    def boot_args(self, microvm: MicroVM) -> str:
        args = self.base_boot_args(microvm) + [self.ip_arg(microvm.slot)]
        if microvm.fault:
            args.append("fleetkit.fault=%s" % microvm.fault)
        if microvm.chromium_extra_flags:
            args.append("fleetkit.chromium_extra_flags=%s" % encode_chromium_flags(microvm.chromium_extra_flags))
        if self.extra_boot_args:
            args.append(self.extra_boot_args)
        return " ".join(args)

    def base_boot_args(self, microvm: MicroVM) -> List[str]:
        """The kernel command line before ip= (subclass)."""
        raise NotImplementedError

    def run_dir(self, microvm: MicroVM) -> str:
        return os.path.join(self.run_root, microvm.id)

    def console_log_path(self, microvm: MicroVM) -> str:
        return os.path.join(self.log_dir, "microvms", microvm.id, "console.log")

    def hypervisor_log_path(self, microvm: MicroVM) -> str:
        return os.path.join(self.log_dir, "microvms", microvm.id, self.log_name)

    def sidecar(self, microvm: MicroVM) -> Dict[str, Any]:
        """What a human needs in the run directory: where the console went and which slot this is."""
        return {"microvm_id": microvm.id, "slot": microvm.slot, "hypervisor": self.name,
                "options": self.options(microvm), "guest_ip": self.guest_ip(microvm.slot),
                "guest_mac": self.guest_mac(microvm.slot), "tap": self.tap(microvm.slot),
                "unit": self.unit(microvm.slot) + ".scope", "console_log": self.console_log_path(microvm),
                "hypervisor_log": self.hypervisor_log_path(microvm), "fault": microvm.fault,
                "chromium_extra_flags": list(microvm.chromium_extra_flags)}

    def hypervisor_argv(self, microvm: MicroVM) -> List[str]:
        """The hypervisor's own command line (subclass)."""
        raise NotImplementedError

    def write_files(self, microvm: MicroVM) -> None:
        """Config files the hypervisor reads, written into the run directory (subclass; may be none)."""

    def scope_argv(self, microvm: MicroVM) -> List[str]:
        return ["systemd-run", "--scope", "--quiet",
                "--unit", self.unit(microvm.slot),
                "--description", "fleetkit microVM %s" % microvm.id,
                "-p", "MemoryMax=%dM" % (microvm.mem_mib + self.mem_overhead_mib),
                "-p", "MemorySwapMax=0",
                "-p", "CPUQuota=%d%%" % (microvm.vcpus * CPU_QUOTA_PCT_PER_VCPU)] + self.hypervisor_argv(microvm)

    # --- lifecycle ---------------------------------------------------------
    def create(self, microvm: MicroVM) -> None:
        self.options(microvm)       # refuse what this backend can't carry out before touching the host
        slot = microvm.slot
        d = self.run_dir(microvm)
        tap = self.tap(slot)
        microvm.handle.update({"tap": tap, "unit": self.unit(slot) + ".scope", "run_dir": d})
        microvm.console_log = self.console_log_path(microvm)
        self.runner.mkdir(d)
        self.runner.mkdir(os.path.dirname(microvm.console_log))
        self.write_files(microvm)
        self.runner.write_file(os.path.join(d, "microvm.json"), json.dumps(self.sidecar(microvm), indent=2) + "\n")
        try:
            self.runner.run(["ip", "tuntap", "add", "dev", tap, "mode", "tap"], timeout=20)
            self.runner.run(["ip", "link", "set", tap, "master", self.bridge], timeout=20)
            self.runner.run(["ip", "link", "set", tap, "up"], timeout=20)
        except CommandError as e:
            raise BackendError("tap setup failed: %s" % e) from e
        handle = self.runner.popen(self.scope_argv(microvm), stdout_path=microvm.console_log)
        microvm.handle["process"] = handle
        microvm.handle["pid"] = handle.pid
        if not self.runner.dry_run:
            # A hypervisor that dies at once (bad config, missing kernel) shows up here, not after a poll.
            rc = handle.wait(0.2)
            if rc is not None:
                raise BackendError("%s exited immediately (rc=%s); see %s" % (self.name, rc, microvm.console_log))

    def alive(self, microvm: MicroVM) -> bool:
        handle = microvm.handle.get("process")
        if handle is None:
            return False
        return handle.poll() is None

    def exit_info(self, microvm: MicroVM) -> Optional[str]:
        handle = microvm.handle.get("process")
        if handle is None or self.runner.dry_run:
            return None
        return (self.exit_reason(microvm, handle.poll()) + console_tail(microvm.console_log))[:500]

    def exit_reason(self, microvm: MicroVM, rc: Optional[int]) -> str:
        return "%s exited rc=%s" % (self.name, rc)

    def destroy(self, microvm: MicroVM) -> None:
        slot = microvm.slot
        unit = self.unit(slot) + ".scope"
        # SIGKILL the scope: nothing to flush in a read-only guest (section 4).
        self.runner.run(["systemctl", "kill", "--signal=SIGKILL", unit], check=False, timeout=20)
        handle = microvm.handle.get("process")
        if handle is not None:
            handle.wait(5.0)
        self.runner.run(["ip", "link", "del", self.tap(slot)], check=False, timeout=20)
        self._wait_scope_gone(unit, 5.0)
        self.runner.run(["systemctl", "reset-failed", unit], check=False, timeout=20)
        self.runner.remove_tree(self.run_dir(microvm))
        self._cgroup_paths.pop(microvm.id, None)

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
    def _cgroup_dir(self, microvm: MicroVM) -> Optional[str]:
        cached = self._cgroup_paths.get(microvm.id)
        if cached:
            return cached
        unit = self.unit(microvm.slot) + ".scope"
        candidate = os.path.join(self.cgroup_root, "system.slice", unit)
        if os.path.isdir(candidate):
            self._cgroup_paths[microvm.id] = candidate
            return candidate
        r = self.runner.run(["systemctl", "show", "-p", "ControlGroup", "--value", unit], check=False, timeout=10)
        cg = r.stdout.strip()
        if cg:
            path = self.cgroup_root + cg
            if os.path.isdir(path):
                self._cgroup_paths[microvm.id] = path
                return path
        return None

    def sample(self, microvm: MicroVM) -> Dict[str, Optional[int]]:
        """The scope's cgroup figures, VmRSS summed over its processes, and its CPU split into the
        vCPU threads and the hypervisor's own (cgroup usage minus vCPU; cumulative, microseconds)."""
        out = empty_sample()
        if self.runner.dry_run:
            return out
        cg = self._cgroup_dir(microvm)
        if not cg:
            return out
        out["cgroup_memory_current"] = read_int(os.path.join(cg, "memory.current"))
        out["cgroup_memory_peak"] = read_int(os.path.join(cg, "memory.peak"))
        cpu_stat = parse_flat_keyed(read_text(os.path.join(cg, "cpu.stat")))
        out["cpu_usage_usec"] = cpu_stat.get("usage_usec")
        out["cpu_throttled_usec"] = cpu_stat.get("throttled_usec")
        out["cpu_nr_throttled"] = cpu_stat.get("nr_throttled")
        cpu_psi = parse_pressure(read_text(os.path.join(cg, "cpu.pressure")))
        mem_psi = parse_pressure(read_text(os.path.join(cg, "memory.pressure")))
        out["cpu_pressure_some_total_us"] = _int_or_none(cpu_psi["some_total"])
        out["cpu_pressure_full_total_us"] = _int_or_none(cpu_psi["full_total"])
        out["memory_pressure_some_total_us"] = _int_or_none(mem_psi["some_total"])
        pids = cgroup_pids(cg)
        rss = [r for r in (status_rss_bytes(self.proc_root, pid) for pid in pids) if r is not None]
        out["rss_bytes"] = sum(rss) if rss else None
        vcpu = thread_cpu_split(pids, self.proc_root, vcpu_prefix=self.vcpu_thread_prefix)["vcpu_usec"]
        out["cpu_vcpu_usec"] = vcpu
        usage = out["cpu_usage_usec"]
        # Thread times are read after cpu.stat, and are whole clock ticks: never below zero.
        out["cpu_hypervisor_usec"] = max(0, usage - vcpu) if usage is not None and vcpu is not None else None
        return out

    # --- static facts for GET /host/info -------------------------------------
    def version(self) -> Optional[str]:
        """First line of `<hypervisor> --version` (None in dry-run or when it does not run)."""
        if self.runner.dry_run:
            return None
        r = self.runner.run([self.binary, "--version"], check=False, timeout=10)
        lines = [line.strip() for line in r.stdout.splitlines() if line.strip()] if r.ok else []
        return lines[0] if lines else None

    def info(self) -> Dict[str, Any]:
        example = MicroVM(id="s000-example", slot=0, backend=self.name, address=self.address(0), vcpus=2,
                          mem_mib=2048, fault=None, ready_timeout_s=0, max_lifetime_s=0, idle_timeout_s=0,
                          fixture_base_url=self.fixture_base_url)
        return {
            "version": self.version(),
            "kernel_path": public_path(self.kernel),
            "kernel_bytes": _size(self.kernel),
            "rootfs_path": public_path(self.rootfs),
            "rootfs_bytes": _size(self.rootfs),
            "boot_args_example": self.boot_args(example),
            "mem_overhead_mib": self.mem_overhead_mib,
            "cpu_quota_pct_per_vcpu": CPU_QUOTA_PCT_PER_VCPU,
            "smt": SMT,
        }

    # --- host checks -------------------------------------------------------
    def verify_clean(self) -> List[str]:
        """Every hypervisor's leftovers: processes, taps, scopes, and run directories."""
        leftovers: List[str] = []
        if self.runner.dry_run:
            return leftovers
        for process, _, _ in LEFTOVER_NAMES:
            r = self.runner.run(["pgrep", "-x", process], check=False, timeout=10)
            if r.ok:
                leftovers += ["process %s pid %s" % (process, p) for p in r.stdout.split()]
        tap_prefixes = tuple(tap for _, tap, _ in LEFTOVER_NAMES)
        r = self.runner.run(["ip", "-o", "link", "show"], check=False, timeout=10)
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if len(parts) > 1:
                name = parts[1].strip().split("@")[0]
                if name.startswith(tap_prefixes):
                    leftovers.append("tap %s" % name)
        for _, _, unit in LEFTOVER_NAMES:
            r = self.runner.run(["systemctl", "list-units", "--all", "--plain", "--no-legend", "%s*.scope" % unit],
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
            problems.append("the %s backend needs a Linux host with /dev/kvm (use --dry-run elsewhere)" % self.name)
            return problems
        if not os.path.exists("/dev/kvm"):
            problems.append("/dev/kvm is missing")
        for label, path in (("%s binary" % self.name, self.binary), ("kernel", self.kernel), ("rootfs", self.rootfs)):
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


def console_tail(path: Optional[str], lines: int = 3) -> str:
    """' | ' and the console log's last lines joined by ' / ', or '' when there are none."""
    try:
        with open(path or "", "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 600))
            tail = f.read().decode(errors="replace").strip().splitlines()[-lines:]
    except OSError:
        return ""
    return " | " + " / ".join(tail) if tail else ""


def _int_or_none(v: Optional[float]) -> Optional[int]:
    return int(v) if v is not None else None


def _size(path: str) -> Optional[int]:
    try:
        return os.path.getsize(path)
    except OSError:
        return None
