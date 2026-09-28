"""Docker backend: one labeled container per microVM on the `fleetkit` network.

Section 3: the guest daemon's port 8080 is published to 127.0.0.1:<18080+slot>,
so the daemon addresses microVMs by host port on every platform. Memory and CPU
limits stand in for the VM shape. Lifecycle goes through the docker CLI (so
`--dry-run` renders readable commands); per-container stats come from the
Engine API over the unix socket, because `docker stats` blocks for a second.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
from typing import Any, Dict, List, Optional

from ..model import MicroVM, encode_chromium_flags
from ..runner import CommandError, Runner
from .base import Backend, BackendError, empty_sample

PORT_BASE = 18080
PORT_LAST = 18199
GUEST_PORT = 8080
NETWORK = "fleetkit"
ROLE_LABEL = "fleetkit.role=microvm"


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.unix_path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.unix_path)
        self.sock = s


def docker_socket_path() -> Optional[str]:
    host = os.environ.get("DOCKER_HOST", "")
    if host.startswith("unix://"):
        return host[len("unix://"):]
    for p in ("/var/run/docker.sock", os.path.expanduser("~/.docker/run/docker.sock")):
        if os.path.exists(p):
            return p
    return None


class DockerBackend(Backend):
    name = "docker"
    max_slots = PORT_LAST - PORT_BASE + 1
    fixture_base_url = "http://fixture"

    def __init__(self, runner: Runner, log_dir: str, image: str = "fleetkit-guest:dev",
                 network: str = NETWORK, docker_bin: str = "docker", extra_env: Optional[Dict[str, str]] = None):
        super().__init__(runner, log_dir)
        self.image = image
        self.network = network
        self.docker = docker_bin
        self.extra_env = dict(extra_env or {})
        self._network_checked = False

    # --- naming ------------------------------------------------------------
    @staticmethod
    def port(slot: int) -> int:
        if not 0 <= slot <= PORT_LAST - PORT_BASE:
            raise ValueError("slot %d outside the port range" % slot)
        return PORT_BASE + slot

    def address(self, slot: int) -> str:
        return "127.0.0.1:%d" % self.port(slot)

    def slot_usable(self, slot: int) -> bool:
        """Docker Desktop binds published ports on the host, so a bind probe sees any holder."""
        if self.runner.dry_run:
            return True
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", self.port(slot)))
            return True
        except OSError:
            return False
        finally:
            probe.close()

    @staticmethod
    def container_name(microvm: MicroVM) -> str:
        return "fleetkit-microvm-%s" % microvm.id

    def run_argv(self, microvm: MicroVM) -> List[str]:
        argv = [self.docker, "run", "-d",
                "--name", self.container_name(microvm),
                "--label", ROLE_LABEL,
                "--label", "fleetkit.microvm_id=%s" % microvm.id,
                "--label", "fleetkit.slot=%d" % microvm.slot,
                "--network", self.network,
                "--cpus", str(microvm.vcpus),
                "--memory", "%dm" % microvm.mem_mib,
                "-p", "127.0.0.1:%d:%d" % (self.port(microvm.slot), GUEST_PORT),
                "-e", "FLEETKIT_FIXTURE_URL=%s" % self.fixture_base_url,
                "-e", "FLEETKIT_MICROVM_ID=%s" % microvm.id]
        if microvm.fault:
            argv += ["-e", "FLEETKIT_FAULT=%s" % microvm.fault]
        if microvm.chromium_extra_flags:
            argv += ["-e", "FLEETKIT_CHROMIUM_EXTRA_FLAGS=%s" % encode_chromium_flags(microvm.chromium_extra_flags)]
        for k, v in sorted(self.extra_env.items()):
            argv += ["-e", "%s=%s" % (k, v)]
        argv.append(self.image)
        return argv

    # --- lifecycle ---------------------------------------------------------
    def _ensure_network(self) -> None:
        if self._network_checked:
            return
        r = self.runner.run([self.docker, "network", "inspect", self.network], check=False, timeout=20)
        if not r.ok:
            # The observability compose file normally creates it (name: fleetkit). A bare host
            # daemon creates it too, with compose's labels so a later `make up` adopts it instead
            # of refusing ("exists but was not created by compose").
            self.runner.run([self.docker, "network", "create",
                             "--label", "com.docker.compose.network=%s" % self.network,
                             "--label", "com.docker.compose.project=%s" % self.network,
                             self.network], check=False, timeout=20)
        self._network_checked = True

    def create(self, microvm: MicroVM) -> None:
        self._ensure_network()
        microvm.handle["container"] = self.container_name(microvm)
        microvm.handle["port"] = self.port(microvm.slot)
        microvm.console_log = os.path.join(self.log_dir, "microvms", microvm.id, "console.log")
        self.runner.mkdir(os.path.dirname(microvm.console_log))
        try:
            r = self.runner.run(self.run_argv(microvm), timeout=60)
        except CommandError as e:
            raise BackendError("docker run failed: %s" % (e.result.stderr or e.result.stdout).strip()[-400:]) from e
        microvm.handle["container_id"] = r.stdout.strip()[:12] or None

    def alive(self, microvm: MicroVM) -> bool:
        name = microvm.handle.get("container")
        if not name:
            return False
        r = self.runner.run([self.docker, "inspect", "-f", "{{.State.Running}}", name], check=False, timeout=20)
        if self.runner.dry_run:
            return True
        return r.ok and r.stdout.strip() == "true"

    def exit_info(self, microvm: MicroVM) -> Optional[str]:
        name = microvm.handle.get("container")
        if not name or self.runner.dry_run:
            return None
        r = self.runner.run([self.docker, "inspect", "-f",
                             "status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} err={{.State.Error}}",
                             name], check=False, timeout=20)
        if not r.ok:
            return "container gone"
        info = r.stdout.strip()
        logs = self.runner.run([self.docker, "logs", "--tail", "5", name], check=False, timeout=20)
        tail = (logs.stdout + logs.stderr).strip().splitlines()[-3:]
        if tail:
            info += " | " + " / ".join(tail)
        return info[:500]

    def destroy(self, microvm: MicroVM) -> None:
        name = microvm.handle.get("container") or self.container_name(microvm)
        if microvm.console_log:
            # Keep the guest's stdout/stderr as the microVM's console log (evidence bundle).
            r = self.runner.run([self.docker, "logs", "--tail", "2000", name], check=False, timeout=30)
            if not self.runner.dry_run and (r.stdout or r.stderr):
                os.makedirs(os.path.dirname(microvm.console_log), exist_ok=True)
                with open(microvm.console_log, "a") as f:
                    f.write(r.stdout)
                    f.write(r.stderr)
        self.runner.run([self.docker, "rm", "-f", name], check=False, timeout=60)

    # --- sampling ----------------------------------------------------------
    def sample(self, microvm: MicroVM) -> Dict[str, Optional[int]]:
        # The Firecracker-only figures (vCPU/hypervisor split, throttling, cgroup pressure) stay None.
        out = empty_sample()
        name = microvm.handle.get("container")
        sock = docker_socket_path()
        if not name or not sock or self.runner.dry_run:
            return out
        try:
            conn = _UnixHTTPConnection(sock, timeout=2.0)
            conn.request("GET", "/containers/%s/stats?stream=false&one-shot=true" % name)
            resp = conn.getresponse()
            raw = resp.read()
            conn.close()
            if resp.status != 200:
                return out
            stats = json.loads(raw)
        except (OSError, ValueError, http.client.HTTPException):
            return out
        mem = stats.get("memory_stats") or {}
        inner = mem.get("stats") or {}
        # cgroup v2 reports anon; v1 reports rss. `usage` includes page cache on both.
        rss = inner.get("anon", inner.get("rss"))
        out["rss_bytes"] = int(rss) if rss is not None else None
        out["cgroup_memory_current"] = int(mem["usage"]) if mem.get("usage") is not None else None
        out["cgroup_memory_peak"] = int(mem["max_usage"]) if mem.get("max_usage") is not None else None
        cpu = ((stats.get("cpu_stats") or {}).get("cpu_usage") or {}).get("total_usage")
        out["cpu_usage_usec"] = int(cpu) // 1000 if cpu is not None else None
        return out

    def verify_clean(self) -> List[str]:
        r = self.runner.run([self.docker, "ps", "-a", "--filter", "label=%s" % ROLE_LABEL,
                             "--format", "{{.Names}} ({{.Status}})"], check=False, timeout=30)
        if not r.ok:
            return ["docker ps failed: %s" % r.stderr.strip()[:200]]
        return ["container %s" % line.strip() for line in r.stdout.splitlines() if line.strip()]

    def check_host(self) -> List[str]:
        problems = []
        r = self.runner.run([self.docker, "version", "--format", "{{.Server.Version}}"], check=False, timeout=20)
        if not r.ok:
            problems.append("docker engine not reachable: %s" % (r.stderr or r.stdout).strip()[:200])
            return problems
        r = self.runner.run([self.docker, "image", "inspect", "-f", "{{.Id}}", self.image], check=False, timeout=20)
        if not r.ok:
            problems.append("image %s not found (build it with `make guest-image`)" % self.image)
        return problems
