# Capacity experiment: the baseline

**Status:** pre-registered on 2026-09-27, before any capacity data existed. The commit that adds this file comes before the run, and the run records its own commit, so the order is checkable. The values the run reads are in [`experiments/capacity/baseline.env`](../experiments/capacity/baseline.env). Changing any of them after the run makes it a different spec.

## The question

On an m8i.4xlarge running Firecracker under nested virtualization, how many standard shopping tasks can run at once, one per 2 vCPU / 2 GiB microVM, with every task inside the targets? At that density, what does a task cost, and which resource, used by which process, runs out first?

## What the answer will not claim

- **Nothing about bare metal.** Every number describes this nested-virtualization configuration as measured. Nested virtualization adds a layer of VM exits under every guest; we don't extrapolate past it.
- **Not maximum capacity** unless the ladder finds a failing density and the extra trials at the boundary hold. Otherwise the result is "the highest density tested successfully".
- **Not an agent loop.** The driver dispatches a whole task and the guest runs all five steps inside the microVM. No observation goes back to the caller after each step, and there is no think time between steps. The first omission overstates density and the second understates it. Both are follow-ups, one variable at a time.

## Configuration

| Part | Setting |
|---|---|
| Worker host | m8i.4xlarge: 16 vCPUs (8 cores, 2 threads each), 64 GiB, us-east-1, nested virtualization on, Amazon Linux 2023 pinned in `infra/experiments/ami.lock.json` |
| Hypervisor | Firecracker v1.17.0 (`images/lock.env`) |
| Guest | kernel 6.18.48, Debian bookworm-slim, Chromium 154 headless; one read-only root filesystem shared by every microVM, profile on tmpfs |
| MicroVM size | 2 vCPUs, 2048 MiB, SMT off; one cgroup scope per microVM with `CPUQuota=200%` and `MemoryMax` of guest memory plus 256 MiB |
| Network | one tap per microVM on bridge `fcbr0`, NAT through the worker host |
| On the worker host | the host daemon, the driver and the microVMs, nothing else. The driver's own CPU and memory are measured. |
| Support host | m8i.xlarge in the same subnet. The fixture (nginx) is pinned to one CPU, and the observability backend (Grafana LGTM) to the other three. |
| Fixture | the static shopping site from `fixture/`, about 4.74 MB and 126 requests per task (`fixture/dist/manifest.json`) |

## Procedure

One invocation of `driver trial` on one worker host, so one run:

1. **Warm-up.** One trial at density 1, excluded from the results. The first boot after setup reads the root filesystem from disk. Later boots read it from the page cache.
2. **Ladder.** Densities 1, 2, 4, 8, 12 and 16, one trial each. A trial creates N microVMs, waits until all are ready, releases N shopping tasks together, collects the results, and destroys the microVMs. Before every trial the host sits idle for 10 seconds, and its CPU during that wait is recorded.
3. **Stop** at the first density that fails. No higher density runs.
4. **Check the boundary.** Two more trials at the last passing density and two at the first failing density, for three trials at each. If one of the new trials at the last passing density fails, that density becomes a miss and the next lower passing density gets two more trials, and so on down.
5. **Illustration.** One trial at density 1 with a screenshot after every step, for the explorer's filmstrip. The screenshots take time inside the task, so this trial is excluded from the results.

## Pass criteria

A **trial** passes only if all of these hold:

1. All N microVMs report ready within 180 seconds. Startup time is reported but not otherwise judged.
2. All N tasks complete with no failure and no timeout. The step timeout is 10 seconds and the task timeout is 45 seconds.
3. For each of the five steps, the median over the trial's N tasks is at most **1000 ms** and the 95th percentile at most **2000 ms**. These are the product's starting targets.
4. The 95th percentile of task time is at most **5000 ms**. This is the completion target, set here.
5. After cleanup, the host has no leftover microVM process, tap device, cgroup scope or run directory.

A **density** passes only if every trial at it passes. The **headline** is the highest density that passed with every lower density also passing.

