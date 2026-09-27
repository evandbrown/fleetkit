# Harness design: one browser task, end to end

**Status:** implementation design, revision 2. Revision 1 was reviewed by three independent reviewers (requirements, integration, delivery); their accepted findings are folded in and listed in section 13. Routine technical decisions are made here and listed in section 12. This document is the contract the components are built to.

**Goal of the first milestone:** a local smoke loop that reliably proves task success, timeout handling and cleanup on the container backend, then one small run on AWS with real Firecracker microVMs, with the evidence preserved and the temporary resources torn down. Density tuning and a second VMM come later.

## 0. What the harness must satisfy

- One standard task against our own fixture site: load the site, search for an item, open it, add it to the cart, verify the cart.
- A trial: create N sessions (one browser in one microVM), wait until all are ready, start N tasks at the same moment, measure, destroy. Repeat at several N. Startup and cleanup are measured separately from the task.
- Failures and timeouts are recorded and categorized, never hidden in a success rate.
- Every session has a maximum lifetime and an idle timeout, enforced by the host, so a lost caller can't leak a VM.
- The headline is the highest N actually tested successfully. It is never called maximum capacity.
- Cost per task from public on-demand pricing, execution-only and observed (including setup and cleanup), with fixture-serving cost reported separately. Every number labeled measured, modeled or assumed.
- Containers are the development backend; Firecracker is the measurement backend. Real numbers come only from the Firecracker backend on AWS.

## 1. Components and responsibilities

| Component | Where it runs | Owns | Talks to |
|---|---|---|---|
| **fixture** | nginx container (Mac and host) | The static shopping site and its manifest (page weights, request counts, DOM size, expected bytes and requests per task); the task's product list | Serves guests over HTTP |
| **guest daemon** (`guestd`) | Inside every session: a container locally, a microVM on AWS | Starting Chromium, one task at a time over the DevTools protocol, per-step timings, bytes and request counts, a final screenshot, its own recent log lines | Chromium on loopback; answers the host daemon on port 8080 |
| **host daemon** (`hostd`) | One per host (Mac or EC2), as a root systemd unit on the host | Session lifecycle for two backends (`docker`, `firecracker`): create, readiness, task proxy, lifetime and idle enforcement, destroy, verify-clean; host and per-session resource sampling; telemetry hub (its own spans, guest spans emitted after the fact, guest log forwarding, state-transition events) | Docker Engine or Firecracker processes; guests; the collector |
| **driver** | Where the operator runs it (Mac; on the EC2 host as a transient systemd unit) | The trial protocol; incremental CSV and JSON outputs; the report; the smoke suite; fault injection; the evidence bundle | The host daemon; the collector |
| **observability** | `grafana/otel-lgtm` container (Mac and host), optional | Traces, logs and metrics from driver and host daemon, correlated by run, trial, session and task ids; a durable OTLP-JSON copy on disk | Receives OTLP on 4318 |

Why a daemon in the guest: Chromium's DevTools port binds to loopback only in new headless mode and the address switch is won't-fix, so something must run next to Chromium. Making that something own the task keeps step timings next to the browser and keeps the host daemon generic. The guest daemon has no telemetry SDK: it echoes the `traceparent` it received and returns timestamps, and the host daemon turns those into spans under a `guest-daemon` service.

## 2. Images: reproducible and declarative

**Guest image, one source for both backends.** `images/guest/Dockerfile`, `FROM debian:bookworm-slim@sha256:<digest from images/lock.env>`, installs `chromium` (Debian's real package, 154.x, amd64 and arm64), `fonts-liberation`, `python3`, `python3-websockets` (the apt package, 10.x API, so both architectures run the same library), `tini`, `iproute2`, `ca-certificates`, and the guest daemon. The Docker backend runs this image directly. The Firecracker backend uses the same build turned into a root filesystem: `docker create` + `docker export` of the image, the tree patched so `/etc/resolv.conf` is a symlink to `/run/resolv.conf` (BuildKit bind-mounts resolv.conf during builds, so this can't be done in the Dockerfile), then `mkfs.ext4 -d` in a pinned alpine e2fsprogs container. No buildx, mounts, loop devices or root. The image records `apt list --installed` into `/etc/fleetkit-packages.txt`; the build writes a manifest with the base digest, the rootfs sha256 and the package list.

