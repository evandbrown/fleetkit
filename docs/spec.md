# Specs and campaign definitions

A campaign definition says what to run. A spec says what one run does. This page lists every field, shows how a campaign expands into runs, and says where the results go. The words come from the [method](method.md).

## Definitions

- **Campaign:** a named set of runs launched together to answer one question.
- **Run:** one worker host carrying out one spec, start to finish.
- **Trial:** start N fresh microVMs at the same moment, run one task in each, destroy them. N is the trial's density.
- **MicroVM:** one of the N in a trial.
- **Spec:** everything fixed about one run, including the list of densities to test.
- **Campaign definition:** the question, a base spec, the named specs written as their changes from the base, and the number of replicas.
- **Replicas:** how many runs each spec gets, each on its own worker host.
- **Density:** how many microVMs a trial starts at once. A value, not a unit: the spec lists the densities, and each trial records its own.

## Files

| File | What it is |
|---|---|
| [`experiments/schema/spec.schema.json`](../experiments/schema/spec.schema.json) | JSON Schema (2020-12) for a spec |
| [`experiments/schema/campaign.schema.json`](../experiments/schema/campaign.schema.json) | JSON Schema for a campaign definition |
| [`experiments/schema/instance-types.json`](../experiments/schema/instance-types.json) | Each instance type's vCPUs, memory, whether it's metal, and its price (some estimated) |
| [`experiments/schema/limits.json`](../experiments/schema/limits.json) | The cost limit per campaign, the project cap, the vCPU quota, and the estimate of a run's length |
| [`experiments/schema/chromium-flags.json`](../experiments/schema/chromium-flags.json) | Chromium's base flags, and the extra flags a spec may add, each with why it's allowed |
| [`experiments/schema/expand.py`](../experiments/schema/expand.py) | Expands and checks a campaign, and estimates its cost; standard library only |
| [`experiments/schema/tests/cases.json`](../experiments/schema/tests/cases.json) | Cases that `expand.py` and the site's builder must both pass |
| `experiments/campaigns/<name>.json` | The campaigns to run, one file each |
| `experiments/campaigns/examples/` | Campaigns written but not yet approved to run |

## The spec

Every field is required but two, so a spec never depends on a default; the defaults are what the builder starts a new campaign with. The exceptions were added after the first campaigns ran, so the specs those campaigns recorded stay valid and unchanged: `procedure.release_after_ready_s`, absent 0, and `workload.chromium_extra_flags`, absent none (the whole `workload` section may be left out). A spec that leaves one out runs exactly what one that sets its default runs. **Basic** fields are on the builder's first screen, **advanced** ones behind a disclosure, and **rare** ones in the full spec view.

