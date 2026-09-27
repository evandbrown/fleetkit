# Host daemon (`hostd`)

One per host. Owns session lifecycle for two backends, enforces lifetime and idle timeouts, proxies tasks to the guest daemon, samples host and per-session resources, and is the telemetry hub for everything the guest reports. The contract is [docs/harness-design.md](../../docs/harness-design.md), sections 3, 4, 6 and 8.

```
python3 -m hostd --backend docker|firecracker [--port 8090] [--log-dir results/hostd] [--host-id <id>] [--metrics-period 1.0] [--dry-run]
make hostd                      # the same, from the repo root, in harness/.venv
python3 -m hostd --backend firecracker --render --slot 3 --fault hang_task   # print one session's plan
```

Stdlib only at runtime. `psutil` (in `requirements.txt`) is optional and only supplies host memory and CPU figures on macOS; Linux reads `/proc`. Python 3.11-compatible (`from __future__ import annotations`, no `match`).

## API (port 8090)

| Route | Behaviour |
|---|---|
| `POST /sessions` `{backend, count, vcpus, mem_mib, ready_timeout_s, max_lifetime_s, idle_timeout_s, launch_interval_ms, fault}` | Allocates slots and returns `[{id, slot, address}]` at once; creation and readiness polling (every 250 ms) run in the background. `backend` must match the daemon's. 409 when fewer slots are free than requested. |
| `GET /sessions`, `GET /sessions/{id}` | The section 4 record: `state, backend, slot, address, created_ts, process_started_ts, ready_ts, destroyed_ts, last_activity_ts, startup_ms, cleanup_ms, outcome, error`, plus `id, vcpus, mem_mib, fault, fixture_base_url, run_id, trial_id, console_log, trace_id` and the timeouts in force, plus the boot phases and `guest_info` below. |
| `POST /sessions/{id}/task` | Proxies to the guest with deadline `task_timeout_ms + 5000`. 409 `session_not_ready` unless the session is `ready`. Otherwise 200 with the guest's body plus `session_id, slot, task_id, trace_id, clock_offset_ns, guest_mem_available, chromium_rss, host_rtt_ms, proxy_ms, session_outcome`. No answer by the deadline: `guest_unreachable`, session destroyed with `task_failure_destroyed` (or the outcome the reaper already set, e.g. `lifetime_expired`). |
| `DELETE /sessions/{id}` | Idempotent; blocks until every leftover is gone; `cleanup_ms` runs from receipt to that point. |
| `GET /host/metrics` | Latest sample (every `--metrics-period` seconds, default 1): `ts, mem_total, mem_available, cpu_util, steal, psi{cpu,memory,io}{some_avg10, some_total, full_avg10, full_total}, cpu_count, hostd_cpu_usec, hostd_rss_bytes, sessions[{id, rss_bytes, cgroup_memory_current, cgroup_memory_peak, cpu_usage_usec, cpu_vcpu_usec, cpu_vmm_usec, cpu_throttled_usec, cpu_nr_throttled, cpu_pressure_some_total_us, cpu_pressure_full_total_us, memory_pressure_some_total_us}]`. `steal` and `psi` are null where the platform has none (macOS); the per-session fields after `cpu_usage_usec` are Firecracker-only (null on docker). |
| `GET /host/info` | Static facts, computed once and cached: `host_id, backend, hostd_version, kernel_release, cpu_model, cpu_count, threads_per_core, cores_per_socket, sockets, mem_total, virtualized, kvm, ec2 {instance_id, instance_type, ami_id, availability_zone}` (IMDSv2, 1 s budget, null off EC2), `metrics_period_s` (null with `--no-metrics`), and `firecracker {version, kernel_path, kernel_bytes, rootfs_path, rootfs_bytes, boot_args_example, mem_overhead_mib, cpu_quota_pct_per_vcpu, smt}` (null on docker). Never the hostname. |
| `GET /host/verify-clean` | `{clean, leftovers}`. Docker: containers labelled `fleetkit.role=session`. Firecracker: `firecracker` processes, `fc-*` taps, `fc-vm*` scopes, anything under `/run/fleetkit/`. Other roles (fixture, observability, dev tests) are not counted. |
| `GET /health` | `{ok, backend, dry_run, host_id, uptime_s, sessions: {state: count}, fixture_base_url}` |

