"""Firecracker backend: one microVM per slot on the fcbr0 bridge (Linux hosts).

The shared part (taps, the scope, sampling, teardown, host checks) is in hypervisor.py. Here:
  tap fc-<s>, scope fc-vm<s>.scope;
  VM config JSON (vm.json) with the read-only shared rootfs, the vCPUs, memory and NIC;
  `firecracker --api-sock <run>/fc.sock --config-file <run>/vm.json`.

The spec's hypervisor options:
  virtio_transport  mmio (the default, as cap-baseline-1 ran: `pci=off` on the kernel command
                    line) or pci (`--enable-pci`: every virtio device on the PCI transport);
  virtio_rng        false (the default: no entropy device) or true (`"entropy": {}` in vm.json).

vCPU threads are named `fc_vcpu <n>`; the main event loop (with the device emulation) and
the API thread are the hypervisor's own. The serial console (console=ttyS0) is Firecracker's
stdout; its own log goes to firecracker.log. Firecracker adds `root=/dev/vda ro` itself for a
root drive marked read-only.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from ..model import MicroVM
from .hypervisor import (BRIDGE, CGROUP_ROOT, DEFAULT_KERNEL, DEFAULT_ROOTFS, GATEWAY, MEM_OVERHEAD_MIB,
                         RESOLVER, RUN_ROOT, SMT, HypervisorBackend)
from ..procfs import PROC_ROOT
from ..runner import Runner

DEFAULT_FIRECRACKER = "/usr/local/bin/firecracker"

__all__ = ["FirecrackerBackend", "DEFAULT_FIRECRACKER", "DEFAULT_KERNEL", "DEFAULT_ROOTFS"]


class FirecrackerBackend(HypervisorBackend):
    name = "firecracker"
    default_binary = DEFAULT_FIRECRACKER
    tap_prefix = "fc-"
    unit_prefix = "fc-vm"
    vcpu_thread_prefix = "fc_vcpu"
    log_name = "firecracker.log"
    hypervisor_options = {"virtio_transport": ["mmio", "pci"], "virtio_rng": [False, True]}

    def __init__(self, runner: Runner, log_dir: str, firecracker_bin: str = DEFAULT_FIRECRACKER,
                 kernel: str = DEFAULT_KERNEL, rootfs: str = DEFAULT_ROOTFS, bridge: str = BRIDGE,
                 run_root: str = RUN_ROOT, resolver: str = RESOLVER, gateway: str = GATEWAY,
                 mem_overhead_mib: int = MEM_OVERHEAD_MIB, extra_boot_args: str = "",
                 cgroup_root: str = CGROUP_ROOT, proc_root: str = PROC_ROOT, binary: str = "", arch: str = ""):
        super().__init__(runner, log_dir, binary=binary or firecracker_bin, kernel=kernel, rootfs=rootfs,
                         bridge=bridge, run_root=run_root, resolver=resolver, gateway=gateway,
                         mem_overhead_mib=mem_overhead_mib, extra_boot_args=extra_boot_args,
                         cgroup_root=cgroup_root, proc_root=proc_root, arch=arch or None)

    @property
    def firecracker(self) -> str:
        return self.binary

    def base_boot_args(self, microvm: MicroVM) -> List[str]:
        args = ["console=ttyS0", "reboot=k", "panic=1"]
        if self.options(microvm)["virtio_transport"] == "mmio":
            args.append("pci=off")
        return args

    def firecracker_log_path(self, microvm: MicroVM) -> str:
        return self.hypervisor_log_path(microvm)

    def vm_config(self, microvm: MicroVM) -> Dict[str, Any]:
        """Firecracker rejects unknown keys, so only its own schema goes here."""
        cfg: Dict[str, Any] = {
            "boot-source": {"kernel_image_path": self.kernel, "boot_args": self.boot_args(microvm)},
            "drives": [{"drive_id": "rootfs", "path_on_host": self.rootfs,
                        "is_root_device": True, "is_read_only": True}],
            "machine-config": {"vcpu_count": microvm.vcpus, "mem_size_mib": microvm.mem_mib, "smt": SMT},
            "network-interfaces": [{"iface_id": "eth0", "guest_mac": self.guest_mac(microvm.slot),
                                    "host_dev_name": self.tap(microvm.slot)}],
            "logger": {"log_path": self.hypervisor_log_path(microvm), "level": "Warning",
                       "show_level": True, "show_log_origin": False},
        }
        if self.options(microvm)["virtio_rng"]:
            cfg["entropy"] = {}
        return cfg

    def write_files(self, microvm: MicroVM) -> None:
        d = self.run_dir(microvm)
        self.runner.write_file(os.path.join(d, "vm.json"), json.dumps(self.vm_config(microvm), indent=2) + "\n")
        self.runner.write_file(self.hypervisor_log_path(microvm), "")   # Firecracker wants it to exist

    def hypervisor_argv(self, microvm: MicroVM) -> List[str]:
        d = self.run_dir(microvm)
        argv = [self.binary, "--api-sock", os.path.join(d, "fc.sock"), "--config-file", os.path.join(d, "vm.json")]
        if self.options(microvm)["virtio_transport"] == "pci":
            argv.append("--enable-pci")
        return argv
