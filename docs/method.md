# How we run capacity experiments

This page defines the words we use and the procedure every capacity experiment follows. Specs, pre-registrations, reports, the explorer and the code use these words and no others. When a term here changes, everything else changes with it.

## The question

How many browsers can one worker host run at the same time, each doing a real shopping task, with every task inside its latency targets? At that number, what does a task cost, and what ran out first: which resource, used by which process?

Every browser runs in its own microVM. That isolation is a requirement of the product, not something an experiment varies.

## Glossary

### The setup

| Term | Meaning |
|---|---|
| **Worker host** | The EC2 instance whose capacity is measured. |
| **Host kind** | *Metal*: an EC2 bare-metal instance, where microVMs run directly on the hardware. *Nested*: an ordinary EC2 instance, where microVMs run inside it through nested virtualization. |
| **Hypervisor** | The program on the worker host that runs each microVM: Firecracker or Cloud Hypervisor. |
| **Support host** | A separate instance that serves the test shopping site and collects telemetry, so neither competes with the microVMs for the worker host's CPU. |

### What a result is made of

From biggest to smallest. Each contains the next.

| Term | Meaning |
|---|---|
| **Campaign** | Several runs launched together. |
| **Run** | One worker host executing one spec from start to finish. |
| **Level** | A number N. At level N, N microVMs are started at the same moment. Level 8 means 8 microVMs at once. A run tests several levels. |
| **Trial** | One attempt at a level: start N fresh microVMs, run one shopping task in each, destroy them. Trials at the same level run one after another, never overlapping, and are numbered from 1 within their level: "trial 2 at level 8". Every trial at a level has identical inputs, down to which product each microVM shops for. Only the conditions the experiment doesn't control differ: fresh microVMs, when the trial ran, and the host's state at that moment. |
| **MicroVM** | One of the N in a trial. It runs one headless Chrome, and that browser runs one shopping task of five steps. |

### How runs relate

| Term | Meaning |
|---|---|
| **Spec** | The complete, fixed inputs of a run: the host, the hypervisor, the microVM's size, the workload, the procedure and the pass criteria. A spec is written and committed before its run. |
| **Replica** | Another run with the same spec on a different worker host. |

Extra trials at one level show how much the same test varies on the same host. Replicas show how much it varies from host to host. A comparison between two specs means something only when the difference between them is larger than both.

### Words we don't use

| Don't say | Say |
|---|---|
| repeat | trial 2 at level 8 |
| factor, condition | the specs in this campaign, or the input that differs |
| VMM | hypervisor |
| session, for a microVM | microVM |

## The procedure

### 1. Write the spec and commit it first

The spec names everything the run will do, including the pass criteria and the rules for reading the result. It is committed before the run starts, and the run records the commit it ran from. Anyone can then check that the criteria weren't chosen after the data arrived.

### 2. One run on one worker host

1. **Warm-up.** One trial at level 1, excluded from the result. The first microVM after setup reads its root filesystem from disk; later ones read it from memory.
2. **The ladder.** The levels in increasing order, for example 1, 2, 4, 8, 12, 16, with one trial each.
3. **Stop at the first failing level.** No higher level runs.
4. **Check the boundary.** Two more trials at the last passing level and two more at the first failing level, three trials at each. If a new trial at the last passing level fails, that level becomes a failing level, and the next lower passing level gets two more trials, and so on down.
5. **Illustration.** One trial at level 1 that takes a screenshot after every step, for the filmstrip. The screenshots take time inside the task, so this trial is excluded from the result.

### 3. One trial

1. The worker host sits idle for a fixed period, and its CPU during that time is recorded.
2. N microVMs are started together. The trial waits until every one reports its browser ready.
3. All N shopping tasks are released at the same moment. Each task has five steps: open the home page, search, open a product, add it to the cart, and verify the cart.
4. When every task has returned, all N microVMs are destroyed, and the host is checked for anything left behind.

### 4. Judge

A **trial passes** only if all of its criteria hold:

- all N microVMs became ready within the time limit
- all N tasks succeeded
- every step met its latency targets over the trial's N tasks
- the whole task met its latency target
- nothing was left on the host

A **level passes** only if every trial at it passed.

The **result of a run** is the highest level that passed with every lower level also passing. It is always stated as "tested successfully", never as a maximum, because levels between the last pass and the first miss were not tried.

### 5. Record

Every run records:

- **Inputs:** its spec, and facts measured on the worker host, such as its CPU model and core count.
- **Per microVM:** when it was created, when its kernel, guest and browser started, when it became ready, and when it was destroyed.
- **Per step:** start, end, bytes and requests.
- **Samples:** the worker host's CPU, pressure and memory, five times a second, split by microVM and by the hypervisor's own threads. Inside each microVM, CPU and memory by process type during its task.
- **Screenshots:** one per task, plus the illustration trial's filmstrip.

### 6. Attribute

Each trial gets a verdict on which resource limited it. The verdict is computed by rules whose thresholds are fixed in the spec, from what was measured while the tasks ran:

- host CPU
- the microVM's own CPU allowance
- host memory
- disk and network IO
- CPU taken by the hypervisor under a nested worker host
- none of these

Beside the verdict, the report names the process that used the resource: on the worker host, each microVM's virtual CPUs against the hypervisor's own work; inside a microVM, the browser's renderer against its other processes.

### 7. Compare

A campaign runs several specs at once. Specs in a campaign differ only in the inputs being compared, and each spec runs on more than one worker host as replicas. After a result, the next experiment changes one input, chosen from what the evidence shows.

## Worked example: cap-baseline-1

The first capacity run, on 27 September 2026, pre-registered in [capacity-experiment.md](capacity-experiment.md) (commit 652f26d, before the run).

**Setup:** one m8i.4xlarge worker host (nested; 16 virtual CPUs on 8 physical cores), Firecracker, microVMs of 2 virtual CPUs and 2 GiB.

| Level | Trials | Result |
|---|---|---|
| 1 | 1 | passed |
| 2 | 1 | passed |
| 4 | 1 | passed |
| 8 | 3 | all three passed |
| 12 | 3 | all three failed |
| 16 | 0 | not run: the ladder stopped at 12 |

At level 12, every microVM became ready and every task succeeded. Each trial failed one latency target: the median home-page step took 1,279, 1,063 and 1,563 ms against a target of 1,000 ms.

**Result:** level 8 was tested successfully on this worker host. Levels 9 to 11 were not tried.

**Attribution:** at level 12 the worker host's CPU was contended. Tasks were waiting for CPU for 35 to 47 percent of the time. The microVMs' virtual CPUs used almost all of it, and inside them Chrome's renderer processes did most of the work.