| Field | What it controls | Default | Tier |
|---|---|---|---|
| `worker_host.instance_type` | The EC2 instance whose capacity the run measures. Its host kind (metal or nested), vCPUs, memory and price follow from it. | `m8i.4xlarge` | basic |
| `hypervisor.name` | The program that runs each microVM: `firecracker` or `cloud-hypervisor`. | `firecracker` | basic |
| `microvm.vcpus` | Virtual CPUs in each microVM, 1 to 8. | 2 | basic |
| `microvm.memory_mib` | Guest memory of each microVM, in MiB, a multiple of 256. | 2048 | basic |
| `densities` | The densities to test, in increasing order, each 1 to 200. Make the steps fine where the host is expected to fail. | `[1, 2, 4, 8, 12, 16]` | basic |
| `criteria.step_p50_target_ms` | For each of the five steps, the median over a trial's tasks must be at most this. | 1000 | basic |
| `criteria.step_p95_target_ms` | For each step, the 95th percentile over a trial's tasks must be at most this. | 2000 | basic |
| `criteria.task_p95_target_ms` | The 95th percentile of whole-task time must be at most this. | 5000 | basic |
| `hypervisor.virtio_transport` | How each microVM's devices reach its guest kernel: `mmio` or `pci`. Cloud Hypervisor only uses `pci`. | `mmio` | advanced |
| `hypervisor.virtio_rng` | Whether each microVM has a random-number device. Cloud Hypervisor always has one. | `false` | advanced |
| `procedure.trials_per_density` | Trials at each density on the way up. | 1 | advanced |
| `procedure.boundary_trials` | Extra trials at the highest passing density and the lowest failing one, once the run stops going up. | 2 | advanced |
| `procedure.release_after_ready_s` | Seconds every trial waits, 0 to 60, once all its microVMs are ready, before it releases their tasks together. 0 is a cold start; a wait measures a warm pool. Task times start at the release, so no task's time includes it. Optional: absent is 0. | 0 | advanced |
| `support_host.instance_type` | The instance that serves the test shopping site and collects telemetry: `m8i.xlarge`, `m8i.2xlarge` or `m8i.4xlarge`. | `m8i.xlarge` | advanced |
| `workload.chromium_extra_flags` | Chromium flags added after the base flags, to compare browser configurations: each from the allowed list below, each once, at most 8. Optional: absent is none. | `[]` | advanced |
| `criteria.ready_timeout_s` | Every microVM must report its browser ready within this many seconds. | 180 | rare |
| `criteria.step_timeout_ms` | A step that takes longer fails its task. | 10000 | rare |
| `criteria.task_timeout_ms` | A task that takes longer fails. | 45000 | rare |
| `procedure.settle_s` | Seconds the worker host sits idle before every trial. | 10 | rare |

A trial also has to meet three criteria every spec includes: every microVM became ready, every task succeeded, and nothing was left on the host.

Checked beyond the schema, each error shown next to its field:

