# fleetkit harness

The experiment harness for one browser task, end to end, built to
[docs/harness-design.md](../docs/harness-design.md) (revision 2). That document is the contract;
this file explains how to run the pieces, defines the terms the outputs use, and copies the
schemas so they stay in one place. The words (run, trial, density, microVM, spec) are the
glossary in [docs/method.md](../docs/method.md).

## Components

| Component | Path | Runs where | Owns |
|---|---|---|---|
| fixture | `fixture/` (built into `fixture/dist/`) | nginx container, Mac and host | The static shopping site, its manifest (page weights, expected bytes and requests per task) and `task-products.json` |
| guest daemon `guestd` | `harness/guest/`, image in `images/guest/` | inside every microVM (a container stands in for one locally) | Chromium, one task at a time over DevTools, step timings, bytes and requests, a screenshot, its log tail; port 8080 |
| host daemon `hostd` | `harness/host/` | one per host, port 8090 | MicroVM lifecycle for the `docker` and `firecracker` backends, readiness, task proxy, lifetime and idle enforcement, destroy, verify-clean, host metrics, the telemetry hub |
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
python3 -m driver trial  --backend docker --densities 2,4 --trials-per-density 1 \
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

The driver runs against its stub host daemon, which implements the hostd API with a fake microVM
model, the five faults, both reapers and verify-clean:

```sh
cd harness/driver
../.venv/bin/python -m tests.stub_hostd   --port 8090 --telemetry-dir ../../results/hostd &
../.venv/bin/python -m tests.stub_fixture --port 8081 &
../.venv/bin/python -m driver smoke --backend docker --out ../../results/stub-smoke --no-lgtm
../.venv/bin/python -m pytest              # the same, as tests, in about a minute
```

## Running on the host (AWS, Firecracker)

A campaign runs on AWS from one command, `experiments/launch.sh experiments/campaigns/<name>.json`
(`experiments/launcher/launch.py` describes every check and step). For each run it creates a worker
host and a support host, then over SSM Run Command: `images/host/setup.sh` (venv, fixture, guest
image, rootfs, the hostd systemd unit), `images/host/hostcheck.sh`, `images/host/stage.sh run`
(`driver trial --spec ...` as a transient systemd unit, so no Run Command timeout can kill it) and
`images/host/stage.sh bundle` (`driver report` and `driver bundle`, synced to the results bucket).
Then it destroys both hosts.

On the firecracker backend the guest reaches the fixture at `http://10.200.0.1:8081` (the driver's
default `--fixture-base-url` for that backend); on docker it is `http://fixture`. The driver checks
`--fixture-check-url` from where it runs, before creating microVMs and again just before the
barrier.

## The driver

`python3 -m driver {trial,report,smoke,bundle}`; `--help` on each lists every option. The driver is
standard-library Python (3.11+), talks to hostd over HTTP with `traceparent`, `X-Fleetkit-Run-Id`,
`X-Fleetkit-Trial-Id` and `X-Fleetkit-Task-Id` headers, and writes rows as they arrive: every file in
the run directory is valid after any interruption. Ctrl-C or SIGTERM during a trial still destroys
the trial's microVMs and runs verify-clean before exiting (130); hostd's lifetime and idle reapers
back that up.

### `trial`

For each density in `--densities`, and for each trial at it (`--trials-per-density`, default 1):

1. `fixture_check`: GET `--fixture-check-url`, load the product list (`--products`, else
   `<fixture-check-url>/task-products.json`, else `fixture/dist/task-products.json`).
2. `create`: `POST /microvms` with the backend, the count (the density), the shape and the
   timeouts; `create_start` is taken just before.
3. `wait_ready`: `GET /microvms/{id}` every 250 ms until every microVM is `ready` or terminal;
   hostd enforces `ready_timeout_s`, the driver waits at most 30 s longer for a terminal state.
   `all_ready` is taken when the loop ends.