**Why 5000 ms for the task.** It's five steps at the 1-second median step target. It's also about four times the uncontended task time measured on the validation host, 1.25 seconds at density 1. So it bounds how much slower a user's whole task may get under load, which the per-step targets alone don't.

**Small samples.** With N tasks in a trial, the 95th percentile is in effect the slowest task for any N up to 20. The criteria are strict on purpose: one slow browser fails the density.

**Workload check, not a criterion.** If a trial's mean bytes per task are more than 15% from the fixture's expected bytes, or its mean requests more than 20% from the expected requests, it is flagged as not having run the standard workload.

## What is measured

- **Per step:** duration, bytes and requests. **Per task:** time on the guest clock, wall time at the driver, and outcome.
- **Per microVM:** startup split into phases (hypervisor start, kernel and init, guest daemon to Chromium launch, Chromium start, detection delay), and cleanup time.
- **Host, five times a second:** CPU use and steal; CPU, memory and IO pressure; available memory. Per microVM: CPU of its virtual-CPU threads, CPU of the hypervisor's own threads, cgroup throttling, cgroup CPU and memory pressure, and memory. The host daemon's and the driver's own CPU and memory.
- **Guest, five times a second during each task:** CPU and memory by process group: Chromium's browser, renderer, GPU, network service, other utility and zygote processes, the guest daemon, and everything else.
- **Around the worker host:** fixture latency from the worker host once a second, and the support host's CPU once a second, to show that the fixture never became the bottleneck.

## How a trial's limit is attributed

These thresholds help read the data. They are not pass criteria. They are fixed here so the verdict isn't chosen after seeing the numbers. Each is computed over the trial's task window, from release to the last task's return:

| Verdict | Triggered when |
|---|---|
| `host_cpu` | host CPU pressure ("some") is at least 20% of the window, or mean CPU use is at least 90% |
| `vm_cpu_quota` | the microVMs' cgroups were throttled for at least 10% of the window on average |
| `host_memory` | available memory fell below 10% of total, or memory pressure is at least 5% |
| `io` | IO pressure is at least 10% |
| `steal` | mean steal is at least 5%: the hypervisor under the worker host took CPU from it |
| `none` | none of the above |

Alongside the verdict, the report names the process group that used the most CPU on the host and inside the guests.

## Cost

The cost per task is modelled from the measured windows and the assumed public price, $0.84672 an hour. It's shown two ways: execution only, from release to the last return, and observed, from the first create to a clean host. The support host, $0.21168 an hour, serves the fixture and the observability backend, and its cost is reported on its own line.

## Deviation from the product requirements

The product requirements serve the fixture from S3. Here nginx serves it from a support host in the same subnet. That keeps the fixture fast, steady and off the worker host, so the result describes the worker host. Serving from S3 is a later one-variable change, and the S3 request cost is still reported as an estimate.

## Follow-ups, each changing one variable and chosen from the evidence

- Per-step observations sent back to the caller, a screenshot and a DOM snapshot after each step.
- Think time between steps.
- A smaller microVM shape, if the verdict is CPU and the microVMs' own quota isn't the limit.
- Extra densities between the last pass and the first miss.
- The fixture on S3.
- Cloud Hypervisor in place of Firecracker.

## Reproduce

From a workstation with the experiments stack initialised and the member account's profile logged in:

```
bash images/host/run-validation.sh <run-id> --plan capacity
```

(Note added 2026-09-27: `run-validation.sh` and `experiments/capacity/baseline.env` were removed once campaigns moved to `experiments/launch.sh`. Both are in the git history at the commit this run recorded.)

It creates the worker host and the support host, sets both up, checks the worker host (KVM, a test microVM's health, the guest's egress through NAT to the fixture, OTLP delivery), runs this procedure, reports and bundles the evidence, syncs the support host's telemetry, and destroys both hosts. The evidence lands in `results/<run-id>/` and in the results bucket.
