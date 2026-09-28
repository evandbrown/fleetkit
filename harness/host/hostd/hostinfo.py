"""GET /host/info: static facts about this host, computed once and cached by the server.

What a reader of a run needs to know about the machine the numbers came from: kernel
release, CPU model and topology, memory, whether the host is itself virtualized and has
KVM, its transparent huge page mode (which decides what a spec's memory pages get), its EC2
identity (IMDSv2 with a 1 s budget, null off EC2, never raises), the metrics period, and for
the firecracker backend the Firecracker version and the kernel and rootfs the microVMs boot.
Never the hostname: it can carry the operator's name into a shared bundle.

Every source is injectable (proc root, /sys cpu root, the THP directory, /dev/kvm path, IMDS
base URL, the lscpu call) so the collector can be tested off Linux.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
import urllib.request
from typing import Any, Callable, Dict, Optional

from . import __version__
from .metrics import read_meminfo
from .model import hypervisor_options, microvm_options
from .procfs import PROC_ROOT, read_text

IMDS_URL = "http://169.254.169.254"
IMDS_TIMEOUT_S = 1.0
SYS_CPU_ROOT = "/sys/devices/system/cpu"
SYS_THP = "/sys/kernel/mm/transparent_hugepage"
DEV_KVM = "/dev/kvm"

_TOPOLOGY_KEYS = ("threads_per_core", "cores_per_socket", "sockets")


def _int(v: Any) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def cpuinfo_facts(proc_root: str = PROC_ROOT) -> Dict[str, Any]:
    """{cpu_model, virtualized} from /proc/cpuinfo. virtualized is the x86 `hypervisor` flag:
    True or False where a `flags` line exists, None where none does (no /proc, or arm64)."""
    model: Optional[str] = None
    flags_seen = hypervisor = False
    for line in (read_text(os.path.join(proc_root, "cpuinfo")) or "").splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        if key == "model name" and model is None:
            model = value.strip() or None
        elif key == "flags":
            flags_seen = True
            hypervisor = hypervisor or "hypervisor" in value.split()
    return {"cpu_model": model, "virtualized": hypervisor if flags_seen else None}


def selected(text: Optional[str]) -> Optional[str]:
    """The bracketed choice of a sysfs mode file: "always [madvise] never" -> "madvise"; None without one."""
    for word in (text or "").split():
        if len(word) > 2 and word.startswith("[") and word.endswith("]"):
            return word[1:-1]
    return None


def thp_facts(sys_thp: str = SYS_THP) -> Optional[Dict[str, Optional[str]]]:
    """{enabled, defrag}: the host's transparent huge page mode (always, madvise or never) and when a fault on
    a huge-page region may stall to compact memory. A spec's memory pages thp asks for huge pages the way
    madvise mode grants them, and 4k asks for none, which always mode gives anyway. None where the host has
    no THP (macOS, a kernel without it)."""
    enabled = selected(read_text(os.path.join(sys_thp, "enabled")))
    defrag = selected(read_text(os.path.join(sys_thp, "defrag")))
    if enabled is None and defrag is None:
        return None
    return {"enabled": enabled, "defrag": defrag}


def run_lscpu() -> Optional[str]:
    """`lscpu -J` output, or None where lscpu is missing or fails."""
    if shutil.which("lscpu") is None:
        return None
    try:
        proc = subprocess.run(["lscpu", "-J"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _lscpu_fields(entries: Any, out: Dict[str, Any]) -> None:
    # util-linux >= 2.37 nests fields under "children"; older versions give a flat list.
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, dict):
            out.setdefault(str(e.get("field", "")).strip().rstrip(":"), e.get("data"))
            _lscpu_fields(e.get("children"), out)


def topology_from_lscpu(text: Optional[str]) -> Optional[Dict[str, Optional[int]]]:
    try:
        doc = json.loads(text or "")
    except ValueError:
        return None
    fields: Dict[str, Any] = {}
    _lscpu_fields(doc.get("lscpu") if isinstance(doc, dict) else None, fields)
    topo = {"threads_per_core": _int(fields.get("Thread(s) per core")),
            "cores_per_socket": _int(fields.get("Core(s) per socket")),
            "sockets": _int(fields.get("Socket(s)"))}
    return topo if any(v is not None for v in topo.values()) else None


def topology_from_sys(sys_cpu_root: str = SYS_CPU_ROOT) -> Optional[Dict[str, Optional[int]]]:
    """Topology from /sys/devices/system/cpu/cpu<N>/topology/{core_id,physical_package_id}."""
    try:
        names = [n for n in os.listdir(sys_cpu_root) if n.startswith("cpu") and n[3:].isdigit()]
    except OSError:
        return None
    logical = 0
    cores: set = set()
    packages: set = set()
    for n in names:
        topo = os.path.join(sys_cpu_root, n, "topology")
        core, pkg = read_text(os.path.join(topo, "core_id")), read_text(os.path.join(topo, "physical_package_id"))
        if core is None or pkg is None:
            continue
        logical += 1
        cores.add((pkg.strip(), core.strip()))
        packages.add(pkg.strip())
    if not logical:
        return None
    return {"threads_per_core": logical // len(cores), "cores_per_socket": len(cores) // len(packages),
            "sockets": len(packages)}


def ec2_identity(base_url: str = IMDS_URL, timeout_s: float = IMDS_TIMEOUT_S) -> Optional[Dict[str, Optional[str]]]:
    """{instance_id, instance_type, ami_id, availability_zone} over IMDSv2, or None off EC2.

    A token PUT and four reads share one `timeout_s` budget; any failure gives None. Proxies
    from the environment are ignored (the metadata service is link-local). Never raises.
    """
    deadline = time.monotonic() + timeout_s
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def fetch(req: urllib.request.Request) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("IMDS budget spent")
        with opener.open(req, timeout=remaining) as r:
            return r.read().decode().strip()

    try:
        token = fetch(urllib.request.Request(base_url + "/latest/api/token", method="PUT",
                                             headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}))
        out: Dict[str, Optional[str]] = {}
        for key, path in (("instance_id", "instance-id"), ("instance_type", "instance-type"),
                          ("ami_id", "ami-id"), ("availability_zone", "placement/availability-zone")):
            out[key] = fetch(urllib.request.Request(base_url + "/latest/meta-data/" + path,
                                                    headers={"X-aws-ec2-metadata-token": token})) or None
        return out
    except Exception:
        return None


def collect_host_info(backend: Any, host_id: str, metrics_period_s: Optional[float],
                      proc_root: str = PROC_ROOT, sys_cpu_root: str = SYS_CPU_ROOT, dev_kvm: str = DEV_KVM,
                      imds_url: str = IMDS_URL, imds_timeout_s: float = IMDS_TIMEOUT_S,
                      lscpu: Optional[Callable[[], Optional[str]]] = run_lscpu,
                      sys_thp: str = SYS_THP) -> Dict[str, Any]:
    """The GET /host/info document. `metrics_period_s` is None when the sampler is off."""
    cpu = cpuinfo_facts(proc_root)
    topo = (topology_from_lscpu(lscpu()) if lscpu else None) or topology_from_sys(sys_cpu_root) \
        or {k: None for k in _TOPOLOGY_KEYS}
    try:
        facts = backend.info()
    except Exception:
        facts = None
    return {
        "host_id": host_id,
        "backend": backend.name,
        "hostd_version": __version__,
        "kernel_release": platform.release() or None,
        "cpu_model": cpu["cpu_model"],
        "cpu_count": os.cpu_count(),
        "threads_per_core": topo["threads_per_core"],
        "cores_per_socket": topo["cores_per_socket"],
        "sockets": topo["sockets"],
        "mem_total": read_meminfo(proc_root)["mem_total"],
        "virtualized": cpu["virtualized"],
        "kvm": os.path.exists(dev_kvm),
        "transparent_hugepage": thp_facts(sys_thp),
        "ec2": ec2_identity(imds_url, imds_timeout_s),
        "metrics_period_s": metrics_period_s,
        # The hypervisor's own facts (version, kernel, rootfs), whichever hypervisor runs. "firecracker" is
        # the key runs recorded before Cloud Hypervisor, kept for their readers; it is set on Firecracker only.
        "hypervisor": facts,
        "firecracker": facts if backend.name == "firecracker" else None,
        # What a spec may ask of this daemon: the most microVMs at once, the hypervisor options its
        # backend can carry out, whether it passes a spec's extra Chromium flags to the guest (every
        # backend does), and the guest consoles and memory pages it can carry out. The driver refuses a
        # spec that asks for more.
        "max_slots": getattr(backend, "max_slots", None),
        "hypervisor_options": hypervisor_options(backend),
        "chromium_extra_flags": True,
        "microvm_options": microvm_options(backend),
    }