4. `fixture_recheck`, then `tasks`: one thread per ready microVM waits at a barrier;
   `barrier_release` is taken as it opens. With `launch_interval_ms > 0` thread *i* starts
   *i* x interval later. Each posts `/microvms/{id}/task` with the driver client timeout
   (`task_timeout_ms + 5000 + 5000`), appends its `tasks.csv` and `steps.csv` rows, saves
   `screenshots/<task_id>.jpg` and `guest-logs/<microvm_id>.jsonl`, then reads the microVM's state
   once more (the fault table's "microVM stays ready").
5. `cleanup`: `DELETE /microvms/{id}` for every microVM in parallel, poll until destroyed,
   append `microvms.csv` rows with hostd's `startup_ms`, `cleanup_ms`, `outcome`.
6. `verify_clean`: `GET /host/verify-clean`; `verify_clean_pass` is taken only when it is clean.
7. `trial.json` written (it is also rewritten at every phase change, with `complete: false`).

Products are assigned `list[slot mod len]`. MicroVMs that never became ready get no task; the
trial dispatches fewer tasks than its density (`counts.tasks_dispatched`) and is marked
`degraded`. A sampler appends `GET /host/metrics` to `host_metrics.csv` for the whole invocation,
at `--metrics-hz` (default 1).

A capacity run adds the procedure in [docs/method.md](../docs/method.md): `--warmup K` labelled
trials at density 1 first, `--settle-s` of idle before every trial, `--stop-at-first-miss` to end
the ladder at the first failing density, `--boundary-trials K` more trials at the last passing and
the first failing density (walking down if the last pass fails one), and `--illustration` for one
labelled trial at density 1 with a screenshot after every step. The criteria
(`--step-p50-target-ms`, `--step-p95-target-ms`, `--task-p95-target-ms`) are judged per trial.

`run.json` holds the run's `spec` (its fixed inputs: the command line, the options, the criteria,
the densities and the procedure), what it `observed` about the worker host and the guest
(`host_info` from `GET /host/info`, `guest_info` from the first ready guest), and the `plan`:
`densities`, `trials_per_density`, `boundary_trials`, `densities_run[]` (`density`, `trials`,
`ladder_trial_count`, `boundary_trial_count`, `ladder_passed`, `passed`), `densities_not_run`,
`boundary_checks[]` (`density`, `trials`, `passed`), `boundary` (`last_pass`, `first_miss`,
`last_pass_trials`, `first_miss_trials`) and `stop_reason`.

Exit code 0 when every trial is `ok`, 1 otherwise, 3 when hostd is unreachable, 130 on interruption.

### `report`

Reads only the run directory, so it works from the bundle alone. Writes `report.md` and
`report.json`. Per density (`densities[]`: backend and density, its trials pooled) and per trial
(`trials[]`): p50/p95 per step and per task, failure rate by category, targets met, harness
overhead, startup and cleanup percentiles, cost per task. Warm-up, illustration and fault trials
are listed separately (`excluded_trials[]`) and never pooled into a density. The headline per
backend, `highest_density_tested_successfully`, is the highest density that passed with every
lower density passing; the report says "tested successfully" and never maximum capacity.

Targets: `--step-target-ms` applies to every step's p50, `--step-p95-ms` to every step's p95,
`--task-target-ms` to task p95. A trial passes only if every provided target is met on top of the
protocol rule below, and a density passes only if every trial at it passed.
`--fixture-manifest fixture/dist/manifest.json` adds the expected-bytes flag (the manifest's own
`tolerance.bytes_pct` / `requests_pct` when present, else `--bytes-tolerance`, default 25%); it is
a report-time flag, never a failure category.

### `smoke`

Runs the assertions of design section 7 in order and writes `smoke-report.md` (pass/fail per
assertion with the evidence) and `smoke-state.json` (the consecutive-green counter, reset by any
failed assertion, and the cumulative time against the three-hour local budget).

