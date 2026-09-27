# fleetkit harness

The experiment harness for one browser task, end to end, built to
[docs/harness-design.md](../docs/harness-design.md) (revision 2). That document is the contract;
this file explains how to run the pieces, defines the terms the outputs use, and copies the
schemas so they stay in one place.

## Components

| Component | Path | Runs where | Owns |
|---|---|---|---|
| fixture | `fixture/` (built into `fixture/dist/`) | nginx container, Mac and host | The static shopping site, its manifest (page weights, expected bytes and requests per task) and `task-products.json` |
| guest daemon `guestd` | `harness/guest/`, image in `images/guest/` | inside every session (container locally, microVM on AWS) | Chromium, one task at a time over DevTools, step timings, bytes and requests, a screenshot, its log tail; port 8080 |
| host daemon `hostd` | `harness/host/` | one per host, port 8090 | Session lifecycle for the `docker` and `firecracker` backends, readiness, task proxy, lifetime and idle enforcement, destroy, verify-clean, host metrics, the telemetry hub |
| driver | `harness/driver/` | where the operator runs it (Mac; a transient systemd unit on EC2) | The trial protocol, incremental CSV/JSON outputs, the report, the smoke suite, the evidence bundle |
| telemetry | `harness/telemetry/` | library used by hostd (and available to the driver) | OpenTelemetry bootstrap, JSONL exporters, correlation keys |
| observability | `observability/` | `grafana/otel-lgtm` container, optional | Traces, logs, metrics; a durable OTLP-JSON copy under `results/lgtm/otlp/` |

Each component has its own tests, runnable offline (`make test`). The driver's tests use a stub
host daemon and a stub fixture (`harness/driver/tests/`), so the driver is proven without Docker.

## Running locally (macOS, Docker Desktop)

The `docker` backend is the development backend: it proves the protocol, the fixture and task
script, the driver's outputs, the observability pipeline and the guest image build. It proves no
timing and nothing about Firecracker (design section 9). Real numbers come only from the
`firecracker` backend on AWS.

```sh
make venv                     # harness/.venv with the telemetry, host, driver and guest (test) requirements
make test                     # offline pytest suites: telemetry, host, driver, guest
make guest-image              # fleetkit-guest:dev for the local architecture
make fixture                  # fixture/dist/ (site, manifest.json, task-products.json)
make up                       # observability + fixture on http://127.0.0.1:8081 (LGTM=0: fixture only)
make hostd                    # host daemon in the foreground on :8090, logs under results/hostd/
```

Then, in another terminal (`harness/.venv/bin/python` has everything on its path when run through
`make`; otherwise `cd harness/driver` first):

```sh
python3 -m driver trial  --backend docker --n 2,4 --repeats 1 \
    --fixture-check-url http://127.0.0.1:8081 --out results/<run-id>
python3 -m driver report --run results/<run-id> --price-per-hour 0.2117 --instance m8i.xlarge \
    --step-target-ms 1000 --step-p95-ms 2000 [--task-target-ms N]
python3 -m driver smoke  --backend docker --out results/<run-id>          # or: make smoke
python3 -m driver bundle --run results/<run-id> [--strict]
make down
```

Without the observability stack, pass `--no-lgtm` to the driver (and `LGTM=0` to `make up` /
`make hostd`): both components still write their own `spans.jsonl` and `logs.jsonl`, so the bundle
is complete without the collector. OTLP export is best-effort with a two-second timeout either way.

`host_id` is the correlation key on every span, log record and CSV row. It defaults to
`$FLEETKIT_HOST_ID`, else `local`, never the machine's hostname (which can carry the operator's
name into a bundle that will be shared). Give both components the same value when it matters:
`make hostd HOSTD_ARGS="--host-id i-0123"` and `driver trial ... --host-id i-0123` (the EC2
instance id on AWS), or export `FLEETKIT_HOST_ID` once for both.

### Offline, without Docker at all

