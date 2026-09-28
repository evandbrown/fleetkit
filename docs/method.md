# How we run capacity experiments

This page defines the words we use and the procedure every campaign, run and trial follows. Specs, campaign definitions, pre-registrations, reports, the explorer and the code use these words and no others. When a term here changes, everything else changes with it.

## The question

How many browsers can one worker host run at the same time, each doing a real shopping task, with every task inside its latency targets? At that density, what does a task cost, and what ran out first: which resource, used by which process?

Every browser runs in its own microVM. That isolation is a requirement of the product, not something a campaign varies.

## Glossary

### The setup

| Term | Meaning |
|---|---|
| **Worker host** | The EC2 instance whose capacity is measured. |
| **Host kind** | *Metal*: an EC2 bare-metal instance, where microVMs run directly on the hardware. *Nested*: an ordinary EC2 instance, where microVMs run inside it through nested virtualization. It follows from the instance type, so a spec never states it. |
| **Hypervisor** | The program on the worker host that runs each microVM: Firecracker or Cloud Hypervisor. |
| **Support host** | A separate instance that serves the test shopping site and collects telemetry, so neither competes with the microVMs for the worker host's CPU. |

### Units

There are four units, from biggest to smallest. Each contains the next, and there are no others.

| Unit | Meaning |
|---|---|
| **Campaign** | A named set of runs launched together to answer one question. |
| **Run** | One worker host carrying out one spec, start to finish. |
| **Trial** | Start N fresh microVMs at the same moment, run one task in each, destroy them. N is the trial's density. |
| **MicroVM** | One of the N in a trial. It runs one headless Chromium, and that browser runs one shopping task of five steps. |

Trials at the same density run one after another, never overlapping, and are numbered from 1 within their density: "trial 2 at density 8". The warm-up and illustration trials are labelled as such and not numbered.

Every trial at a density has identical inputs, down to which product each microVM shops for. Only what the run doesn't control differs: fresh microVMs, when the trial ran, and the host's state at that moment.

### Inputs

| Input | Meaning |
|---|---|
| **Spec** | Everything fixed about one run, including the list of densities to test. It covers the worker host, the hypervisor and the microVM's devices, the microVM's size, the densities, the pass criteria, the procedure and the support host; [spec.md](spec.md) lists every field. What is the same in every run, such as the guest, the task, and the sampling and attribution rules, is set by this page and the harness, not by the spec. |
| **Campaign definition** | What the experiment builder produces: the question, a base spec, the named specs written as their changes from the base, the number of replicas, and optionally one sentence per spec on why it's there. |
| **Replicas** | How many runs each spec gets, each on its own worker host. |

Two kinds of value are never inputs:

- **Values derivable from other inputs.** The host kind follows from the instance type.
- **Measured facts.** The worker host's CPU model and the guest kernel's digest are examples. A run records them beside its spec, as what it observed.

### Measures

| Measure | Meaning |
|---|---|
| **Density** | How many microVMs a trial starts at once. It is a value, not a unit: the spec lists the densities to test, and each trial records its own. Nothing sits between a run and its trials; a run's trials are grouped by density only to judge them. |
| **Pass** | A trial passes if it meets every criterion in its spec. A density passes if every trial at it passed. |
| **Result** | A run's result is the highest density that passed with every lower density passing too. It is stated as "tested successfully", never as a maximum, because the densities between the last pass and the first miss were not tried. |
| **Density per host vCPU** | A run's result divided by the worker host's vCPUs. It puts hosts of different sizes on one scale. |
| **Cost per 1,000 tasks** | What 1,000 tasks cost at a run's result, from the worker host's hourly price and the time the tasks took. It puts hosts at different prices on one scale. |

Extra trials at one density show how much the same test varies on the same host. Replicas show how much it varies from host to host. A difference between two specs means more the larger it is than both, so a comparison always shows every replica (see Compare, below).

### Words we don't use

| Don't say | Say |
|---|---|
| level | density: "density 8", "trial 2 at density 8" |
| repeat | trial 2 at density 8 |
| factor, condition | the specs in this campaign, or the input that differs |
| session, for a microVM | microVM |
| VMM | hypervisor |
| experiment, as a unit | campaign, run or trial. The experiment builder is the tool that writes campaign definitions. |

## The procedure

### 1. Write the campaign definition and commit it first

The campaign definition names everything its runs will do: every spec, with its pass criteria and the rules for reading the result, and how many replicas each spec gets. It is committed before the first run starts, and each run records the commit it ran from. Anyone can then check that the criteria weren't chosen after the data arrived.

### 2. One run on one worker host

1. **Warm-up.** One trial at density 1, labelled *warm-up*, not numbered and excluded from the result. The first microVM after setup reads its root filesystem from disk; later ones read it from memory.
2. **The ladder.** The spec's densities in increasing order, for example 1, 2, 4, 8, 12, 16. Each gets trial 1, and more if the spec asks for more trials per density.
3. **Stop at the first failing density.** No higher density runs.
4. **Check the boundary.** The spec's boundary trials run at the last passing density and again at the first failing density. With two boundary trials and one ladder trial, each of the two densities ends with trials 1 to 3. If a new trial at the last passing density fails, that density becomes a failing density, the next lower passing density gets the boundary trials, and so on down.
5. **Illustration.** One trial at density 1, labelled *illustration* and not numbered, that takes a screenshot after every step for the filmstrip. The screenshots take time inside the task, so this trial is excluded from the result.

### 3. One trial