| # | assertion | evidence |
|---|---|---|
| 1a, 1b | docker trials at densities 2 and 4 (`--densities`, default 2,4), one each: every microVM ready, every task `ok`, zero startup failures | `trial.json` counts |
| 2a-2e | each fault (`crash_on_start`, `never_ready`, `hang_task`, `hang_step`, `slow_step:<2 x step_timeout>`) yields the tabulated result; task faults within `task_timeout + 5000 + 5000` ms of wall clock; `never_ready` at `ready_timeout_s` (10 s for this case, `--never-ready-timeout-s`); `crash_on_start` within `--crash-bound-s` (10 s) | the fault trial's `trial.json` (`microvms[0].outcome`, `.task.failure_category`, `.task.failed_step`, `.state_after_task`, `.task.wall_ms`) |
| 3a, 3b | a microVM with `idle_timeout_s=5` is destroyed with `idle_expired`; one with `max_lifetime_s=8` with `lifetime_expired` | `trials/<id>/case.json`, `microvms.csv` |
| 4 | verify-clean passed after each of the above | every trial's `verify_clean`, plus a direct call after each reaper case |
| 5 | the density 2 trial's trace has spans from `driver`, `hostd` and `guest-daemon`, and log records with that trace id; every microVM of the density 2 and density 4 trials has a `microvm.state` record under its trial's trace id (design section 7, observability row) | `otlp/traces.jsonl` and `otlp/logs.jsonl` (copied from `results/lgtm/otlp/` when present), the driver's `spans.jsonl`/`logs.jsonl`, hostd's `spans.jsonl`/`logs.jsonl` from `--hostd-dir` (default `results/hostd`) |

`--until-green 3` runs the suite round after round until three consecutive rounds are green, the
budget (`--budget-s`, default 3 h) is spent, or `--max-rounds` (default 10) have run;
`smoke-state.json` lists them under `rounds[]`. Exit code 0 when the round (or the target count)
is green, 1 otherwise.
The timeouts can be lowered for a quick loop (`--task-timeout-ms`, `--step-timeout-ms`,
`--ready-timeout-s`, `--idle-case-s`, `--lifetime-case-s`); the values used are printed in the
report and recorded in every `trial.json`.

### `bundle`

Assembles `results/<run-id>/` into the evidence bundle: copies hostd's `hostd.log`, `spans.jsonl`,
`logs.jsonl` and `microvms/<id>/console.log` (`--hostd-dir`, default `results/hostd`), copies the
OTLP lines carrying this run's `fleetkit.run_id` from `results/lgtm/otlp/*.jsonl` into `otlp/`
(`--lgtm-dir`, default `results/lgtm`), optionally snapshots the LGTM data directory
(`--snapshot-lgtm`: compose stop, tar, compose start), writes `manifest.json` and `evidence.md`, and
lists what is missing. It succeeds on a partial run directory; `--strict` exits 1 if any mandatory
item is missing.

Mandatory: `microvms.csv`, `tasks.csv`, `steps.csv`, `host_metrics.csv`, at least one
`trials/*/trial.json`, `driver.log`, `spans.jsonl`, `logs.jsonl`, `manifest.json`, `evidence.md`,
`hostd.log`, a screenshot for every task whose row names one, `console-logs/<microvm_id>/console.log`
for every microVM in `microvms.csv` (hostd writes it on both backends), and
`guest-logs/<microvm_id>.jsonl` for every microVM whose guest answered a task (every `tasks.csv`
row except `guest_unreachable` and `microvm_not_ready`); the missing microVM ids are named in
`evidence.md`. With `--aws`: `cloud-init-output.log` and `hostcheck-output.txt`. When the
collector ran, `otlp/`; when snapshotted, `lgtm-data.tgz`. Everything else (`hostd-spans.jsonl`,
`report.md`, `smoke-report.md`) is listed as present or absent.

`manifest.json`: `git_commit`, `guest_image_base_digest` (from `--guest-manifest`, the
`guest-<arch>.manifest.json` that `images/guest/build-rootfs.sh` writes, else `images/lock.env`),
`rootfs_sha256`, `rootfs_size_bytes`, `guest_image_id`, `guest_arch` and `guest_chromium_version`
(all from `--guest-manifest`), `kernel_version` and `kernel_sha256`
(lock file), `firecracker_version`, `ami_id` (`--ami-lock infra/experiments/ami.lock.json` or
`--ami-id`), `instance_type`, `vcpu_quota`, `host_provisioning_s`, `run_start_ts`, `run_end_ts`,
`timeout_parameters`, and the trial list (`trials[]`: `trial_id`, `backend`, `density`,
`trial_number`, `trial_kind`, `fault`, `status`, `passed`). Operator-supplied values survive later
bundles that omit them.