Correlation: the daemon reads `traceparent` (W3C) and either `baggage` (`fleetkit.run_id`, `fleetkit.trial_id`) or the driver's `X-Fleetkit-Run-Id` / `X-Fleetkit-Trial-Id` / `X-Fleetkit-Task-Id` headers. `run_id` and `trial_id` may also be body fields on `POST /sessions`.

## States, outcomes, faults

`creating → booting → ready ⇄ busy → destroying → destroyed`, or `failed` (terminal; cleanup still runs and sets `destroyed_ts` and `cleanup_ms`). `created_ts` is taken when the launch thread starts on the session, before any container or tap; `process_started_ts` after `docker run` returns or Firecracker is spawned; `startup_ms = ready_ts - created_ts`. Every transition emits a `session.state {session_id, from, to, ts, outcome}` log record under the session's trace, including the initial `null → creating`.

Outcomes: `completed`, `startup_timeout` (no `/health` 200 by `ready_timeout_s`), `startup_error` (`docker run` failed, or the container/VMM exited during boot; checked once a second so `crash_on_start` is caught within seconds), `lifetime_expired`, `idle_expired`, `task_failure_destroyed`.

The reaper runs every second: any live session past `max_lifetime_s` since `created_ts` (busy or not, booting or not) is destroyed with `lifetime_expired`; a `ready` session past `idle_timeout_s` since `last_activity_ts` with `idle_expired`. `busy` sessions are never idle-reaped. `last_activity_ts` is set at task start and end.

Boot phases: the `/health` answer that finds a session ready also dates its boot on the host clock. With `t_host = send_ns/1e9 + rtt_ns/2e9` for that request, `kernel_start_ts = t_host - kernel_uptime_s`, `guestd_start_ts = t_host - guestd_uptime_s` (`uptime_s` from an older guest), `chromium_launch_ts = guestd_start_ts + chromium_launch_s`, `chromium_ready_ts = guestd_start_ts + chromium_ready_s`; each is null when its input is missing. `guest_info` keeps `guestd_version, chromium_version, chromium_flags, kernel_cmdline, vcpus, mem_total` from the same answer. On docker `kernel_uptime_s` is the Docker VM's, so `kernel_start_ts` means nothing there.

Faults pass through unchanged: `FLEETKIT_FAULT=<name>` in the container environment, `fleetkit.fault=<name>` on the kernel command line. The closed set is validated (`crash_on_start`, `never_ready`, `hang_task`, `hang_step`, `slow_step:<ms>`).

Slot allocation is lowest-free. On the docker backend a candidate slot's host port is probe-bound first and skipped (with a warning) if something else holds it, so a stray container on 18080 costs one slot instead of a failed session.

## Backends

**docker.** `docker run -d --name fleetkit-session-<id> --label fleetkit.role=session --label fleetkit.session_id=<id> --label fleetkit.slot=<n> --network fleetkit --cpus <vcpus> --memory <mem_mib>m -p 127.0.0.1:<18080+slot>:8080 -e FLEETKIT_FIXTURE_URL=http://fixture [-e FLEETKIT_FAULT=<fault>] fleetkit-guest:dev`. Sessions are addressed as `127.0.0.1:<port>`; tasks get `fixture_base_url: http://fixture` unless the caller supplies one. Destroy is `docker logs --tail 2000` into the session's console log, then `docker rm -f`. Per-container figures come from the Engine API (`/containers/<name>/stats?stream=false&one-shot=true` over the unix socket); `cgroup_memory_peak` is null on cgroup v2 hosts, where Docker does not report it. If the `fleetkit` network is missing the daemon creates it carrying compose's labels, so a later `make up` adopts it instead of refusing.