The driver runs against its stub host daemon, which implements the hostd API with a fake session
model, the five faults, both reapers and verify-clean:

```sh
cd harness/driver
../.venv/bin/python -m tests.stub_hostd   --port 8090 --telemetry-dir ../../results/hostd &
../.venv/bin/python -m tests.stub_fixture --port 8081 &
../.venv/bin/python -m driver smoke --backend docker --out ../../results/stub-smoke --no-lgtm
../.venv/bin/python -m pytest              # the same, as tests, in about a minute
```

## Running on the host (AWS, Firecracker)

The sequence is design section 10; only the driver-facing steps are repeated here. Every host
step runs through SSM Run Command; long driver invocations run as transient systemd units
(`systemd-run --unit fleetkit-driver ...`) and are polled by reading their status files, so no
Run Command timeout can kill them.

1. Stage 0 (probe), then the experiments stack, then `make host-setup` over Run Command: guest
   image, rootfs, fixture on the bridge address, the hostd systemd unit, optional observability.
2. Boot one VM to `/health` with a 90-second readiness timeout.
3. `driver trial --backend firecracker --n 1 --ready-timeout-s 90 --fixture-check-url http://10.200.0.1:8081 --out results/<run-id>`
4. `driver trial --backend firecracker --n 2,4 --ready-timeout-s <3 x observed startup_ms, in seconds> --out results/<run-id>`
   (the same run directory: rows append, trial ids keep counting).
5. `driver smoke --backend firecracker --out results/<run-id>`
6. `driver bundle --run results/<run-id> --aws --hostd-dir results/hostd --cloud-init-log /var/log/cloud-init-output.log --hostcheck-output <path> --instance-type m8i.xlarge --vcpu-quota <n> --strict`
   after every stage and from the shell trap on any exit, then `aws s3 sync results/<run-id> s3://<results bucket>/runs/<run-id>/`.

On the firecracker backend the guest reaches the fixture at `http://10.200.0.1:8081` (the driver's
default `--fixture-base-url` for that backend); on docker it is `http://fixture`. The driver checks
`--fixture-check-url` from where it runs, before creating sessions and again just before the
barrier.

## The driver

`python3 -m driver {trial,report,smoke,bundle}`; `--help` on each lists every option. The driver is
standard-library Python (3.11+), talks to hostd over HTTP with `traceparent`, `X-Fleetkit-Run-Id`,
`X-Fleetkit-Trial-Id` and `X-Fleetkit-Task-Id` headers, and writes rows as they arrive: every file in
the run directory is valid after any interruption. Ctrl-C or SIGTERM during a trial still destroys
the trial's sessions and runs verify-clean before exiting (130); hostd's lifetime and idle reapers
back that up.

### `trial`

For each N in `--n` and each repeat:

1. `fixture_check`: GET `--fixture-check-url`, load the product list (`--products`, else
   `<fixture-check-url>/task-products.json`, else `fixture/dist/task-products.json`).
2. `create`: `POST /sessions` with the backend, count, shape and the timeouts; `create_start` is
   taken just before.
3. `wait_ready`: `GET /sessions/{id}` every 250 ms until every session is `ready` or terminal;
   hostd enforces `ready_timeout_s`, the driver waits at most 30 s longer for a terminal state.
   `all_ready` is taken when the loop ends.