## Definitions

- **task_ms** (measured, guest): monotonic clock in the guest from receipt of `POST /task` to the
  settle of `verify_cart`, tab creation included. The report's headline timing.
- **wall_ms** (measured, driver): from sending `POST /microvms/{id}/task` to receiving the
  response. **Harness overhead** = `wall_ms - task_ms`, reported per density.
- Timing percentiles (`task_ms`, `wall_ms`, harness overhead) are over ok tasks only. A failed
  task's `task_ms` is the guest's elapsed time to the failure (about `step_timeout_ms` for a
  `step_timeout`), reported separately as `failed_task_elapsed_ms`; counts and failure rates cover
  every task.
- **startup_ms** = `ready_ts - created_ts` (hostd); `process_started_ts - created_ts` is the host
  setup time. **cleanup_ms** = from receipt of `DELETE` to every per-microVM leftover gone.
- **Trial** = start N fresh microVMs at the same moment, run one task in each, destroy them:
  create N, wait all ready, barrier, N tasks, destroy all, verify-clean. N is the trial's
  **density**. **Trial status**: `ok` (all N ready, all N tasks ok, clean); `degraded` (the
  protocol completed but some microVM failed startup or some task failed); `failed` (fixture check
  failed, hostd error, no microVM ready, verify-clean found leftovers, or interrupted).
- **A trial passes** (`passed`) only if all N microVMs reached ready, all N tasks are `ok`,
  verify-clean passed, and, once targets exist, every provided target is met (step p50 and p95,
  task p95). A degraded trial never passes. **A density passes** only if every trial at it passed.
- **Headline**: the highest density tested successfully per backend, with every lower density
  passing too. Never maximum capacity.
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
| `ready_timeout_s` | 60 local; 90 on AWS for the first trial at density 1, then 3x the observed `startup_ms` |
| `max_lifetime_s` | 600 |
| `idle_timeout_s` | 120 |
| `launch_interval_ms` | 0 (all at once) |

**Failure categories** (closed set): `ok`, `step_timeout`, `task_timeout`, `assertion_failed`,
`navigation_error`, `browser_crashed`, `guest_unreachable`, `microvm_not_ready`. `failed_step`
names the step for every non-ok category except `guest_unreachable` and `microvm_not_ready`. The
driver maps a transport error or client timeout to `guest_unreachable`, HTTP 409 without a category
to `microvm_not_ready`, and never invents a category outside the set.

**MicroVM outcomes** (closed set): `completed`, `startup_timeout`, `startup_error`,
`lifetime_expired`, `idle_expired`, `task_failure_destroyed`. **States**:
`creating -> booting -> ready <-> busy -> destroying -> destroyed`, or `failed` (terminal; still
cleaned up, `destroyed_ts` set).

**Fault -> expected result**:

| Fault | Expected |
|---|---|
| `crash_on_start` | microVM `failed`, outcome `startup_error`, within seconds |
| `never_ready` | microVM `failed`, outcome `startup_timeout`, at `ready_timeout_s` |
| `hang_task` | task `guest_unreachable` at the proxy deadline; microVM destroyed with `task_failure_destroyed` |
| `hang_step` | task `step_timeout` with `failed_step`; microVM stays `ready` |
| `slow_step:<ms>`, ms > step timeout | task `step_timeout` with `failed_step`; microVM stays `ready` |

## Run directory and schemas

`results/<run-id>/` (design section 11):

