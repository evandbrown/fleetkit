# Observability stack and fixture server

One pinned `grafana/otel-lgtm:0.34.0` container (collector, Tempo, Loki, Prometheus,
Grafana, correlation pre-wired) and one nginx container serving `fixture/dist`, on a Docker
network named exactly `fleetkit` (design sections 3 and 8). The stack is optional: the driver
and host daemon write their own `spans.jsonl` and `logs.jsonl` and export to it best-effort
with a two-second timeout (`--no-lgtm` on the driver, `--no-otlp` on the host daemon).

```
make up                 # both services, waits for health;   make up LGTM=0   fixture only
make lgtm-check         # one span in -> results/lgtm/otlp/traces.jsonl, Grafana /api/health
make down               # stop; results/lgtm/ is kept
make clean-lgtm         # stop and delete results/lgtm/
```

| Service | Container | Published | Mounts |
|---|---|---|---|
| `lgtm` | `fleetkit-lgtm` (label `fleetkit.role=observability`) | `127.0.0.1:3000` Grafana, `127.0.0.1:4317` OTLP/gRPC, `127.0.0.1:4318` OTLP/HTTP | `results/lgtm/data:/data` (all backends' state), `results/lgtm/otlp:/otlp` (file exporters), `otelcol-config.yaml:/otel-lgtm/otelcol-config.yaml:ro` |
| `fixture` | `fleetkit-fixture` (label `fleetkit.role=fixture`) | `127.0.0.1:8081` (`FIXTURE_BIND=10.200.0.1` on the AWS host, the bridge address) | `fixture/dist:/usr/share/nginx/html:ro` |

`mem_limit: 2g` and `stop_grace_period: 60s` on the LGTM container; its health is the
`/tmp/ready` file the image writes once every component answers. Grafana is anonymous
admin (the image default); the ports are bound to loopback. Session containers on the
`fleetkit` network reach the fixture as `http://fixture`; the host daemon and driver on the
host use `127.0.0.1`.

## The collector override

The image starts `otelcol-contrib` with `--config=file:./otelcol-config.yaml` from
`/otel-lgtm`, so mounting a file at that path read-only replaces the configuration.
`otelcol-config.yaml` is a full override: a verbatim copy of `docker/otelcol-config.yaml` at
tag `v0.34.0` of `grafana/docker-otel-lgtm` (the file in the image is byte-identical; checked
with `docker cp`), plus file exporters so a durable OTLP-JSON copy of what the collector
receives lands in `results/lgtm/otlp/{traces,metrics,logs}.jsonl`:

```diff
 exporters:
   ...
+  file/traces:   { path: /otlp/traces.jsonl,  format: json, append: true, flush_interval: 1s }
+  file/metrics:  { path: /otlp/metrics.jsonl, format: json, append: true, flush_interval: 1s }
+  file/logs:     { path: /otlp/logs.jsonl,    format: json, append: true, flush_interval: 1s }
 service:
   pipelines:
     traces:
-      exporters: [otlp_http/traces]
+      exporters: [otlp_http/traces, file/traces]
     metrics:
       receivers: [otlp, prometheus/collector]
       exporters: [otlp_http/metrics]            # unchanged
+    metrics/file:
+      receivers: [otlp]
+      processors: [batch]
+      exporters: [file/metrics]
     logs:
-      exporters: [otlp_http/logs]
+      exporters: [otlp_http/logs, file/logs]
```

Two choices to know about:

- **Metrics get their own pipeline instead of an appended exporter.** The default metrics
  pipeline also scrapes the collector's own metrics every second. With `file/metrics`
  appended there, `metrics.jsonl` grew by 15.6 KB/s (56 MB an hour, 30 collector-internal
  metrics per line), none of it harness data or ever copied into a bundle. `metrics/file`
  is fed by the `otlp` receiver only, so the file holds exactly what the driver and host
  daemon sent (verified: a gauge from the SDK lands, self-scrape lines do not).
- **`append: true`, no rotation.** A collector restart mid-run never truncates evidence.
  The files grow for the life of the stack; `make clean-lgtm` removes them.

### Deviation from the design

The design (docs/harness-design.md, section 8) says file exporters are appended to every
pipeline; that does not hold for metrics, whose file copy comes from a separate `metrics/file`
pipeline fed by the `otlp` receiver only, because appending to the default metrics pipeline
would also write the collector's once-a-second self-scrape (56 MB an hour that no bundle keeps).

`make lgtm-check` validates the whole path with the standard library only
(`observability/check.py`): POST one OTLP-JSON span to `:4318/v1/traces`, wait for it in
`traces.jsonl`, `GET :3000/api/health`. The override itself can be validated without
starting anything:

```
docker run --rm -v "$PWD/observability/otelcol-config.yaml:/cfg.yaml:ro" \
  --entrypoint /otel-lgtm/otelcol-contrib/otelcol-contrib "$LGTM_IMAGE" \
  validate --feature-gates service.profilesSupport --config=file:/cfg.yaml
```

## File format and bundling

Each line of `results/lgtm/otlp/*.jsonl` is one `Export{Trace,Metrics,Logs}ServiceRequest`
in OTLP-JSON (camelCase, hex `traceId`/`spanId`, uint64 nanosecond timestamps as strings).
The collector batches, so one line can hold spans from several requests. The components'
own `spans.jsonl`/`logs.jsonl` written by `fleetkit_telemetry` use the same encoding with one
record per line, and `fleetkit_telemetry.filter_jsonl(src, dst, run_id)` copies the lines
carrying `fleetkit.run_id == run_id` from either kind of file into `results/<run-id>/otlp/`
at bundle time (a collector line is copied whole when any record in it matches).

Snapshotting `/data` for the bundle, as the design describes:

```
docker compose -f observability/compose.yaml stop lgtm       # honours the 60 s grace period
tar -C results/lgtm -czf results/<run-id>/lgtm-data.tgz data
docker compose -f observability/compose.yaml start lgtm
```

Re-mount: extract into an empty `results/lgtm/data` and `make up`.

## On AWS: the support host

On the Mac and in the validation run the stack runs next to the host daemon, as above. In the
capacity run (`bash images/host/run-validation.sh <run-id> --plan capacity`) it does not run on
the worker at all: it runs on a separate support host (`aws_instance.support` in
`infra/experiments`, provisioned by `images/support/cloud-config.yaml`) together with the
fixture, so neither competes with the microVMs for the worker's CPU. There both containers are
started with plain `docker run` by cloud-init: `fleetkit-fixture` on `0.0.0.0:8081` pinned to
CPU 0, `fleetkit-lgtm` pinned to the remaining CPUs with OTLP on `0.0.0.0:4317/4318` and
Grafana on `127.0.0.1:3000` only, the same collector override, and state under
`/var/lib/fleetkit/lgtm/{data,otlp}`. The security group admits only the experiment hosts
(their guests arrive NATed from the host). The worker's host daemon and driver export to
`http://<support-ip>:4318`; `images/support/sync.sh` stops `fleetkit-lgtm` with a 60 s grace at
the end of the run and uploads `otlp/` and the support host's own 1 Hz metrics
(`support-metrics.csv`) to `runs/<run-id>/support/` in the results bucket.

## Hosts without docker compose

AL2023 ships no compose package. `observability/run-plain.sh up|down [--no-lgtm]` starts the
same two containers with plain `docker run` (same names, labels, ports, mounts, network and
pins, read from `images/lock.env` when present); the Makefile falls back to it automatically
when `docker compose version` fails.

## Seeing it in Grafana

Explore, data source Tempo: search by service `driver`, `hostd` or `guest-daemon`, or paste
the `trace_id` from `tasks.csv`. The trace of a trial shows the driver's `trial` span, the
host daemon's server spans, and the guest's `task` span with its five step children,
re-timed with the clock offset. Loki: `{service_name="hostd"} |= "session.state"` lists the
state transitions; every record carries `fleetkit.run_id`, `trial_id`, `session_id`,
`task_id`, `backend` and `host_id` as attributes.