4. `fixture_recheck`, then `tasks`: one thread per ready session waits at a barrier;
   `barrier_release` is taken as it opens. With `launch_interval_ms > 0` thread *i* starts
   *i* x interval later. Each posts `/sessions/{id}/task` with the driver client timeout
   (`task_timeout_ms + 5000 + 5000`), appends its `tasks.csv` and `steps.csv` rows, saves
   `screenshots/<task_id>.jpg` and `guest-logs/<session_id>.jsonl`, then reads the session state
   once more (the fault table's "session stays ready").
5. `cleanup`: `DELETE /sessions/{id}` for every session in parallel, poll until destroyed,
   append `sessions.csv` rows with hostd's `startup_ms`, `cleanup_ms`, `outcome`.
6. `verify_clean`: `GET /host/verify-clean`; `verify_clean_pass` is taken only when it is clean.
7. `trial.json` written (it is also rewritten at every phase change, with `complete: false`).

Products are assigned `list[slot mod len]`. Sessions that never became ready get no task; the
trial runs at its actual concurrency and is marked `degraded`. A 1 Hz sampler appends
`GET /host/metrics` to `host_metrics.csv` for the whole invocation.

Exit code 0 when every trial is `ok`, 1 otherwise, 3 when hostd is unreachable, 130 on interruption.

### `report`

Reads only the run directory, so it works from the bundle alone. Writes `report.md` and
`report.json`. Per level (backend, N; repeats aggregated) and per trial: p50/p95 per step and per
task, failure rate by category, targets met, harness overhead, startup and cleanup percentiles,
cost per task. Fault trials are listed separately and never form a level. The headline per backend
is the highest N that passed; the report calls it "highest N tested successfully" and never
maximum capacity.

Targets: `--step-target-ms` applies to every step's p50, `--step-p95-ms` to every step's p95,
`--task-target-ms` to task p95. A level passes only if every provided target is met on top of the
protocol rule below. `--fixture-manifest fixture/dist/manifest.json` adds the expected-bytes flag
(the manifest's own `tolerance.bytes_pct` / `requests_pct` when present, else `--bytes-tolerance`,
default 25%); it is a report-time flag, never a failure category.

### `smoke`

Runs the assertions of design section 7 in order and writes `smoke-report.md` (pass/fail per
assertion with the evidence) and `smoke-state.json` (the consecutive-green counter, reset by any
failed assertion, and the cumulative time against the three-hour local budget).

| # | assertion | evidence |
|---|---|---|
| 1a, 1b | docker trials n=2 and n=4, repeats 1: every session ready, every task `ok`, zero startup failures | `trial.json` counts |
| 2a-2e | each fault (`crash_on_start`, `never_ready`, `hang_task`, `hang_step`, `slow_step:<2 x step_timeout>`) yields the tabulated result; task faults within `task_timeout + 5000 + 5000` ms of wall clock; `never_ready` at `ready_timeout_s` (10 s for this case, `--never-ready-timeout-s`); `crash_on_start` within `--crash-bound-s` (10 s) | the fault trial's `trial.json` (`sessions[0].outcome`, `.task.failure_category`, `.task.failed_step`, `.state_after_task`, `.task.wall_ms`) |
| 3a, 3b | a session with `idle_timeout_s=5` is destroyed with `idle_expired`; one with `max_lifetime_s=8` with `lifetime_expired` | `trials/<id>/case.json`, `sessions.csv` |
| 4 | verify-clean passed after each of the above | every trial's `verify_clean`, plus a direct call after each reaper case |
| 5 | the n=2 trial's trace has spans from `driver`, `hostd` and `guest-daemon`, and log records with that trace id; every session of the n=2 and n=4 trials has a `session.state` record under its trial's trace id (design section 7, observability row) | `otlp/traces.jsonl` and `otlp/logs.jsonl` (copied from `results/lgtm/otlp/` when present), the driver's `spans.jsonl`/`logs.jsonl`, hostd's `spans.jsonl`/`logs.jsonl` from `--hostd-dir` (default `results/hostd`) |

`--until-green 3` repeats until three consecutive green runs or the budget (`--budget-s`,
default 3 h) is spent. Exit code 0 when the run (or the target count) is green, 1 otherwise.
The timeouts can be lowered for a quick loop (`--task-timeout-ms`, `--step-timeout-ms`,
`--ready-timeout-s`, `--idle-case-s`, `--lifetime-case-s`); the values used are printed in the
report and recorded in every `trial.json`.

### `bundle`

Assembles `results/<run-id>/` into the evidence bundle: copies hostd's `hostd.log`, `spans.jsonl`,
`logs.jsonl` and `sessions/<id>/console.log` (`--hostd-dir`, default `results/hostd`), copies the
OTLP lines carrying this run's `fleetkit.run_id` from `results/lgtm/otlp/*.jsonl` into `otlp/`
(`--lgtm-dir`, default `results/lgtm`), optionally snapshots the LGTM data directory
(`--snapshot-lgtm`: compose stop, tar, compose start), writes `manifest.json` and `evidence.md`, and
lists what is missing. It succeeds on a partial run directory; `--strict` exits 1 if any mandatory
item is missing.

Mandatory: `sessions.csv`, `tasks.csv`, `steps.csv`, `host_metrics.csv`, at least one
`trials/*/trial.json`, `driver.log`, `spans.jsonl`, `logs.jsonl`, `manifest.json`, `evidence.md`,
`hostd.log`, a screenshot for every task whose row names one, `console-logs/<session_id>/console.log`
for every session in `sessions.csv` (hostd writes it on both backends), and
`guest-logs/<session_id>.jsonl` for every session whose guest answered a task (every `tasks.csv`
row except `guest_unreachable` and `session_not_ready`); the missing session ids are named in
`evidence.md`. With `--aws`: `cloud-init-output.log` and `hostcheck-output.txt`. When the
collector ran, `otlp/`; when snapshotted, `lgtm-data.tgz`. Everything else (`hostd-spans.jsonl`,
`report.md`, `smoke-report.md`) is listed as present or absent.

`manifest.json`: `git_commit`, `guest_image_base_digest` (from `--guest-manifest`, the
`guest-<arch>.manifest.json` that `images/guest/build-rootfs.sh` writes, else `images/lock.env`),
`rootfs_sha256`, `rootfs_size_bytes`, `guest_image_id`, `guest_arch` and `guest_chromium_version`
(all from `--guest-manifest`), `kernel_version` and `kernel_sha256`
(lock file), `firecracker_version`, `ami_id` (`--ami-lock infra/experiments/ami.lock.json` or
`--ami-id`), `instance_type`, `vcpu_quota`, `host_provisioning_s`, `run_start_ts`, `run_end_ts`,
`timeout_parameters`, the trial list. Operator-supplied values survive later bundles that do not
repeat them.

## Definitions

- **task_ms** (measured, guest): monotonic clock in the guest from receipt of `POST /task` to the
  settle of `verify_cart`, tab creation included. The report's headline timing.
- **wall_ms** (measured, driver): from sending `POST /sessions/{id}/task` to receiving the
  response. **Harness overhead** = `wall_ms - task_ms`, reported per level.
- Timing percentiles (`task_ms`, `wall_ms`, harness overhead) are over ok tasks only. A failed
  task's `task_ms` is the guest's elapsed time to the failure (about `step_timeout_ms` for a
  `step_timeout`), reported separately as `failed_task_elapsed_ms`; counts and failure rates cover
  every task.
- **startup_ms** = `ready_ts - created_ts` (hostd); `process_started_ts - created_ts` is the host
  setup time. **cleanup_ms** = from receipt of `DELETE` to every per-session leftover gone.
- **Trial** = one concurrency level N, one repeat: create N, wait all ready, barrier, N tasks,
  destroy all, verify-clean. **Trial status**: `ok` (all N ready, all N tasks ok, clean);
  `degraded` (the protocol completed but some session failed startup or some task failed, reported
  at its actual concurrency); `failed` (fixture check failed, hostd error, no session ready,
  verify-clean found leftovers, or interrupted).
- **A level passes** only if all N sessions reached ready, all N tasks are `ok`, verify-clean
  passed, and, once targets exist, every provided target is met (step p50 and p95, task p95).
  A degraded trial never counts toward the headline.
- **Headline**: the highest N actually tested successfully per backend. Never maximum capacity.
- **Cost**: execution-only $/task = price/h x (last task return - barrier release) / tasks ok;
  observed $/task = price/h x (verify-clean pass - first create) / tasks ok; host provisioning time
  on its own line, in neither; fixture-serving cost estimated from `request_count` at published S3
  request pricing (`--s3-get-price-per-1000`, default $0.0004; in-region transfer to EC2 is free),
  labeled an estimate because nginx served the fixture. Prices are **assumed**, timings and counts
  **measured**, cost per task **modeled**.
- **Timestamps** are Unix seconds as floats; **durations** are milliseconds. Percentiles use
  linear interpolation between closest ranks. Step timestamps in `steps.csv` are the guest's
  monotonic offsets converted to host time with `clock_offset_ns`
  (`host_send_ts + rtt/2 - guest_clock_ns`, computed by hostd and attached to the task reply; the
  driver computes it itself only when the reply lacks it).

**Timeouts** (defaults; overridable per trial; recorded in `trial.json`):

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

**Failure categories** (closed set): `ok`, `step_timeout`, `task_timeout`, `assertion_failed`,
`navigation_error`, `browser_crashed`, `guest_unreachable`, `session_not_ready`. `failed_step`
names the step for every non-ok category except `guest_unreachable` and `session_not_ready`. The
driver maps a transport error or client timeout to `guest_unreachable`, HTTP 409 without a category
to `session_not_ready`, and never invents a category outside the set.

**Session outcomes** (closed set): `completed`, `startup_timeout`, `startup_error`,
`lifetime_expired`, `idle_expired`, `task_failure_destroyed`. **States**:
`creating -> booting -> ready <-> busy -> destroying -> destroyed`, or `failed` (terminal; still
cleaned up, `destroyed_ts` set).

**Fault -> expected result**:

| Fault | Expected |
|---|---|
| `crash_on_start` | session `failed`, outcome `startup_error`, within seconds |
| `never_ready` | session `failed`, outcome `startup_timeout`, at `ready_timeout_s` |
| `hang_task` | task `guest_unreachable` at the proxy deadline; session destroyed with `task_failure_destroyed` |
| `hang_step` | task `step_timeout` with `failed_step`; session stays `ready` |
| `slow_step:<ms>`, ms > step timeout | task `step_timeout` with `failed_step`; session stays `ready` |

## Run directory and schemas

`results/<run-id>/` (design section 11):

```
tasks.csv  steps.csv  sessions.csv  host_metrics.csv     appended row by row, one header
trials/<trial_id>/trial.json                              rewritten at every phase; case.json for smoke reaper cases
screenshots/<task_id>.jpg                                 JPEG q60 from the guest, after the timed region
guest-logs/<session_id>.jsonl                             the guest daemon's log tail, per task
console-logs/<session_id>/console.log                     VM console logs, copied by bundle from hostd
driver.log  spans.jsonl  logs.jsonl  run.json             the driver's own text log, spans and structured logs
hostd.log  hostd-spans.jsonl  hostd-logs.jsonl            copied by bundle from results/hostd
otlp/{traces,metrics,logs}.jsonl                          this run's lines from results/lgtm/otlp (when the collector ran)
lgtm-data.tgz                                             when snapshotted
manifest.json  evidence.md  report.md  report.json  smoke-report.md  smoke-state.json
```

Trial ids are `t<seq>-<backend>-n<N>-r<repeat>` (`t<seq>-smoke-n<N>`, `t<seq>-fault-<name>`,
`t<seq>-case-<idle|lifetime>` from the smoke suite); task ids are `<trial_id>-slot<slot>`.

**`tasks.csv`**: `run_id, trial_id, backend, level_n, repeat, session_id, slot, task_id, product_id,
dispatch_ts, task_ms, wall_ms, ok, failure_category, failed_step, bytes_received, request_count,
guest_mem_available, chromium_rss, screenshot_path, trace_id, clock_offset_ns, error`

**`steps.csv`**: `run_id, trial_id, task_id, step_index, name, dispatch_ts, settle_ts, duration_ms,
error` (`step_index` is 0-based in dispatch order; `error` is set on the failed step only). The
guest reports only the steps that completed, so for a failed task the driver adds one row for
`failed_step` itself: `dispatch_ts` is the previous step's `settle_ts` (or the task's dispatch),
`settle_ts` and `duration_ms` are empty, `error` is the task's error. Percentiles ignore rows
without a `duration_ms`.

