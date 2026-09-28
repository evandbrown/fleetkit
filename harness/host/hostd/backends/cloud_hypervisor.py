"""Cloud Hypervisor backend: one microVM per slot on the fcbr0 bridge (Linux hosts).

The shared part (taps, the scope, sampling, teardown, host checks) is in hypervisor.py. Here:
  tap ch-<s>, scope ch-vm<s>.scope;
  the whole microVM on the command line, no API calls: the same guest kernel and read-only
  rootfs as Firecracker, the same vCPUs and memory, one NIC on the slot's tap, an entropy
  device, the serial console on stdout (into console.log) and the virtio console off.

Cloud Hypervisor only has the PCI transport and always adds an entropy device, so the spec's
hypervisor options have one value each: virtio_transport pci, virtio_rng true.

Settings pinned so the two hypervisors differ only where they must:
  --cpus     one thread per core (Firecracker smt=false), no nested virtualization in the guest,
             no core scheduling cookies (Cloud Hypervisor's defaults are on and vm);
  --memory   anonymous 4 KiB pages without a MADV_HUGEPAGE hint (thp=off), as Firecracker maps
             guest memory; no hugepages, no prefault, no KSM. The spec's memory pages thp turns the
             hint on (thp=on), as Firecracker's huge_pages Transparent does;
  --disk     read-only raw image, one queue of 256, synchronous I/O through the host page cache
             (Firecracker's Sync engine), not io_uring or AIO; no discard (sparse=off);
  --net      one queue pair of 256, checksum and segmentation offloads on;
  --serial   the 8250 serial port on stdout, console=ttyS0, the virtio console off. On aarch64
             Cloud Hypervisor's serial port is a PL011, which the Firecracker CI kernel has no
             driver for, so there (development hosts only) the console is the virtio console,
             hvc0, on stdout instead; without a console the guest's init has no stdout and dies.

The kernel command line is Firecracker's without `pci=off`, plus `root=/dev/vda ro`, which
Firecracker adds itself and Cloud Hypervisor does not.

A guest reset (reboot=k, or panic=1 once init dies) makes Cloud Hypervisor reboot the guest
where Firecracker exits. So the event monitor writes to <run>/events.json and alive() treats a
"rebooting" or "shutdown" event as the microVM having exited: a guest that crashes during boot
is a startup error on both hypervisors, not a ready timeout on one of them.

vCPU threads are named `vcpu<n>`; everything else in the scope is the hypervisor's own: its main
and API threads, the event monitor, the signal handler and one thread per device queue
(`rootfs_q0`, `eth0_qp0`, ...), plus a kernel loader thread that exits after boot.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from ..model import MicroVM
from .hypervisor import HypervisorBackend

DEFAULT_CLOUD_HYPERVISOR = "/usr/local/bin/cloud-hypervisor"
QUEUE_SIZE = 256
# Events after which the guest is gone as far as a run is concerned.
EXIT_EVENTS = ("rebooting", "rebooted", "shutdown")


class CloudHypervisorBackend(HypervisorBackend):
    name = "cloud-hypervisor"
    default_binary = DEFAULT_CLOUD_HYPERVISOR
    tap_prefix = "ch-"
    unit_prefix = "ch-vm"
    vcpu_thread_prefix = "vcpu"
    log_name = "cloud-hypervisor.log"
    hypervisor_options = {"virtio_transport": ["pci"], "virtio_rng": [True]}

    def console_args(self) -> List[str]:
        """(kernel console, --serial, --console): the 8250 serial port on x86_64, hvc0 on aarch64."""
        if self.arch in ("aarch64", "arm64"):
            return ["hvc0", "off", "tty"]
        return ["ttyS0", "tty", "off"]

    def base_boot_args(self, microvm: MicroVM) -> List[str]:
        return ["console=%s" % self.console_args()[0], "reboot=k", "panic=1", "root=/dev/vda", "ro"]

    def events_path(self, microvm: MicroVM) -> str:
        return os.path.join(self.run_dir(microvm), "events.json")

    def hypervisor_argv(self, microvm: MicroVM) -> List[str]:
        self.options(microvm)
        d = self.run_dir(microvm)
        n = microvm.vcpus
        tap, mac = self.tap(microvm.slot), self.guest_mac(microvm.slot)
        _, serial, console = self.console_args()
        return [
            self.binary,
            "--api-socket", "path=%s" % os.path.join(d, "ch.sock"),
            "--kernel", self.kernel,
            "--cmdline", self.boot_args(microvm),
            "--cpus", "boot=%d,max=%d,topology=1:%d:1:1,nested=off,core_scheduling=off,kvm_hyperv=off" % (n, n, n),
            "--memory", "size=%dM,thp=%s,hugepages=off,shared=off,prefault=off,mergeable=off" % (
                microvm.mem_mib, "on" if microvm.memory_pages == "thp" else "off"),
            "--disk", "path=%s,readonly=on,direct=off,image_type=raw,sparse=off,num_queues=1,queue_size=%d,"
                      "id=rootfs,_disable_io_uring=on,_disable_aio=on" % (self.rootfs, QUEUE_SIZE),
            "--net", "tap=%s,mac=%s,num_queues=2,queue_size=%d,offload_tso=on,offload_ufo=on,offload_csum=on,"
                     "id=eth0" % (tap, mac, QUEUE_SIZE),
            "--rng", "src=/dev/urandom",
            "--serial", serial,
            "--console", console,
            "--seccomp", "true",
            "--event-monitor", "path=%s" % self.events_path(microvm),
            "--log-file", self.hypervisor_log_path(microvm),
        ]

    # --- a guest reset is an exit ---------------------------------------------
    def exit_event(self, microvm: MicroVM) -> Optional[str]:
        """The first event after which the guest is gone ("vm rebooting", ...), or None."""
        if self.runner.dry_run:
            return None
        for e in read_events(read_file(self.events_path(microvm))):
            if e.get("event") in EXIT_EVENTS:
                return "%s %s" % (e.get("source", "?"), e.get("event"))
        return None

    def alive(self, microvm: MicroVM) -> bool:
        if not super().alive(microvm):
            return False
        event = self.exit_event(microvm)
        if event:
            microvm.handle["exit_event"] = event
            return False
        return True

    def exit_reason(self, microvm: MicroVM, rc: Optional[int]) -> str:
        event = microvm.handle.get("exit_event") or self.exit_event(microvm)
        if event and rc is None:
            return "cloud-hypervisor event '%s': the guest reset or shut down (it reboots rather than exits)" % event
        return super().exit_reason(microvm, rc)


def read_file(path: str) -> str:
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def read_events(text: str) -> List[Dict[str, Any]]:
    """The event monitor's file: JSON objects one after another (pretty-printed, blank lines
    between). A partly written last object is left for the next read."""
    events: List[Dict[str, Any]] = []
    decoder = json.JSONDecoder()
    i, n = 0, len(text)
    while True:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            return events
        try:
            obj, i = decoder.raw_decode(text, i)
        except ValueError:
            return events
        if isinstance(obj, dict):
            events.append(obj)