**firecracker** (Linux hosts; `--dry-run` anywhere). Slot `s`: tap `fc-<s>` on `fcbr0`, guest IP `10.200.0.(10+s)`, MAC `06:00:0A:C8:00:<10+s hex>`, kernel `ip=10.200.0.<10+s>::10.200.0.1:255.255.255.0:vm<s>:eth0:off:10.42.0.2`, address `10.200.0.(10+s):8080`, fixture `http://10.200.0.1:8081`. Files under `/run/fleetkit/<id>/`: `vm.json` (boot source with `console=ttyS0 reboot=k panic=1 pci=off` plus `ip=` and `fleetkit.fault=`; the shared rootfs as a read-only root drive; `machine-config` with `smt: false`; the tap; Firecracker's own log pointed at the evidence directory), `session.json` (where the console went, which slot, tap, unit), `fc.sock`. Launch: `systemd-run --scope --quiet --unit fc-vm<s> -p MemoryMax=<mem_mib+256>M -p MemorySwapMax=0 -p CPUQuota=<vcpus*100>% firecracker --api-sock ... --config-file ...` with stdout (the serial console) appended to `<log-dir>/sessions/<id>/console.log`. Destroy: `systemctl kill --signal=SIGKILL fc-vm<s>.scope`, wait for the process, `ip link del fc-<s>`, wait for the scope to go inactive, `systemctl reset-failed`, remove the run directory. Sampling reads the scope's cgroup (`memory.current`, `memory.peak`, `cpu.stat` `usage_usec`/`throttled_usec`/`nr_throttled`, the `some`/`full` totals of `cpu.pressure` and the `some` total of `memory.pressure`), sums `VmRSS` over `cgroup.procs`, and splits the Firecracker process's CPU by thread from `/proc/<pid>/task/<tid>/{comm,stat}`: threads named `fc_vcpu <n>` are `cpu_vcpu_usec`, every other thread (event loop and device emulation, API) is `cpu_vmm_usec`. Firecracker appends `root=/dev/vda ro` itself for a read-only root drive, so the boot args do not repeat it. Paths: `--firecracker` (default `/usr/local/bin/firecracker`), `--kernel` (`/var/lib/fleetkit/vmlinux`), `--rootfs` (`/var/lib/fleetkit/guest.ext4`), or `FLEETKIT_FIRECRACKER`, `FLEETKIT_KERNEL`, `FLEETKIT_ROOTFS`.

`--dry-run` renders every command, file and config to the log instead of executing, marks sessions ready without polling, and answers `guest_unreachable` for tasks. `--render` prints one session's create and destroy plan and exits.

## Telemetry and evidence

Under `--log-dir` (default `results/hostd`): `hostd.log` (human-readable), `logs.jsonl`, `spans.jsonl`, `host_metrics.jsonl`, and `sessions/<id>/console.log` (plus `firecracker.log` for VMs). Spans: `session.create` (parent: the caller's `traceparent`), `session.destroy`, `task.proxy`, and, under service `guest-daemon`, `guest.task` and `step.<name>` reconstructed from the guest's `guest_clock_ns` and step offsets after `clock_offset_ns = host_send_ns + rtt/2 - guest_clock_ns`, where `rtt` is that of a `/health` probe made just before the task rather than the task call itself. The guest's `log_tail` is forwarded as `guest-daemon` log records under the task's trace. Every record carries `fleetkit.run_id`, `trial_id`, `session_id`, `task_id`, `backend`, `host_id` where known.

OTLP/HTTP export (JSON encoding, `/v1/traces`, `/v1/logs`, `/v1/metrics`) goes to `--otlp-endpoint` (default `http://127.0.0.1:4318` or `OTEL_EXPORTER_OTLP_ENDPOINT`), best-effort with a two-second timeout and exponential back-off while the collector is away; `--no-otlp` disables it. The JSONL files are the durable copy and are written regardless. The driver copies the lines carrying its run id into the bundle.

On SIGTERM/SIGINT the daemon destroys every live session, runs verify-clean, logs the result and exits.

## Tests

```
cd harness/host && ../.venv/bin/python -m pytest
```

Offline: a fake backend, fake guest and fake clock for allocation, rendering, state transitions and the reaper; and the real HTTP server with the real guest client against an in-process stub guest (`tests/stubguest/stubguestd.py`) for the proxy paths, 409s, `hang_task`, `never_ready`, and the idle and lifetime reapers. The same stub can be built as a container (`docker build -t fleetkit-guest-stub:dev tests/stubguest`; run with `--image fleetkit-guest-stub:dev`) when the real guest image is not available.