**`sessions.csv`**: `run_id, trial_id, session_id, slot, backend, vcpus, mem_mib, created_ts,
process_started_ts, ready_ts, destroyed_ts, startup_ms, cleanup_ms, outcome, error`

**`host_metrics.csv`** (long): `ts, session_id or host, metric, value`. Host metrics:
`mem_total, mem_available, cpu_util, steal, psi_<cpu|memory|io>_<some_avg10|some_total|full_avg10|full_total>`;
per session: `rss_bytes, cgroup_memory_current, cgroup_memory_peak, cpu_usage_usec`. Nulls (PSI and
steal where the platform has none) are not written.

**`trial.json`**: ids (`run_id`, `trial_id`, `trace_id`, `host_id`), `level_n`, `repeat`, `backend`,
`fault`, `vcpus`, `mem_mib`, `timeouts` used (including the derived proxy deadline and client
timeout), `fixture` (check URL, guest base URL, products source), `timestamps` (`create_start`,
`all_ready`, `barrier_release`, `last_task_return`, `verify_clean_pass`), `counts` (sessions
requested/created/ready/failed at startup, `sessions_by_outcome`, tasks dispatched/ok,
`tasks_by_failure_category`), `actual_concurrency`, `status` (`ok | degraded | failed`), `complete`,
`phase`, `level_passed`, `error`, `verify_clean`, `percentiles` (per step, `task_ms`, `wall_ms`,
harness overhead, `startup_ms`, `cleanup_ms`; p50 and p95; `task_ms`, `wall_ms` and harness
overhead cover ok tasks only, and `failed_task_elapsed_ms` summarizes the guest's elapsed time to
failure for the others, which is not a task duration), `means` (`bytes_received`,
`request_count`), and `sessions` (per session: state, outcome, timings, `state_after_task`, its task
summary).