**Guest init.** A short POSIX `/sbin/init` that writes a marker to the console after each step and drops to a shell on the console if anything fails, so a dead boot is debuggable from its console log: mount devtmpfs, proc, sysfs; `mkdir -p /dev/pts /dev/shm`; mount devpts and tmpfs on `/dev/shm`, `/run`, `/tmp`; `cp /proc/net/pnp /run/resolv.conf` (the `ip=` argument carries the resolver); read `fleetkit.fault=<name>` from `/proc/cmdline` into `FLEETKIT_FAULT`; export `HOME=/tmp`; `exec tini -g -- python3 -m guestd`. Network comes from the kernel `ip=` argument; the CI kernel config confirms `CONFIG_IP_PNP=y` and `CONFIG_DEVTMPFS_MOUNT=y`. The root filesystem is mounted read-only and shared by every VM; everything Chromium writes goes to tmpfs. Chromium is launched as `/usr/lib/chromium/chromium` directly with an explicit flag list (not Debian's wrapper, which sources `/etc/chromium.d/*` and adds API keys, extensions and background traffic): `--headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage --remote-debugging-port=9222 --user-data-dir=/tmp/profile --no-first-run --no-default-browser-check --disable-background-networking --disable-component-update --disable-sync --disable-default-apps --window-size=1280,800 about:blank`. The VM is the security boundary; the sandbox is dropped inside it.

**Guest kernel.** Firecracker's prebuilt CI kernel `vmlinux-6.18.48` from a pinned dated prefix of the `spec.ccfc.min` bucket (path-style URL; the virtual-hosted one fails TLS), for x86_64 and aarch64. Guest 6.1 left Firecracker's support window on 2026-09-02. The bucket publishes no checksums, so `images/lock.env` records the sha256 and every fetch verifies it.

**Firecracker.** v1.17.0 release binary, verified against the published `.sha256.txt`. Run without the jailer for this experiment; each VM runs in its own systemd scope (`systemd-run --scope`) with memory and CPU limits, which gives a cgroup v2 leaf with `memory.current`, `memory.peak` and `cpu.stat` for free. Upstream allows constraints "equal or more restrictive" applied by the operator.

**Host.** Stock Amazon Linux 2023 with kernel 6.18 (a supported Firecracker host kernel; AL2023's default since August 2026), resolved once from the public SSM parameter into `infra/experiments/ami.lock.json` so Terraform reads a fixed AMI id. cloud-init does only what must happen before anything else, in under five minutes: arm the self-shutdown timer as its very first `bootcmd`; install docker, iptables-nft, e2fsprogs, git, python3.12; download and verify Firecracker and the guest kernel; create the bridge and NAT; clone the repository at the pinned commit. Everything else (guest image, rootfs, fixture, host daemon unit, observability) is an idempotent `make` target run over SSM Run Command, so a fix is a push and one command, not a new instance. AL2023 has no compose package; compose is a pinned binary from the docker/compose releases, verified by checksum, or the host runs the two service containers with plain `docker run` from the same Makefile. No Packer, no custom AMI.

## 3. Networking

**Docker backend (Mac and host).** One Docker network named exactly `fleetkit` (compose sets `name:` so it isn't prefixed); session containers are attached to it. The guest daemon's port 8080 is published to a host port per slot from the range 18080-18199 (Docker Desktop doesn't route to container IPs from macOS), so the host daemon addresses sessions as `127.0.0.1:<port>`. Guests reach the fixture by service name, `http://fixture`; the fixture is also published on `127.0.0.1:8081` so the driver can check it before a trial. Memory and CPU limits per container stand in for the VM shape.

**Firecracker backend (host).** A bridge `fcbr0` at `10.200.0.1/24`; one tap per session attached to it; slot `s` gets guest IP `10.200.0.(10+s)` and MAC `06:00:0A:C8:00:<10+s in hex>`, pushed by the kernel argument `ip=10.200.0.<10+s>::10.200.0.1:255.255.255.0:vm<s>:eth0:off:10.42.0.2` (the VPC resolver). The host daemon reaches guests directly on the bridge. Egress: `ip_forward=1`; with the `iptables` binary (nft backend, the one Docker uses) the accepts go in the `DOCKER-USER` chain, which Docker evaluates first and never clobbers (`-I DOCKER-USER -i fcbr0 -j ACCEPT`, `-I DOCKER-USER -o fcbr0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT`), and `-t nat -A POSTROUTING -s 10.200.0.0/24 ! -o fcbr0 -j MASQUERADE`; the egress interface is discovered from the default route (it's `ens5` on AL2023, not `eth0`). Order: docker started, then bridge, then rules. The fixture is nginx published on the bridge address, `http://10.200.0.1:8081`, started after the bridge exists, so the task doesn't depend on NAT; the smoke test checks NAT separately with one HTTPS fetch from inside a VM.

**Fixture placement.** The vCPU quota increase to 32 has been granted, so a separate fixture host is possible; for this milestone the fixture stays on the experiment host to keep one instance and one failure domain. A separate fixture host and the S3 option are follow-ups.

## 4. APIs, enums, timeouts

**Guest daemon, port 8080.**
- `GET /health` → 200 `{ready: true, chromium_version, uptime_s}` once Chromium answers `/json/version`; 503 before.
- `POST /task` `{task_id, fixture_base_url, product_id, query, expected_title, step_timeout_ms, task_timeout_ms}` → on success `{ok: true, failure_category: "ok", steps: [{name, dispatch_ns, settle_ns, duration_ms}], task_ms, bytes_received, request_count, screenshot_b64, guest_clock_ns, traceparent, log_tail}`; on failure the same shape with `ok: false`, `failure_category`, `failed_step`, `error`, and the steps completed so far. Only one task at a time; a second concurrent `POST /task` gets 409.
- `GET /metrics` → `{mem_total, mem_available, cached, chromium_rss, tasks_run, tasks_failed}`.
- `GET /logs?since=<seq>` → the ring buffer of structured log lines.
- Faults, from `FLEETKIT_FAULT` (containers: environment; microVMs: `fleetkit.fault=` on the kernel command line, exported by init): `crash_on_start`, `never_ready`, `hang_task`, `hang_step`, `slow_step:<ms>`.

**Task definition.** `task_ms` is measured in the guest with a monotonic clock from receipt of `POST /task` to the settle of `verify_cart`, tab creation included (a caller pays for it). `wall_ms` is measured by the driver from sending `POST /sessions/{id}/task` to receiving the response. Both go in `tasks.csv`; the report's headline uses `task_ms` and reports `wall_ms - task_ms` as harness overhead. Steps, each with its action, settle condition and assertion:

| Step | Action | Settle | Assertion |
|---|---|---|---|
| `home` | new tab; `Page.navigate` to `/` | `Page.loadEventFired` | `input[name=q]` exists |
| `search` | fill `input[name=q]` with `query`, submit the form (navigates to `/search.html?q=`) | `Page.loadEventFired`, then `[data-testid=result]` count > 0 | a result with `data-product-id == product_id` exists |
| `open_product` | click that result (navigates to `/p/<id>.html`) | `Page.loadEventFired` | `h1[data-testid=product-title]` text == `expected_title` |
| `add_to_cart` | click `button[data-testid=add-to-cart]` | DOM condition `[data-testid=added]` visible, awaited with a MutationObserver promise (no polling, no fixed sleeps) | cart badge count == 1 |
| `verify_cart` | `Page.navigate` to `/cart.html` | `Page.loadEventFired` | exactly one `[data-testid=cart-item]`, with `data-product-id == product_id` and its title == `expected_title` |

`bytes_received` = sum of `Network.loadingFinished.encodedDataLength` for the task's tab; `request_count` = count of `Network.requestWillBeSent`. Screenshot: `Page.captureScreenshot` JPEG quality 60, after the timed region, saved by the driver as `screenshots/<task_id>.jpg`. Products come from `fixture/dist/task-products.json`, assigned as `list[slot mod len]`.

**Failure categories (closed set).** `ok`, `step_timeout`, `task_timeout`, `assertion_failed`, `navigation_error`, `browser_crashed`, `guest_unreachable`, `session_not_ready`. `failed_step` names the step for every non-ok category except `guest_unreachable` and `session_not_ready`.

**Session outcomes (closed set).** `completed`, `startup_timeout`, `startup_error`, `lifetime_expired`, `idle_expired`, `task_failure_destroyed`.

**Fault → expected result.**

| Fault | Expected |
|---|---|
| `crash_on_start` | session `failed`, outcome `startup_error`, within seconds |
| `never_ready` | session `failed`, outcome `startup_timeout`, at `ready_timeout_s` |
| `hang_task` (daemon never answers) | task `guest_unreachable` at the proxy deadline; session destroyed with outcome `task_failure_destroyed` |
| `hang_step` (hangs inside a step) | task `step_timeout` with `failed_step`; session stays `ready` |
| `slow_step:<ms>` with ms > step timeout | task `step_timeout` with `failed_step`; session stays `ready` |

**Timeouts (defaults, overridable per trial, recorded in `trial.json`).**

| Parameter | Default |
|---|---|
| `step_timeout_ms` | 10000 |
| `task_timeout_ms` | 45000 |
| host proxy deadline | `task_timeout_ms + 5000` |
| driver client timeout | proxy deadline + 5000 |
| `ready_timeout_s` | 60 local; 90 on AWS for the first n=1 trial, then 3x the observed `startup_ms` |
| `max_lifetime_s` | 600 |
| `idle_timeout_s` | 120 |
| `launch_interval_ms` | 0 (all at once) |

**Host daemon, port 8090.**
- `POST /sessions` `{backend, count, vcpus, mem_mib, ready_timeout_s, max_lifetime_s, idle_timeout_s, launch_interval_ms, fault}` → `[{id, slot, address}]`; returns immediately; readiness is polled by the daemon every 250 ms.
- `GET /sessions`, `GET /sessions/{id}` → `{state, backend, slot, address, created_ts, process_started_ts, ready_ts, destroyed_ts, last_activity_ts, startup_ms, cleanup_ms, outcome, error}`. States: `creating → booting → ready ⇄ busy → destroying → destroyed`, or `failed` (terminal; the daemon still cleans up and sets `destroyed_ts`). `created_ts` is taken when the daemon starts working on the session, before any tap or container; `process_started_ts` when the VMM or container process is launched; `startup_ms = ready_ts - created_ts`, and `process_started_ts - created_ts` is reported as host setup time.
- `POST /sessions/{id}/task` → proxies to the guest with the proxy deadline; 409 with `session_not_ready` if the session is not `ready`; sets `last_activity_ts` at task start and end; the idle reaper skips `busy` sessions (lifetime still applies); a session destroyed while a task is in flight returns `guest_unreachable` with outcome `lifetime_expired`.
- `DELETE /sessions/{id}` → idempotent; `cleanup_ms` = from receipt to every per-session leftover gone (container removed with `docker rm -f`, or Firecracker process killed, tap deleted, scope gone, socket removed). Firecracker is stopped with SIGKILL, not `SendCtrlAltDel`: the guest has a read-only root and tmpfs state, so there is nothing to flush, and the reset would be immediate anyway. The console log's last lines may be lost; that is accepted and documented.
- `GET /host/metrics` → `{ts, mem_total, mem_available, cpu_util, steal, psi: {cpu, memory, io}: {some_avg10, some_total, full_avg10, full_total}, sessions: [{id, rss_bytes, cgroup_memory_current, cgroup_memory_peak, cpu_usage_usec}]}`, sampled every second into gauges; PSI and steal are null where the platform has none.
- `GET /host/verify-clean` → `{clean, leftovers}`; docker: no containers labeled `fleetkit.role=session`; firecracker: no `firecracker` processes, no `fc-*` taps, no `fc-vm*` scopes, nothing under `/run/fleetkit/`. The fixture and observability containers carry other roles and are not counted.
- `GET /health`.
- Every state transition emits a structured log record `session.state {session_id, from, to, ts, outcome}` with trace context.

**Driver CLI.** `driver trial --backend docker|firecracker --n 2,4 --repeats 1 --fixture-check-url http://127.0.0.1:8081 --out results/<run-id>`; `driver report --run results/<run-id> --price-per-hour 0.2117 --instance m8i.xlarge --step-target-ms 1000 --step-p95-ms 2000 [--task-target-ms N]`; `driver smoke --backend ... --out ...`; `driver bundle --run results/<run-id> [--strict]`. The driver writes rows as they arrive; every file below is valid after any interruption.

## 5. One task, end to end

1. **Image build.** `make guest-image` builds `fleetkit-guest:dev` for the local architecture; `make rootfs` (on the host, amd64) exports it and packs `guest.ext4`. Verified by `docker run fleetkit-guest:dev --selftest`, which starts Chromium and loads a bundled page through the task code path, and on the host by booting one VM and calling `/health`.
2. **Provisioning (AWS only).** Stage 0 first (section 10): the experiments stack with a minimal cloud-init, a Run Command probe for `/dev/kvm`, packages, and a Firecracker boot of a known rootfs, then teardown. Only after that: the same stack again, then `make host-setup` over Run Command.
3. **Session creation.** `created_ts`; slot, port or tap; container start, or VM config JSON, scope, `firecracker --config-file --api-sock` with the console on a per-VM log; `process_started_ts`; state `booting`; span `session.create` under the trial span.
4. **Browser readiness.** `/health` polled every 250 ms until `ready_timeout_s`. On 200: `ready`, `ready_ts`, `startup_ms`. The driver's wait-all-ready returns when every session is ready or failed.
5. **Task execution.** A barrier releases N threads at once (or spaced by `launch_interval_ms`); each posts `/task`. The guest runs the five steps, captures the screenshot, answers. The host daemon proxies, emits the guest spans with the clock offset, forwards the log tail, fetches guest `/metrics` and attaches `mem_available` and `chromium_rss` to the task row.
6. **Result collection.** Rows appended to `tasks.csv` and `steps.csv` as each task returns; screenshots saved; `host_metrics.csv` sampled every second throughout.
7. **Cleanup.** `DELETE` for every session; `cleanup_ms` recorded; `verify-clean`; the trial fails if anything is left. `trial.json` written; the bundle updated.

## 6. Startup failure and timeout, walked through

**A guest that never becomes ready.** The daemon polls until `ready_timeout_s`, marks the session `failed` with outcome `startup_timeout`, destroys it exactly like a healthy one, and records `cleanup_ms`. `sessions.csv` shows the outcome, `trial.json` counts it, and the VM's console log is kept. The trial continues with the sessions that did become ready and is marked `degraded`. A process that exits at once (`crash_on_start`) is caught earlier: the daemon sees the container or Firecracker process gone and records `startup_error`.

**A task that hangs.** Inside the guest, `step_timeout_ms` wraps every step and `task_timeout_ms` wraps the task, using the same deadline mechanism as the DevTools waits; on expiry the guest answers `step_timeout` or `task_timeout` with `failed_step` and closes the tab, and the session stays usable. If the guest daemon itself never answers (`hang_task`), the host proxy deadline fires, the answer is `guest_unreachable`, and the daemon destroys the session with outcome `task_failure_destroyed`. The driver's own client timeout sits above the proxy deadline, so it never hangs either.

**Idle and lifetime.** The reaper destroys any session past `max_lifetime_s` since creation, or past `idle_timeout_s` since its last activity while not `busy`, with the matching outcome and a `session.state` record.

## 7. Verification and the smoke suite

| Stage | Check | Evidence |
|---|---|---|
| Guest image | `--selftest` passes | build manifest, selftest log |
| Rootfs (AWS) | one VM boots; `/health` 200 within 90 s; one HTTPS fetch from inside the guest succeeds through NAT | VM console log, hostcheck output |
| Provisioning | `cloud-init status` 0; `fleetkit-hostcheck` green (kvm, kernel 6.18, Firecracker version, bridge, rules, docker, bucket write) | cloud-init logs, hostcheck output |
| Session creation | N sessions `ready`; `startup_ms` per session | sessions.csv, spans |
| Task | every task `ok` with five steps and a screenshot, or a categorized failure | tasks.csv, steps.csv, screenshots |
| Timeouts | the fault table holds | smoke-report.md |
| Cleanup | `verify-clean` after every trial and fault case | smoke-report.md |
| Observability | for the trial's trace id, spans from all three services and `session.state` records for every session | Grafana, `otlp/*.jsonl` |
| Evidence | `driver report` works from the bundle alone; the LGTM snapshot re-mounts | evidence.md |

**`driver smoke`, the assertions, in order.** (1) docker trial n=2 and n=4, repeats 1: every session ready, every task `ok`, zero startup failures. (2) each of the five faults yields the tabulated result within `task_timeout + proxy margin + client margin` of wall clock. (3) a session with `idle_timeout_s=5` is destroyed with `idle_expired`; one with `max_lifetime_s=8` with `lifetime_expired`. (4) `verify-clean` passes after each of the above. (5) `otlp/traces.jsonl` (or the driver's own span file) contains the trial trace with spans from `driver`, `hostd` and `guest-daemon`, and log records with that trace id. Any failed assertion resets the count. "Reliably" means three consecutive green runs, within a three-hour local budget; past the budget the AWS leg starts with the green subset and the flaky case is a known issue.

**A level passes** only if all N sessions reached ready, all N tasks are `ok`, and, once targets exist, step p95 is within target. A degraded trial is reported at its actual concurrency and never counts toward the headline.

## 8. Observability

OpenTelemetry from driver and host daemon over OTLP/HTTP to `grafana/otel-lgtm:0.34.0`, one pinned container (arm64 and amd64) bundling the collector, Tempo, Loki, Prometheus and Grafana with correlation pre-wired. The collector config is a full override copied from the image's default with file exporters appended to every pipeline, writing `/otlp/{traces,metrics,logs}.jsonl` on a fixed bind mount (`results/lgtm/otlp`); `/data` is mounted at `results/lgtm/data`; `stop_grace_period` 60 s; `mem_limit` 2g. At bundle time the driver copies the OTLP lines carrying its `fleetkit.run_id` into `results/<run-id>/otlp/` and, if asked, snapshots `/data` with `compose stop`, tar, `compose start`. The stack is optional (`--no-lgtm`): the driver and host daemon also write their own spans and logs as JSONL directly into the run directory, and OTLP export is best-effort with a two-second timeout, so the bundle is complete without the container.

One trace per trial: the driver opens the trial span, `traceparent` flows to the host daemon over HTTP and to the guest, which echoes it, and the guest spans are created after the fact with explicit start and end times. The guest returns `guest_clock_ns` (realtime) at request receipt plus monotonic step offsets; the host computes `clock_offset_ns = host_send_ts + rtt/2 - guest_clock_ns` and records it on the task row. Correlation keys on every span, log and metric: `fleetkit.run_id`, `trial_id`, `session_id`, `task_id`, `backend`, `host_id`. Structured JSON logs from all three components; driver and host daemon also ship them as OTLP logs with trace context.

## 9. Local versus AWS: what the Mac can and cannot prove

Docker Desktop on Apple Silicon exposes no `/dev/kvm`, so the Docker backend is a substitute, not a microVM. It shares the Linux VM's kernel, runs arm64 Chromium rather than the amd64 build, isolates with cgroups rather than a VMM, addresses sessions by published port rather than a tap, reports the shared VM's PSI rather than per-session pressure (steal is meaningless there), and starts in hundreds of milliseconds rather than a kernel boot. What it proves: the protocol (session states, readiness, start-together, timeouts, failure categories, idle and lifetime enforcement, cleanup), the fixture and the task script, the driver's outputs and report, the observability pipeline, and the guest image build. What it cannot prove: Firecracker boot, the init and read-only rootfs, tap networking and NAT, cgroup accounting, nested-virtualization behavior, and any timing.

Real Firecracker also runs locally: a Lima VM (`vmType: vz`, `nestedVirtualization: true`, Ubuntu 24.04 arm64) on this M3 Pro exposes `/dev/kvm`, and Firecracker v1.17.0 booted the pinned 6.18.48 aarch64 kernel with Firecracker's own Ubuntu rootfs in it, systemd up at 2.6 s. It is aarch64 end to end and known to be slow on page-fault-heavy code under Apple's nested virtualization, so it is functional-only. For this milestone it is the fallback if the AWS host has no usable KVM (time-boxed to 60 minutes, one VM to `/health`); otherwise it is a follow-up. The AWS run remains the only source of Firecracker timings.

## 10. The AWS validation run

**Stage 0, before anything else and in parallel with local work.** Apply `infra/experiments` with `host_count = 1` and a cloud-init that arms the timer and installs the base packages. Over Run Command: `cloud-init status --wait` (bounded), then `images/host/probe.sh`: kernel, `/dev/kvm`, `vmx`, Firecracker version, docker, python, iptables, bridge, package list, then boot Firecracker's own Ubuntu rootfs on the pinned kernel and, separately, a VM on a tap with a static `ip=` and a ping from the host; results copied to the results bucket, which also proves the instance role's write access. Then `host_count = 0`. Stop rules: no `/dev/kvm` after `modprobe kvm_intel` means the AWS Firecracker leg is off tonight (Lima fallback, finding recorded for the human); `RunInstances` refused (`PendingVerification`, SCP deny) is recorded as a human item and local work continues.

**The stack.** Host m8i.xlarge (4 vCPU, 16 GiB, nested virtualization enabled, about $0.21 an hour), AL2023 kernel 6.18 from the lock file, 60 GiB gp3 encrypted, IMDSv2, `instance_initiated_shutdown_behavior = "terminate"`, its own IAM role and instance profile `fleetkit-experiment-host` (SSM core plus put/get/list on the results bucket only; the project stack's profile is SSM-only and is not changed), an egress-only security group in the project VPC, the project's public subnet, tag `Project=fleetkit`, self-shutdown at 4 hours armed first. A private results bucket in the member account. Applied from the Mac with the member-account profile for resources and the management-account profile for state (`backend.hcl`), state key `experiments/terraform.tfstate`.

**Driving the host.** Run Command only; Session Manager needs a plugin the Mac doesn't have. Every host step is a script in the repo run through `AWS-RunShellScript` with an explicit `executionTimeout` and output to the results bucket. The host daemon runs as a root systemd unit installed by `make host-setup`; long driver invocations run as transient units (`systemd-run --unit fleetkit-driver`) and are polled through short commands that read a status file, so no invocation timeout can kill them. Logs go to files the bundle collects.

**Sequence.** Apply; wait for cloud-init (bounded); `make host-setup` (guest image, rootfs, fixture, hostd unit, optional observability) over Run Command; hostcheck; boot one VM to `/health` with a 90-second readiness timeout, at most 45 minutes of fix loops (push, one command); `driver trial --n 1`; then `--n 2,4` with readiness set to 3x the observed startup; `driver smoke --backend firecracker`; bundle and `aws s3 sync` after every stage and from a shell trap on any exit, and from a systemd timer at T+3h45; `host_count = 0`; `describe-instances` shows nothing running; pull the bucket. The Mac-side orchestration runs the teardown apply from an exit trap and never re-applies `host_count = 1` after the single provisioning step; a detached backstop started at apply time terminates any tagged instance still alive at T+4h30 and applies `host_count = 0`. Hard schedule: the main AWS leg starts by 05:30 local whatever the local loop's state; all AWS work stops at 09:30; the CLI session is checked before each stage and its expiry ends the run with a note for the morning.

## 11. Evidence bundle

`results/<run-id>/` always contains: every VM console log and per-session guest log tail, a screenshot for every task that reached the screenshot phase, `sessions.csv`, `tasks.csv`, `steps.csv`, `host_metrics.csv`, `trial.json` per trial, `hostd.log`, `driver.log`, the driver's own `spans.jsonl` and `logs.jsonl`, `otlp/` (when the collector ran), `lgtm-data.tgz` (when snapshotted), `manifest.json` (git commit, guest image base digest and rootfs sha256, kernel sha256, Firecracker version, AMI id, instance type, vCPU quota at run time, run start and end timestamps, timeout parameters), `cloud-init-output.log` and hostcheck output on AWS, and `evidence.md` (what ran, what passed, what is missing). `driver bundle` succeeds on a partial run directory and lists missing items; with `--strict` it exits non-zero if any mandatory item is missing. On AWS the bundle is uploaded to the results bucket under `runs/<run-id>/`.

**Schemas.** `tasks.csv`: run_id, trial_id, backend, level_n, repeat, session_id, slot, task_id, product_id, dispatch_ts, task_ms, wall_ms, ok, failure_category, failed_step, bytes_received, request_count, guest_mem_available, chromium_rss, screenshot_path, trace_id, clock_offset_ns, error. `steps.csv`: run_id, trial_id, task_id, step_index, name, dispatch_ts, settle_ts, duration_ms, error. `sessions.csv`: run_id, trial_id, session_id, slot, backend, vcpus, mem_mib, created_ts, process_started_ts, ready_ts, destroyed_ts, startup_ms, cleanup_ms, outcome, error. `host_metrics.csv` (long): ts, session_id or `host`, metric, value. `trial.json`: ids, level_n, repeat, backend, timeouts used, timestamps of create-start, all-ready, barrier release, last task return, verify-clean pass; counts by outcome and failure category; status `ok | degraded | failed`; p50 and p95 per step and per task; mean bytes and requests per task. Timestamps are Unix seconds as floats; durations in milliseconds.

**Report.** Per level: p50/p95 per step and per task, failure rate by category, targets met, harness overhead, the highest N that passed. Cost: execution-only $/task = price/h x (last task return - barrier release) / tasks ok; observed $/task = price/h x (verify-clean pass - first create) / tasks ok; host provisioning time on its own line, in neither; fixture-serving cost estimated from `request_count` and `bytes_received` at published S3 request pricing (in-region transfer to EC2 is free), labeled an estimate because nginx served the fixture. Every number labeled measured, modeled or assumed.

## 12. Decisions recorded here

1. Guest OS Debian bookworm-slim with Debian's `chromium`; one Dockerfile for both backends; rootfs via `docker export` plus `mkfs.ext4 -d`; no mkosi, debos or buildroot.
2. Shell init plus tini in the guest; no systemd. Chromium launched directly, bypassing the distribution wrapper.
3. Guest kernel 6.18.48 from Firecracker's CI bucket, pinned prefix and sha256 in `images/lock.env`; host kernel 6.18 via a pinned AL2023 AMI in `ami.lock.json`.
4. Firecracker v1.17.0 without the jailer; a systemd scope per VM; no balloon; SIGKILL teardown.
5. Host provisioning by cloud-init on a stock AMI, limited to packages, binaries, networking and the timer; image and service setup by `make` targets over Run Command; no Packer, no Session Manager.
6. Bridge `fcbr0` with per-slot addresses from the kernel command line; NAT with accepts in `DOCKER-USER`.
7. Fixture served by nginx: a compose service locally on `127.0.0.1:8081`, the same image on the experiment host's bridge address for this milestone; a separate fixture host and S3 are follow-ups.
8. Guest daemon in Python with `python3-websockets` from apt; host daemon and driver in Python 3.11-compatible code with the OpenTelemetry SDK; no framework.
9. Observability: OpenTelemetry to `grafana/otel-lgtm:0.34.0`, optional; file exporters and the components' own JSONL as the durable copies; guest telemetry emitted by the host daemon after the fact.
10. Docker backend addresses sessions by published host port on every platform; the network is named `fleetkit`.
11. Validation host m8i.xlarge; VMs 2 vCPU, 2 GiB, `smt: false`, transparent hugepages off on AWS; its own instance profile; terminate on shutdown.
12. Evidence bundle as in section 11, written incrementally, uploaded after every stage.
13. Stage 0 runs first and in parallel with local work; Lima is the fallback for a host without usable KVM.

## 13. Review log (revision 1 → 2)

Accepted from the requirements reviewer: task timing defined two ways; closed enums and the fault table; the schemas; the smoke assertions and level-pass rule; timeout defaults; state-machine rules; `created_ts` before setup and `launch_interval_ms`; the per-step table; bytes, requests and clock offset; one trace per trial and `session.state` records; per-session CPU and guest metrics; expected bytes in the manifest and report targets; cost windows; faults over the kernel command line; mandatory evidence; verify-clean labels; a requirements section in this document; cleanup and screenshot definitions; Lima scoped.

Accepted from the integration reviewer: the results bucket needs its own instance role; resolv.conf via `/run` and DNS in `ip=`; `/dev/shm` created before mounting; no compose package on AL2023; Run Command only with systemd units; `DOCKER-USER` accepts and the discovered egress interface; scopes for cgroups; SIGKILL instead of `SendCtrlAltDel`; terminate-on-shutdown and trap-based teardown; the compose network name and the fixture check URL; Chromium launched directly; the path-style kernel URL; the apt websockets package; the quota rationale corrected.

Accepted from the delivery reviewer: Stage 0 first; the instance role; no Session Manager; the teardown guarantee and the Mac-side backstop; incremental evidence and partial bundles; cloud-init kept minimal with `make` targets over Run Command; Lima as fallback only; the observability stack optional with direct JSONL; the 90-second AWS readiness timeout then 3x observed; init console markers; the hard schedule against the session expiry; the local three-hour budget; the fixture port moved to 8081 and session ports from a fixed range.

Not accepted: none. One clarification: the reviewers' "expected bytes with tolerance" check is a report-time flag, not a task failure category, so a heavier-than-expected page never masquerades as a browser problem.