- `densities` are in increasing order, each density once.
- `step_p50_target_ms` ≤ `step_p95_target_ms` < `step_timeout_ms` ≤ `task_timeout_ms`, and `task_p95_target_ms` < `task_timeout_ms`.
- With Cloud Hypervisor, `virtio_transport` is `pci` and `virtio_rng` is `true`.
- Every extra Chromium flag is on the allowed list and takes the value it lists, none is a base flag, and they add up to at most 512 characters ([below](#extra-chromium-flags)).

The same in every run, so not fields: the guest and the browser it boots with its base flags, the five-step shopping task, the test site's catalog, the warm-up and illustration trials, the stop at the first failing density, sampling five times a second on the worker host and in each microVM, each microVM's CPU cap and memory headroom, and the thresholds that name what limited a trial ([method](method.md)). One becomes a field when a campaign needs to vary it.

## Extra Chromium flags

Each microVM's Chromium runs the base flags every run has ([harness design](harness-design.md), section 2), then the spec's `workload.chromium_extra_flags`, then its start page. The flags a spec may add are listed in [`experiments/schema/chromium-flags.json`](../experiments/schema/chromium-flags.json), which `expand.py` and the builder check against, each with why it's allowed. Every allowed flag changes how Chromium spends memory and CPU: its process model, its scheduling, V8's heap and compilers. None changes what a task does: each task still loads every page, image, script and stylesheet and verifies the cart, so the workload check's bytes and requests stay the same, and a difference between specs is Chromium's configuration alone.

| Flag | Why it's allowed |
|---|---|
| `--renderer-process-limit=N`, N from 1 to 32 | Caps how many renderer processes Chromium keeps. Fewer renderers use less memory per microVM, a trade against step time; the pages are the same. |
| `--process-per-site` | One renderer for all pages of a site instead of one per navigation: a process-model choice that changes memory, not the pages. |
| `--disable-site-isolation-trials` | Turns off the site-isolation trials, so frames from different sites may share a renderer: fewer processes, the same pages. The microVM is the security boundary here, as it is for `--no-sandbox`. |
| `--no-zygote` | Starts renderers directly instead of forking them from zygote processes, which saves the zygotes' memory. It needs `--no-sandbox`, which the base flags have. |
| `--disable-extensions` | Turns off the extension system. No extension is installed, so only its startup work and memory go. |
| `--disable-renderer-backgrounding` | Keeps a renderer at normal priority when Chromium thinks its page is in the background: scheduling only. |
| `--disable-background-timer-throttling` | Stops Chromium slowing the timers of pages it thinks are in the background: scheduling only. |
| `--disable-backgrounding-occluded-windows` | Stops Chromium treating a hidden window as backgrounded: scheduling only. |
| `--disable-features=A,B` | Turns off the named features, each from this list: `IsolateOrigins`, `site-per-process` and `SpareRendererForSitePerProcess` (the process model only); `BackForwardCache` (keeps pages the task has left alive for going back, which it never does); `Translate`, `OptimizationHints` and `MediaRouter` (background services, no page content). |
| `--js-flags=--a --b` | V8 options, space-separated, each from this list; every script still runs in full. `--max-old-space-size=N` (16 to 16384 MiB) caps the JavaScript heap; `--max-semi-space-size=N` (1 to 256 MiB) sizes the young generation; `--jitless`, `--lite-mode`, `--optimize-for-size` and `--no-opt` trade script speed for memory and compile work. |

Refused, with the reason the checker gives: any base flag again (`--disable-background-networking` is already one), anything not listed, and these:

- `--blink-settings`: it can switch off images or scripts, so pages would do less of the work the task measures (images are most of a task's bytes).
- `--disable-http-cache`: it changes how many requests a task makes, which the workload check holds the same in every run.
- `--disk-cache-size`: it changes how many requests a task makes, which the workload check holds the same in every run.
- `--proxy-server`: it changes how the guest reaches the test site, which is the same in every run.
- `--proxy-pac-url`: it changes how the guest reaches the test site, which is the same in every run.
- `--host-resolver-rules`: it changes how the guest reaches the test site, which is the same in every run.
- `--disable-web-security`: it changes what pages may do.
- `--enable-features`: it turns on features the base flags don't have; only turning off listed features is allowed.
- `--single-process`: it runs every page inside the browser process, a debugging mode Chromium doesn't support.

Each flag once, at most 8, at most 512 characters together, and a number written without leading zeros (`=2`, never `=02`), so one configuration can't pass as two specs. A run proves its flags: once every microVM is ready, the driver compares what each browser process runs with, as its guest reads it back from `/proc/<pid>/cmdline`, against the base flags plus the spec's plus the start page (`run.json` `harness.chromium_flags`), and records each microVM's in `trial.json` (`microvms[].chromium_flags`). A trial where they differ, or where a guest can't say while the spec adds flags, is a failure outside the experiment: it runs again once, then the density is not tested.

## The campaign definition

```json
{
  "name": "nested-sizes-1",
  "question": "Does a nested m8i.2xlarge reach the same density per host vCPU as a nested m8i.4xlarge?",
  "replicas": 2,
  "shutdown_after_minutes": 45,
  "base": { "...": "a complete spec" },
  "specs": {
    "m8i-4xlarge": {},
    "m8i-2xlarge": { "worker_host": { "instance_type": "m8i.2xlarge" }, "densities": [1, 2, 3, 4, 5, 6, 7, 8] }
  },
  "why": {
    "m8i-4xlarge": "The host cap-baseline-1 ran on, so it anchors the comparison.",
    "m8i-2xlarge": "Half the vCPUs of the same family, to test the per-vCPU scale."
  }
}
```

| Field | What it is | Default | Tier |
|---|---|---|---|
| `name` | The campaign's name and its directory under `results/`: 2 to 40 lowercase letters, digits and single hyphens, starting with a letter. `dev` is reserved for local runs. | | basic |
| `question` | The one question the runs answer together. | | basic |
| `replicas` | Runs per spec, each on its own worker host, 1 to 5. | 2 | basic |
| `why` | Optional. For each spec by name, one sentence on why it's in the campaign, shown beside it. | | basic |
| `shutdown_after_minutes` | Every host shuts itself down this long after it boots, whatever else happens. A run still going then is labelled *stopped early*. | 45 | advanced |
| `base` | A complete spec. It isn't run by itself; a named spec `{}` runs it as it is. | | |
| `specs` | The named specs, 1 to 12, each written as its changes from the base. | | |

A named spec may change `worker_host`, `hypervisor`, `microvm`, `densities` and `workload` (its one field, the extra Chromium flags). The criteria, the procedure and the support host are set once in the base, so every run in a campaign is tested and judged the same way.

## How a campaign expands into runs

1. **Merge.** Each named spec is the base with its changes merged in. Objects merge key by key; lists and values replace, so a spec that sets `densities` gives the whole list.
2. **Check.** The campaign is valid when it matches the schemas, every expanded spec passes the checks above, no two specs expand to the same spec (to run a spec again, raise `replicas`; an optional field left out counts as its default), every `why` names a spec, a run is expected to finish before the shutdown timer, and the worst case is within the limit for one campaign ($75).
3. **Name the runs.** Spec S gets `replicas` runs, `S-r1`, `S-r2` and so on. Run `m8i-2xlarge-r2` of campaign `nested-sizes-1` is `nested-sizes-1/m8i-2xlarge-r2`, and that's also where its results go.
4. **Cost and quota.** The expected cost assumes every run tests every density, at the trial lengths cap-baseline-1 measured. The worst case assumes every worker and support host runs until its shutdown timer. Each run needs its worker's and its support host's vCPUs at once; runs that don't all fit under the vCPU quota go in waves.

```
python3 experiments/schema/expand.py experiments/campaigns/nested-sizes-1.json            # runs, what differs, cost, waves
python3 experiments/schema/expand.py experiments/campaigns/nested-sizes-1.json --json     # the same as JSON
python3 experiments/schema/expand.py experiments/campaigns/nested-sizes-1.json --run m8i-2xlarge-r2   # one run's resolved spec
python3 experiments/schema/expand.py experiments/campaigns/examples/hv-host-1.json --quota 1024      # waves under another quota
```

Exit status 0 means valid, 1 not valid, 2 a usage error. Errors and warnings each start with the field they're about. Warnings don't block a campaign: one replica per spec, microVM memory that adds up to the host's memory, a run too big for today's quota.

The harness must refuse a spec value it can't carry out yet, so a recorded spec is always what ran. The driver refuses extra Chromium flags on a host daemon that doesn't pass them to its guests (its `GET /host/info` has no `chromium_extra_flags`). Today it can't carry out Cloud Hypervisor, Firecracker on `pci` or with a random-number device, or metal worker hosts.

## Where results go

```
results/<campaign>/
  campaign.json                  the campaign definition as launched
  <spec>-r<k>/                   one run, as the driver writes it:
    run.json                     the resolved spec, what was observed about the host and guest, the run's plan and outcome
    trials/<trial>/trial.json    one per trial: d8-t2 is trial 2 at density 8; warmup and illustration are labelled
    microvms.csv                 one row per microVM: created, booted, ready, destroyed
    tasks.csv                    one row per task
    steps.csv                    one row per step: start, end, bytes, requests
    host_metrics.csv             worker host and per-microVM samples, five times a second
    guest_metrics.csv            each microVM's samples of its own processes during its task
    screenshots/                 one per task, plus the illustration trial's filmstrip
    console-logs/<microvm>/      each microVM's serial console
    guest-logs/<microvm>.jsonl   each guest's log
    driver.log                   the run's operational log, including any trial re-run and why
    logs.jsonl  spans.jsonl      the driver's structured logs and traces
    hostd.log  hostd-logs.jsonl  hostd-spans.jsonl    the host daemon's
    cloud-init-output.log  hostcheck-output.txt       the worker host's setup and its check before the run
    manifest.json  evidence.md   what the run contains, and a check that nothing is missing
    report.json  report.md       the run's result, attribution and cost
    otlp/  lgtm-data.tgz         telemetry copies, when the collector ran
    support/                     the support host's metrics and setup log, added by the launcher
```

`run.json`'s `spec` is what `expand.py --run` printed for that run. Until the driver reads specs, it records the driver's options there instead.