**`spans.jsonl` / `logs.jsonl`** (driver, hostd, and the collector's copies): one OTLP-JSON
`ExportTraceServiceRequest` / `ExportLogsServiceRequest` per line holding one span or one log
record, camelCase, hex ids, uint64 timestamps as strings. `driver/evidence.py` reads that shape and
a flat `{"service", "trace_id", "span_id", "name", ...}` record per line. Correlation attributes on
every span and record: `fleetkit.run_id`, `fleetkit.trial_id`, `fleetkit.session_id`,
`fleetkit.task_id`, `fleetkit.backend`, `fleetkit.host_id`; `service.name` is `driver`, `hostd` or
`guest-daemon`.

## Host daemon API the driver relies on

Design section 4, port 8090. `POST /sessions` returns `[{id, slot, address}]` immediately;
`GET /sessions/{id}` returns `{state, backend, slot, address, created_ts, process_started_ts,
ready_ts, destroyed_ts, last_activity_ts, startup_ms, cleanup_ms, outcome, error}`;
`POST /sessions/{id}/task` returns the guest's reply with `clock_offset_ns`, `guest_mem_available`
and `chromium_rss` added (a 409 body carries `failure_category: session_not_ready`; a proxy
deadline carries `guest_unreachable`); `DELETE /sessions/{id}` is idempotent;
`GET /host/metrics` and `GET /host/verify-clean` as in the design. hostd writes `hostd.log`,
`spans.jsonl`, `logs.jsonl` and `sessions/<id>/console.log` under its `--log-dir`
(`results/hostd`), which is where `driver smoke` and `driver bundle` look by default.
