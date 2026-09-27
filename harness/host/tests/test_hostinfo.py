"""GET /host/info: the collector's sources on fake trees and a fake metadata service."""
from __future__ import annotations

import json
import os
import socket
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hostd.backends.firecracker import FirecrackerBackend
from hostd.hostinfo import (collect_host_info, cpuinfo_facts, ec2_identity, topology_from_lscpu,
                            topology_from_sys)
from hostd.runner import Runner

from conftest import FakeBackend

X86_CPUINFO = """processor\t: 0
vendor_id\t: GenuineIntel
model name\t: Intel(R) Xeon(R) 6975P-C
flags\t\t: fpu vme de pse tsc msr pae hypervisor lahf_lm

processor\t: 1
vendor_id\t: GenuineIntel
model name\t: Intel(R) Xeon(R) 6975P-C
flags\t\t: fpu vme de pse tsc msr pae hypervisor lahf_lm
"""

ARM_CPUINFO = """processor\t: 0
BogoMIPS\t: 48.00
Features\t: fp asimd evtstrm aes pmull sha1 sha2 crc32
CPU implementer\t: 0x61
"""

LSCPU_FLAT = json.dumps({"lscpu": [
    {"field": "Architecture:", "data": "x86_64"},
    {"field": "CPU(s):", "data": "16"},
    {"field": "Thread(s) per core:", "data": "2"},
    {"field": "Core(s) per socket:", "data": "8"},
    {"field": "Socket(s):", "data": "1"}]})

LSCPU_NESTED = json.dumps({"lscpu": [
    {"field": "Architecture:", "data": "x86_64"},
    {"field": "Vendor ID:", "data": "GenuineIntel", "children": [
        {"field": "Model name:", "data": "Intel(R) Xeon(R) 6975P-C", "children": [
            {"field": "Thread(s) per core:", "data": "1"},
            {"field": "Core(s) per socket:", "data": "4"},
            {"field": "Socket(s):", "data": "2"}]}]}]})


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeImds:
    """IMDSv2: PUT /latest/api/token, then GETs that require the token header."""

    def __init__(self, delay_s: float = 0.0):
        self.seen = []
        imds = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, text):
                data = text.encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_PUT(self):
                imds.seen.append(("PUT", self.path, self.headers.get("X-aws-ec2-metadata-token-ttl-seconds")))
                self._send(200, "tok-123")

            def do_GET(self):
                imds.seen.append(("GET", self.path, self.headers.get("X-aws-ec2-metadata-token")))
                time.sleep(delay_s)
                if self.headers.get("X-aws-ec2-metadata-token") != "tok-123":
                    return self._send(401, "")
                values = {"/latest/meta-data/instance-id": "i-0abc123def4567890",
                          "/latest/meta-data/instance-type": "m8i.4xlarge",
                          "/latest/meta-data/ami-id": "ami-0123456789abcdef0",
                          "/latest/meta-data/placement/availability-zone": "us-east-1a"}
                if self.path in values:
                    return self._send(200, values[self.path])
                self._send(404, "")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_cpuinfo_facts(tmp_path):
    x86, arm = str(tmp_path / "x86"), str(tmp_path / "arm")
    write(x86 + "/cpuinfo", X86_CPUINFO)
    write(arm + "/cpuinfo", ARM_CPUINFO)
    assert cpuinfo_facts(x86) == {"cpu_model": "Intel(R) Xeon(R) 6975P-C", "virtualized": True}
    write(x86 + "/cpuinfo", X86_CPUINFO.replace(" hypervisor", ""))
    assert cpuinfo_facts(x86)["virtualized"] is False
    assert cpuinfo_facts(arm) == {"cpu_model": None, "virtualized": None}
    assert cpuinfo_facts(str(tmp_path / "none")) == {"cpu_model": None, "virtualized": None}


def test_topology_sources(tmp_path):
    assert topology_from_lscpu(LSCPU_FLAT) == {"threads_per_core": 2, "cores_per_socket": 8, "sockets": 1}
    assert topology_from_lscpu(LSCPU_NESTED) == {"threads_per_core": 1, "cores_per_socket": 4, "sockets": 2}
    assert topology_from_lscpu("not json") is None
    assert topology_from_lscpu(json.dumps({"lscpu": [{"field": "Architecture:", "data": "x86_64"}]})) is None
    # /sys: 2 packages x 2 cores x 2 threads = 8 logical CPUs, plus an entry that is not a CPU
    root = str(tmp_path / "cpu")
    n = 0
    for pkg in (0, 1):
        for core in (0, 1):
            for _thread in (0, 1):
                write("%s/cpu%d/topology/core_id" % (root, n), "%d\n" % core)
                write("%s/cpu%d/topology/physical_package_id" % (root, n), "%d\n" % pkg)
                n += 1
    write(root + "/cpufreq/boost", "1\n")
    os.makedirs(root + "/cpu8")                             # offline: no topology directory
    assert topology_from_sys(root) == {"threads_per_core": 2, "cores_per_socket": 2, "sockets": 2}
    assert topology_from_sys(str(tmp_path / "nothing")) is None


def test_ec2_identity_over_imdsv2():
    imds = FakeImds()
    try:
        assert ec2_identity(imds.url, 1.0) == {"instance_id": "i-0abc123def4567890", "instance_type": "m8i.4xlarge",
                                               "ami_id": "ami-0123456789abcdef0", "availability_zone": "us-east-1a"}
        assert imds.seen[0] == ("PUT", "/latest/api/token", "60")
        assert all(tok == "tok-123" for method, _, tok in imds.seen[1:])
    finally:
        imds.stop()


