# fleetkit_telemetry

The OpenTelemetry bootstrap shared by the host daemon and the driver (design section 8).
One call gives a process its tracer, meter and logs bridge, its own durable `spans.jsonl`
and `logs.jsonl`, and best-effort OTLP export to the LGTM collector.

```
harness/.venv/bin/pip install -e harness/telemetry     # `make venv` does this
```

## What it guarantees

- **Evidence never depends on the collector.** With `jsonl_dir` set, every span and log
  record is appended synchronously to `spans.jsonl` / `logs.jsonl` in that directory, one
  line each, flushed on write. The lines use the collector's own OTLP-JSON shape (camelCase,
  hex ids, uint64 timestamps as strings), so `results/lgtm/otlp/*.jsonl` and the components'
  files are read and filtered by the same code.
- **OTLP is best-effort.** Traces, logs and metrics go to `<otlp_endpoint>/v1/*` in
  batches with a 2 s timeout per export; a dead collector costs under a second per batch,
  never blocks the caller, and is logged once a minute rather than per batch. `lgtm=False`
  (`--no-lgtm`) disables it entirely.
- **Correlation on everything.** `fleetkit.run_id`, `fleetkit.trial_id`,
  `fleetkit.microvm_id`, `fleetkit.task_id`, `fleetkit.backend`, `fleetkit.host_id` are the
  attribute names (constants in `fleetkit_telemetry.attributes`). They travel as W3C baggage
  next to `traceparent`, and a span processor and a log filter stamp them on every span and
  record; explicit attributes always win.
- **Python 3.11 compatible**, no framework, `http.client`/`urllib` in, `http.server` out.
- **Nothing identifying by default.** `host_id` (`--host-id` / `FLEETKIT_HOST_ID`) feeds every
  resource attribute that names the machine: `fleetkit.host_id`, `host.name` and
  `service.instance.id`. The default is `local`, never `socket.gethostname()`, so a bundle built
  without the flag does not carry the operator's hostname; on AWS pass the instance id.

## Use

```python
import fleetkit_telemetry as ft

parser = argparse.ArgumentParser()
ft.add_telemetry_args(parser)                      # --no-lgtm --otlp-endpoint --otlp-timeout --host-id
                                                   # --host-id sets fleetkit.host_id AND host.name (default: $FLEETKIT_HOST_ID, else local)
args = parser.parse_args()
tel = ft.init_telemetry(ft.config_from_args(args, "driver", jsonl_dir=run_dir, log_file=run_dir / "driver.log"))

# driver: one trace per trial, correlation keys as baggage
with tel.tracer.start_as_current_span("trial") as trial, tel.correlation(run_id=run_id, trial_id=trial_id, backend="docker"):
    headers = tel.inject({"Content-Type": "application/json"})   # traceparent, tracestate, baggage
    ... http.client request with headers ...
    trace_id = format(trial.get_span_context().trace_id, "032x")  # tasks.csv trace_id column

# hostd: extract on the way in, keep correlation per microVM for later spans (reaper, destroy)
with tel.server_span("POST /microvms/{id}/task", request.headers, attributes={"http.request.method": "POST"}):
    microvm.correlation = ft.get_correlation()      # {"fleetkit.run_id": ..., "fleetkit.trial_id": ...}
    with tel.correlation(microvm_id=microvm.id, task_id=task_id):
        out = tel.inject({"Content-Type": "application/json"})  # to the guest; it echoes traceparent
        host_send_ns = time.time_ns(); ... POST /task ...
        emission = tel.emit_guest_task(response, microvm_id=microvm.id, task_id=task_id,
                                       host_send_ns=host_send_ns, rtt_ns=health_rtt_ns)
        tel.emit_guest_logs(response.get("log_tail"), microvm_id=microvm.id, task_id=task_id,
                            clock_offset_ns=emission.clock_offset_ns,
                            trace_id=emission.trace_id, span_id=emission.task_span_id)
        row["clock_offset_ns"] = emission.clock_offset_ns

# later, outside any request
with tel.correlation(**microvm.correlation, microvm_id=microvm.id):
    tel.log_microvm_state(microvm.id, "ready", "destroying", time.time(), outcome="idle_expired")

tel.shutdown()   # flushes everything, bounded by the OTLP timeout
```

`tel.logger` is a stdlib logger named after the service; any logger works, the handlers sit
on the root logger. Records go to stderr as JSON (`ts`, `severity`, `logger`, `service`, `msg`,
`trace_id`, `span_id`, extras, correlation keys), to `log_file` if given, and to OTLP with
trace context. `extra={...}` fields become log attributes.

`tel.meter` is a normal OTel meter; metrics are OTLP-only (`host_metrics.csv` is their
durable copy) and are exported every `metrics_interval_s` (5 s) when LGTM is on.

## Guest spans and the clock offset

The guest has no SDK. `emit_guest_task` takes the `POST /task` response and creates, under
the `guest-daemon` service, a `task` span (kind SERVER, parent = the echoed `traceparent`,
falling back to the current span) and one child span per step, with explicit timestamps:

```
clock_offset_ns = host_send_ns + rtt_ns // 2 - guest_clock_ns        (design section 8)
task_start      = guest_clock_ns + clock_offset_ns                     (host time)
step start/end  = task_start + dispatch_ns / settle_ns                 (offsets since receipt)
task end        = task_start + task_ms, or the last settle when task_ms is absent
```

`rtt_ns` must be a short round trip to the same guest (the readiness `/health` poll or the
`/metrics` fetch), not the task's wall time: `guest_clock_ns` is stamped at request receipt,
so only the one-way latency separates it from `host_send_ns`. If the guest reports absolute
monotonic readings rather than offsets since receipt, pass `dispatch_base_ns`.

Failure: `ok: false` sets ERROR status on `task` and on the span named `failed_step`;
`failure_category`, `failed_step`, `task_ms`, `bytes_received`, `request_count`,
`clock_offset_ns` and `guest_clock_ns` are attributes.

`emit_guest_logs` forwards the `log_tail` as OTLP log records under the same resource: dicts
with `ts|time|timestamp` (s, ms or ns, shifted by the offset), `severity` (or `level`, the
logging-library name for it), `msg|message|body` and any other scalar fields (as
`guest.<key>`), or plain strings.

## microvm.state records

`tel.log_microvm_state(microvm_id, from, to, ts, outcome=None)` emits the record of design
section 4 with `event.name=microvm.state`, `fleetkit.microvm_id`, `microvm_id`, `from`, `to`,
`outcome` and `state_ts` (hostd's own names; the transition time is `state_ts` so it never
collides with the record's own `ts`), the same values namespaced as `microvm.from`,
`microvm.to`, `microvm.ts`, `microvm.outcome`, and the current trace context.

## Reading the files at bundle time

```python
ft.filter_jsonl("results/lgtm/otlp/traces.jsonl", run_dir / "otlp/traces.jsonl", run_id)   # returns line count
ft.filter_jsonl(hostd_dir / "spans.jsonl", run_dir / "otlp/hostd-spans.jsonl", run_id)
for obj in ft.iter_jsonl(path): ...   # a truncated last line is skipped
```

A line "carries" a run id if any resource, scope or record attribute is
`fleetkit.run_id == run_id`; the driver puts it on its resource, the host daemon gets it from
baggage per span and record.

## Tests

```
cd harness/telemetry && ../.venv/bin/python -m pytest -q     # offline; a local stub collector stands in for LGTM
```
