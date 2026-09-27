"""Dry-run rendering of commands and configs for both backends (sections 2-4)."""
from __future__ import annotations

import json
import shlex

from hostd.__main__ import main
from hostd.backends.docker import DockerBackend
from hostd.backends.firecracker import FirecrackerBackend
from hostd.model import MicroVM
from hostd.runner import Runner


def _microvm(backend, slot=0, fault=None, vcpus=2, mem_mib=2048):
    return MicroVM(id="s%03d-abcdef01" % slot, slot=slot, backend=backend.name, address=backend.address(slot),
                   vcpus=vcpus, mem_mib=mem_mib, fault=fault, ready_timeout_s=60, max_lifetime_s=600,
                   idle_timeout_s=120, fixture_base_url=backend.fixture_base_url)


def test_docker_run_command():
    b = DockerBackend(Runner(dry_run=True), "/tmp/logs", image="fleetkit-guest:dev")
    s = _microvm(b, slot=4, fault="hang_step", vcpus=1, mem_mib=512)
    argv = b.run_argv(s)
    text = shlex.join(argv)
    assert argv[:3] == ["docker", "run", "-d"]
    assert "--label fleetkit.role=microvm" in text
    assert "--label fleetkit.microvm_id=s004-abcdef01" in text
    assert "--network fleetkit" in text
    assert "--cpus 1" in text and "--memory 512m" in text
    assert "-p 127.0.0.1:18084:8080" in text
    assert "-e FLEETKIT_FAULT=hang_step" in text
    assert "-e FLEETKIT_FIXTURE_URL=http://fixture" in text
    assert argv[-1] == "fleetkit-guest:dev"
    assert b.container_name(s) == "fleetkit-microvm-s004-abcdef01"
    # no fault -> no FLEETKIT_FAULT
    assert "FLEETKIT_FAULT" not in shlex.join(b.run_argv(_microvm(b, slot=4)))


def test_docker_dry_run_renders_create_and_destroy():
    b = DockerBackend(Runner(dry_run=True), "/tmp/logs")
    s = _microvm(b, slot=1)
    b.create(s)
    b.destroy(s)
    text = b.runner.render_text()
    assert "docker network inspect fleetkit" in text
    assert "docker run -d --name fleetkit-microvm-s001-abcdef01" in text
    assert "docker rm -f fleetkit-microvm-s001-abcdef01" in text
    assert s.handle["port"] == 18081
    assert s.console_log.endswith("microvms/s001-abcdef01/console.log")


def test_firecracker_config_and_commands():
    b = FirecrackerBackend(Runner(dry_run=True), "/var/log/fleetkit", firecracker_bin="/usr/local/bin/firecracker",
                           kernel="/var/lib/fleetkit/vmlinux", rootfs="/var/lib/fleetkit/guest.ext4")
    s = _microvm(b, slot=3, fault="never_ready", vcpus=2, mem_mib=2048)
    cfg = b.vm_config(s)
    assert set(cfg) == {"boot-source", "drives", "machine-config", "network-interfaces", "logger"}
    assert cfg["boot-source"]["kernel_image_path"] == "/var/lib/fleetkit/vmlinux"
    args = cfg["boot-source"]["boot_args"]
    assert args.startswith("console=ttyS0 reboot=k panic=1 pci=off ")
    assert "ip=10.200.0.13::10.200.0.1:255.255.255.0:vm3:eth0:off:10.42.0.2" in args
    assert args.endswith("fleetkit.fault=never_ready")
    drive = cfg["drives"][0]
    assert drive["path_on_host"] == "/var/lib/fleetkit/guest.ext4"
    assert drive["is_root_device"] is True and drive["is_read_only"] is True
    assert cfg["machine-config"] == {"vcpu_count": 2, "mem_size_mib": 2048, "smt": False}
    nic = cfg["network-interfaces"][0]
    assert nic == {"iface_id": "eth0", "guest_mac": "06:00:0A:C8:00:0D", "host_dev_name": "fc-3"}
    assert cfg["logger"]["log_path"] == "/var/log/fleetkit/microvms/s003-abcdef01/firecracker.log"

    b.create(s)
    b.destroy(s)
    text = b.runner.render_text()
    assert "ip tuntap add dev fc-3 mode tap" in text
    assert "ip link set fc-3 master fcbr0" in text
    assert "ip link set fc-3 up" in text
    scope = shlex.join(b.scope_argv(s))
    assert scope.startswith("systemd-run --scope --quiet --unit fc-vm3 ")
    assert "-p MemoryMax=2304M" in scope and "-p CPUQuota=200%" in scope
    assert "--config-file /run/fleetkit/s003-abcdef01/vm.json" in scope
    assert "--api-sock /run/fleetkit/s003-abcdef01/fc.sock" in scope
    assert "--description 'fleetkit microVM s003-abcdef01'" in scope
    sidecar = [e for e in b.runner.rendered if e["kind"] == "write" and e["text"].endswith("/microvm.json")]
    assert json.loads(sidecar[0]["content"])["microvm_id"] == "s003-abcdef01"
    assert "> /var/log/fleetkit/microvms/s003-abcdef01/console.log 2>&1" in text
    assert "systemctl kill --signal=SIGKILL fc-vm3.scope" in text
    assert "ip link del fc-3" in text
    assert "rm -rf /run/fleetkit/s003-abcdef01" in text
    # the rendered vm.json is what Firecracker will read
    written = [e for e in b.runner.rendered if e["kind"] == "write" and e["text"].endswith("vm.json")]
    assert json.loads(written[0]["content"]) == cfg
    assert s.console_log == "/var/log/fleetkit/microvms/s003-abcdef01/console.log"
    # no fault -> no fleetkit.fault= on the command line
    assert "fleetkit.fault" not in b.boot_args(_microvm(b, slot=3))


def test_render_cli(capsys):
    rc = main(["--backend", "firecracker", "--render", "--slot", "2", "--fault", "hang_task"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "fleetkit.fault=hang_task" in out
    assert "systemd-run --scope --quiet --unit fc-vm2" in out
    assert "06:00:0A:C8:00:0C" in out
    rc = main(["--backend", "docker", "--render", "--slot", "0"])
    out = capsys.readouterr().out
    assert rc == 0 and "-p 127.0.0.1:18080:8080" in out