def test_ec2_identity_off_ec2_is_none_and_quick():
    t0 = time.monotonic()
    assert ec2_identity("http://127.0.0.1:%d" % closed_port(), 1.0) is None       # connection refused
    assert time.monotonic() - t0 < 0.5
    # a metadata service that accepts but never answers: the 1 s budget ends it
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(8)
    try:
        t0 = time.monotonic()
        assert ec2_identity("http://127.0.0.1:%d" % silent.getsockname()[1], 1.0) is None
        assert time.monotonic() - t0 < 1.6
    finally:
        silent.close()
    # slow answers share one budget rather than 1 s each
    imds = FakeImds(delay_s=0.3)
    try:
        t0 = time.monotonic()
        assert ec2_identity(imds.url, 1.0) is None
        assert time.monotonic() - t0 < 1.6
    finally:
        imds.stop()


def test_collect_host_info_docker(tmp_path):
    proc = str(tmp_path / "proc")
    write(proc + "/cpuinfo", X86_CPUINFO)
    write(proc + "/meminfo", "MemTotal:       65536000 kB\nMemAvailable:   60000000 kB\n")
    t0 = time.monotonic()
    info = collect_host_info(FakeBackend(), "local", 1.0, proc_root=proc, sys_cpu_root=str(tmp_path / "nosys"),
                             dev_kvm=str(tmp_path / "kvm"), imds_url="http://127.0.0.1:%d" % closed_port(),
                             lscpu=lambda: LSCPU_FLAT)
    assert time.monotonic() - t0 < 1.0
    assert set(info) == {"host_id", "backend", "hostd_version", "kernel_release", "cpu_model", "cpu_count",
                         "threads_per_core", "cores_per_socket", "sockets", "mem_total", "virtualized", "kvm",
                         "ec2", "metrics_period_s", "firecracker"}
    assert info["host_id"] == "local" and info["backend"] == "docker" and info["hostd_version"]
    assert info["cpu_model"] == "Intel(R) Xeon(R) 6975P-C" and info["virtualized"] is True
    assert (info["threads_per_core"], info["cores_per_socket"], info["sockets"]) == (2, 8, 1)
    assert info["mem_total"] == 65536000 * 1024 and info["cpu_count"] == os.cpu_count()
    assert info["kvm"] is False and info["ec2"] is None and info["firecracker"] is None
    assert info["metrics_period_s"] == 1.0
    write(str(tmp_path / "kvm"), "")
    info = collect_host_info(FakeBackend(), "local", None, proc_root=proc, sys_cpu_root=str(tmp_path / "nosys"),
                             dev_kvm=str(tmp_path / "kvm"), imds_url="http://127.0.0.1:%d" % closed_port(),
                             lscpu=lambda: None)
    assert info["kvm"] is True and info["metrics_period_s"] is None
    assert (info["threads_per_core"], info["cores_per_socket"], info["sockets"]) == (None, None, None)


def test_host_info_never_carries_the_hostname(tmp_path):
    info = collect_host_info(FakeBackend(), "i-0abc", 1.0, imds_url="http://127.0.0.1:%d" % closed_port())
    text = json.dumps(info)
    assert "hostname" not in text
    hostname = socket.gethostname()
    if len(hostname) >= 4:
        assert hostname not in text and hostname.split(".")[0] not in text


def test_collect_host_info_firecracker(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/nonexistent-home")      # tmp_path is shown as is, not as ~/...
    fc = tmp_path / "firecracker"
    fc.write_text("#!/bin/sh\necho 'Firecracker v1.12.1'\necho\necho 'Supported snapshot data format versions: v5.0.0'\n")
    fc.chmod(fc.stat().st_mode | stat.S_IXUSR)
    kernel, rootfs = tmp_path / "vmlinux", tmp_path / "guest.ext4"
    kernel.write_bytes(b"k" * 1234)
    rootfs.write_bytes(b"r" * 4096)
    b = FirecrackerBackend(Runner(dry_run=False), str(tmp_path / "logs"), firecracker_bin=str(fc),
                           kernel=str(kernel), rootfs=str(rootfs))
    info = collect_host_info(b, "i-0abc", 0.2, imds_url="http://127.0.0.1:%d" % closed_port(), lscpu=lambda: None)
    assert info["backend"] == "firecracker" and info["metrics_period_s"] == 0.2
    f = info["firecracker"]
    assert f == {"version": "Firecracker v1.12.1", "kernel_path": str(kernel), "kernel_bytes": 1234,
                 "rootfs_path": str(rootfs), "rootfs_bytes": 4096,
                 "boot_args_example": "console=ttyS0 reboot=k panic=1 pci=off "
                                      "ip=10.200.0.10::10.200.0.1:255.255.255.0:vm0:eth0:off:10.42.0.2",
                 "mem_overhead_mib": 256, "cpu_quota_pct_per_vcpu": 100, "smt": False}
    # missing files and a dry-run runner: nulls, not errors
    b2 = FirecrackerBackend(Runner(dry_run=True), str(tmp_path / "logs"), firecracker_bin=str(tmp_path / "nope"),
                            kernel=str(tmp_path / "nope"), rootfs=str(tmp_path / "nope"))
    f2 = b2.info()
    assert f2["version"] is None and f2["kernel_bytes"] is None and f2["rootfs_bytes"] is None


def test_paths_under_home_are_shown_with_a_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    b = FirecrackerBackend(Runner(dry_run=True), str(tmp_path / "logs"), kernel=str(tmp_path / "fc" / "vmlinux"),
                           rootfs="/var/lib/fleetkit/guest.ext4")
    f = b.info()
    assert f["kernel_path"] == os.path.join("~", "fc", "vmlinux")
    assert f["rootfs_path"] == "/var/lib/fleetkit/guest.ext4"
    assert str(tmp_path) not in json.dumps(f)