1. The worker host sits idle for a fixed period, and its CPU during that time is recorded.
2. N microVMs are started together, where N is the trial's density. The trial waits until every one reports its browser ready.
3. All N shopping tasks are released at the same moment. Each task has five steps: open the home page, search, open a product, add it to the cart, and verify the cart. By default the release comes as soon as the last microVM is ready, while browsers may still be finishing startup (a cold start). A spec's wait after ready (`procedure.release_after_ready_s`, up to 60 s) holds the ready microVMs idle that long first, to measure a warm pool; task times start at the release, so none includes the wait.
4. When every task has returned, all N microVMs are destroyed, and the host is checked for anything left behind.

### 4. Judge

A **trial passes** only if it meets every criterion in its spec. The criteria are:

- all N microVMs became ready within the time limit
- all N tasks succeeded
- every step met its latency targets over the trial's N tasks
- the whole task met its latency target
- nothing was left on the host

A **density passes** only if every trial at it passed.

A **run's result** is the highest density that passed with every lower density also passing, stated as "tested successfully".

**A failure outside the experiment is not a result.** When a trial fails because of something the spec doesn't test, such as an overloaded support host, a harness error or an AWS problem, the harness runs it again once. If it still can't get a clean trial, that density is *not tested* and the run goes no higher. The cause goes in the run's operational log (`driver.log`), never in the results.

**Missing data is labelled where it would be,** with no other result states: *not tested* for a density the run didn't reach or couldn't test cleanly; *warm-up, not judged* and *illustration, not judged* for the labelled trials; *stopped early* for a run that ended before its procedure finished, for example when its shutdown timer fired.

### 5. Record

Every run records:

- **Spec:** the spec it carried out.
- **Observed facts:** what was measured about the worker host and the guest, such as the CPU model, the core count and the kernel. They sit beside the spec and are never part of it.
- **Per trial:** its density and its number within that density, or its label. The trial id reads the same way: `d8-t2` is trial 2 at density 8, and `warmup` and `illustration` are the labelled trials. Each trial also records its place in the run's order, because trials at different densities interleave once the boundary is checked.
- **Per microVM:** when it was created, when its kernel, guest and browser started, when it became ready, and when it was destroyed.
- **Per step:** start, end, bytes and requests.
- **Samples:** the worker host's CPU, pressure and memory, five times a second, split by microVM and by the hypervisor's own threads. Inside each microVM, CPU and memory by process type during its task.
- **Screenshots:** one per task, plus the illustration trial's filmstrip.

### 6. Attribute

Each trial gets a verdict on which resource limited it. The verdict is computed by rules whose thresholds are fixed in the harness, the same for every run, from what was measured while the tasks ran:

- host CPU
- the microVM's own CPU allowance
- host memory
- disk and network IO
- CPU taken by the hypervisor under a nested worker host
- none of these

Beside the verdict, the report names the process that used the resource: on the worker host, each microVM's virtual CPUs against the hypervisor's own work; inside a microVM, Chromium's renderer against its other processes.

### 7. Compare

A campaign runs several specs at once. Specs in a campaign differ only in the inputs being compared, and each spec runs on more than one worker host as replicas. Runs are compared by their results, by density per host vCPU and by cost per 1,000 tasks, so a small host and a large one, or a nested host and a metal one, read on one scale. After a result, the next campaign changes one input, chosen from what the evidence shows.

**How two specs are compared.**

1. Each replica's result spans from the highest density that passed to the lowest that failed, each divided by the worker host's vCPUs.
2. Take the midpoint of that span.
3. Average the midpoints of a spec's replicas.
4. Compare specs by those averages, and show every replica's span beside them, so the spread between hosts is in view.

For example, cap-baseline-1 passed density 8 and failed 12 on 16 vCPUs: its span is 0.5 to 0.75 microVMs per host vCPU, and its midpoint 0.625. A replica that found no failing density has no midpoint; its result reads "at least" its top density, and the comparison waits for a campaign that tests higher.

## Worked example: cap-baseline-1

The first capacity run, on 27 September 2026, pre-registered in [capacity-experiment.md](capacity-experiment.md) (commit 652f26d, before the run).

**Setup:** one m8i.4xlarge worker host (nested; 16 virtual CPUs on 8 physical cores), Firecracker, microVMs of 2 virtual CPUs and 2 GiB.

| Density | Trials | Result |
|---|---|---|
| 1 | 1 | passed |
| 2 | 1 | passed |
| 4 | 1 | passed |
| 8 | 3 | all three passed |
| 12 | 3 | all three failed |
| 16 | 0 | not run: the run stopped at 12 |

In the order they ran: the warm-up, d1-t1, d2-t1, d4-t1, d8-t1, d12-t1, then the boundary trials d8-t2, d8-t3, d12-t2 and d12-t3, and last the illustration.

At density 12, every microVM became ready and every task succeeded. Each trial failed one latency target: the median home-page step took 1,279, 1,063 and 1,563 ms against a target of 1,000 ms.

**Result:** density 8 was tested successfully on this worker host. Densities 9 to 11 were not tried.

**Compared across hosts:** 0.5 microVMs per host vCPU (8 on 16), and a span of 0.5 to 0.75 with its midpoint at 0.625. About $0.075 per 1,000 tasks counting only the time the tasks ran, or $0.19 counting startup and cleanup too, at the assumed $0.84672 an hour.

**Attribution:** at density 12 the worker host's CPU was contended. Tasks were waiting for CPU for 35 to 47 percent of the time. The microVMs' virtual CPUs used almost all of it, and inside them Chromium's renderer processes did most of the work.