```
tasks.csv  steps.csv  microvms.csv  host_metrics.csv     appended row by row, one header
guest_metrics.csv                                         appended row by row, one header
trials/<trial_id>/trial.json                              rewritten at every phase; case.json for smoke reaper cases
screenshots/<task_id>.jpg                                 JPEG q60 from the guest, after the timed region
guest-logs/<microvm_id>.jsonl                             the guest daemon's log tail, per task
console-logs/<microvm_id>/console.log                     VM console logs, copied by bundle from hostd
driver.log  spans.jsonl  logs.jsonl                       the driver's own text log, spans and structured logs
run.json                                                  the run's spec, what it observed, and the plan
hostd.log  hostd-spans.jsonl  hostd-logs.jsonl            copied by bundle from results/hostd
otlp/{traces,metrics,logs}.jsonl                          this run's lines from results/lgtm/otlp (when the collector ran)
lgtm-data.tgz                                             when snapshotted
manifest.json  evidence.md  report.md  report.json  smoke-report.md  smoke-state.json
```

Trial ids read the way the method speaks: `d<density>-t<trial_number>`, so `d8-t2` is trial 2 at
density 8. Trials are numbered from 1 within their density, across ladder and boundary trials (and
the smoke suite's density trials). Labelled trials are not numbered: `warmup`, `illustration`,
`fault-<name>` and `case-<idle|lifetime>`; a label used again in the same run directory gets `-2`,
`-3` and so on. Every trial also records `sequence`, its place in the run's execution order from 1,
and readers sort trials by it. Task ids are `<trial_id>-slot<NNN>`, for example `d8-t2-slot003`.
Every trial has a `trial_kind`: `ladder`, `boundary`, `warmup`, `illustration`, `smoke` or `fault`.
Run directories written before these names are read through `driver/legacy.py`, which maps every
old file, column and field name to the current one.

**`tasks.csv`**: `run_id, trial_id, backend, density, trial_number, microvm_id, slot, task_id,
product_id, dispatch_ts, task_ms, wall_ms, ok, failure_category, failed_step, bytes_received,
request_count, guest_mem_available, chromium_rss, screenshot_path, trace_id, clock_offset_ns, error,
timing_valid, guestd_cpu_ms, trial_kind` (`trial_number` is empty for labelled trials;
`timing_valid` is false for the illustration trial, whose step screenshots fall inside the task)

**`steps.csv`**: `run_id, trial_id, task_id, step_index, name, dispatch_ts, settle_ts, duration_ms,
error, bytes_received, request_count` (the traffic from this step's dispatch to the next step's; the
steps of an ok task sum to the task's totals; `step_index` is 0-based in dispatch order; `error` is set on the failed step only). The
guest reports only the steps that completed, so for a failed task the driver adds one row for
`failed_step` itself: `dispatch_ts` is the previous step's `settle_ts` (or the task's dispatch),
`settle_ts` and `duration_ms` are empty, `error` is the task's error. Percentiles ignore rows
without a `duration_ms`.

**`microvms.csv`**: `run_id, trial_id, microvm_id, slot, backend, vcpus, mem_mib, created_ts,
process_started_ts, ready_ts, destroyed_ts, startup_ms, cleanup_ms, outcome, error, kernel_start_ts,
guestd_start_ts, chromium_launch_ts, chromium_ready_ts` (the boot phases, on the host clock, derived
from the uptimes the guest reports in its first ready `/health`)

**`host_metrics.csv`** (long): `ts, subject, metric, value`, where `subject` is `host`, `driver`,
`fixture` or a microVM id. Host metrics:
`mem_total, mem_available, cpu_util, steal, psi_<cpu|memory|io>_<some_avg10|some_total|full_avg10|full_total>`;
`cpu_count, hostd_cpu_usec, hostd_rss_bytes`; per microVM: `rss_bytes, cgroup_memory_current,
cgroup_memory_peak, cpu_usage_usec`, and on Firecracker `cpu_vcpu_usec, cpu_hypervisor_usec` (the
microVM's vCPU threads and the hypervisor's other threads), `cpu_throttled_usec, cpu_nr_throttled,
cpu_pressure_some_total_us, cpu_pressure_full_total_us, memory_pressure_some_total_us` (the
microVM's cgroup); subject `driver`: `driver_cpu_usec, driver_rss_bytes`; subject `fixture`:
`fixture_rtt_ms` (1 Hz). Counters are cumulative. Nulls (PSI and steal where the platform has none)
are not written. Sampled at `--metrics-hz` (the host daemon's `--metrics-period` must match).

**`guest_metrics.csv`** (long): `ts, trial_id, microvm_id, task_id, metric, value`, sampled inside the
guest every `--sample-interval-ms` during each task, `ts` on the host clock. Metrics: `cpu_total_ms`,
`cpu_idle_ms` (since the previous sample), `mem_available`, `psi_cpu_some_total_us`, and per process
group (`browser, renderer, gpu, network, utility, zygote, chromium_other, guestd, other`)
`cpu_ms.<group>`, `rss_bytes.<group>`, `procs.<group>`.

**`trial.json`**: ids (`run_id`, `trial_id`, `trace_id`, `host_id`), `density`, `trial_number` (null
for labelled trials), `sequence`, `trial_kind`, `backend`, `fault`, `vcpus`, `mem_mib`, `timeouts`
used (including the derived proxy deadline and client timeout), `fixture` (check URL, guest base
URL, products source), `timestamps` (`create_start`, `all_ready`, `barrier_release`,
`last_task_return`, `verify_clean_pass`), `counts` (`microvms_requested`, `microvms_created`,
`microvms_ready`, `microvms_failed_startup`, `microvms_by_outcome`, `tasks_dispatched`, `tasks_ok`,
`tasks_by_failure_category`), `status` (`ok | degraded | failed`), `complete`, `phase`, `passed`
(the trial's verdict, equal to `evaluation.passed`), `criteria`, `evaluation`, `error`, `verify_clean`,
`percentiles` (per step, `task_ms`, `wall_ms`, harness overhead, `startup_ms`, `cleanup_ms`; p50 and
p95; `task_ms`, `wall_ms` and harness overhead cover ok tasks only, and `failed_task_elapsed_ms`
summarizes the guest's elapsed time to failure for the others, which is not a task duration; each
carries its sample `count`), `means` (`bytes_received`, `request_count`), and `microvms` (per
microVM: `microvm_id`, state, outcome, timings, `state_after_task`, its task summary).

**`spans.jsonl` / `logs.jsonl`** (driver, hostd, and the collector's copies): one OTLP-JSON
`ExportTraceServiceRequest` / `ExportLogsServiceRequest` per line holding one span or one log
record, camelCase, hex ids, uint64 timestamps as strings. `driver/evidence.py` reads that shape and
a flat `{"service", "trace_id", "span_id", "name", ...}` record per line. Correlation attributes on
every span and record: `fleetkit.run_id`, `fleetkit.trial_id`, `fleetkit.microvm_id`,
`fleetkit.task_id`, `fleetkit.backend`, `fleetkit.host_id`. The driver's trial span also carries
`fleetkit.density`, `fleetkit.trial_number` and `fleetkit.trial_kind`, and its trial log records
`fleetkit.density`. `service.name` is `driver`, `hostd` or `guest-daemon`.

## Host daemon API the driver relies on

Design section 4, port 8090. `POST /microvms` returns `[{id, slot, address}]` immediately;
`GET /microvms/{id}` returns `{state, backend, slot, address, created_ts, process_started_ts,
ready_ts, destroyed_ts, last_activity_ts, startup_ms, cleanup_ms, outcome, error}`;
`POST /microvms/{id}/task` returns the guest's reply with `clock_offset_ns`, `guest_mem_available`
and `chromium_rss` added (a 409 body carries `failure_category: microvm_not_ready`; a proxy
deadline carries `guest_unreachable`); `DELETE /microvms/{id}` is idempotent;
`GET /host/metrics` and `GET /host/verify-clean` as in the design. hostd writes `hostd.log`,
`spans.jsonl`, `logs.jsonl` and `microvms/<id>/console.log` under its `--log-dir`
(`results/hostd`), which is where `driver smoke` and `driver bundle` look by default.
