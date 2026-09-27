# The site's dataset

This is the contract between the dataset builder, which reads `results/` and writes `public/data/`, and the site, which reads only `public/data/`. [src/lib/types.ts](src/lib/types.ts) copies the types below. If the two disagree, this file wins and `types.ts` gets fixed. [src/lib/contract.ts](src/lib/contract.ts) checks every rule in [Rules](#rules), and the tests run it over the fixtures.

## Words

The words come from the glossary in [docs/method.md](../docs/method.md).

- **Units**, each containing the next: campaign > run > trial > microVM.
  - A **campaign** is a named set of runs launched together to answer one question.
  - A **run** is one worker host carrying out one spec, start to finish.
  - A **trial** starts N fresh microVMs at the same moment, runs one task in each, and destroys them. N is the trial's density.
- **Density is a value, not a unit.** The spec lists the densities to test, and each trial records its own. Nothing in the dataset contains trials "inside" a density. `by_density` in a run is a summary of trials grouped by their density value, and the site filters a run's trials by density (`?density=8`).
- **Inputs:**
  - A **spec** is everything fixed about one run.
  - A **campaign definition** is the question, a base spec, the named specs written as changes from the base, and the number of replicas.
  - A **replica** is another run of the same spec on its own worker host.
<!-- legacy-names:start -->
- **Never in field names, ids, file names or values:** level, repeat, factor, condition, session (meaning a microVM), VMM (say hypervisor). Old run directories use these words. They appear in this file only here and in [Mapping older run directories](#mapping-older-run-directories), between markers, so the word test can skip those sections. Where a program is Chromium, the field says `chromium`.
<!-- legacy-names:end -->

## Files

```
data/
  index.json                                      every campaign, newest first
  campaigns/<campaign>/campaign.json              the definition, the specs, a summary of each run, the outcome per spec
  campaigns/<campaign>/runs/<run>.json            one run: results by density, every trial's summary, every task and step
  campaigns/<campaign>/runs/<run>/<trial>.json    one trial, fetched only when opened: microVM lanes, time series, limit detail
  img/<sha8>.t.webp  img/<sha8>.f.webp            screenshots: a 320 px thumbnail and the 1,280 px original
```

- Every URL is relative (`./data/...`), because the site is served under `evan.mx/fleetkit/`.
- The site loads `index.json` first, then the campaign, then a run, then a trial. A deeper document holds only what its parent doesn't, with two exceptions that let the contract check them against each other: a run document includes its `RunEntry` from the campaign, and a trial document includes its marks and verdicts from the run.

## Conventions

- **Schema:** every document carries `"schema": "fleetkit-site-data/1"`. A change that breaks a reader bumps the number.
- **Ids**, each unique within its parent, all `[a-z0-9-]`:

  | Id | Form | Example |
  |---|---|---|
  | campaign | chosen when defined | `nested-sizes-1` |
  | spec | chosen when defined | `m8i-2xlarge` |
  | run | `<spec>-r<replica>`, with replicas numbered from 1 | `m8i-2xlarge-r2` |
  | trial that counts | `d<density>-t<number>`, numbered from 1 within its density | `d8-t2` ("trial 2 at density 8") |
  | trial that doesn't count | its role, unnumbered: `warmup`, `illustration`, `fault`. A second one of the same role gets `-2`, a third `-3`, in execution order; this only tells them apart and is never shown as a trial number | `warmup` |
  | microVM | `index`, 1 to N within its trial (not an id string) | microVM 3 |

- **Time:**
  - A trial document gives integer ms from the trial's start (t = 0 is the moment all N microVMs were requested).
  - A run's overview gives seconds from the run's start.
  - The only absolute times are `started` and `ended`: ISO 8601 UTC, to the minute (`2026-09-27T16:49Z`).
- **Ranges** are `[min, max]`.
- **Absent vs null:** an optional field that's absent means this run's format didn't record it, and the site says "not recorded". `null` means it was recorded and has no value; for example, no density passed.
- **Precision:**
  - Summary numbers keep the builder's precision (at most 4 decimals), so the site can format a value without rounding it across its threshold (89.98% stays below 90%).
  - Time series are rounded to 3 significant figures.
- **Columns:** tables are objects of equal-length arrays, one array per column.
- **Classes of number:** every field below is **measured** unless marked otherwise.
  - **Derived:** computed from measured values, such as a boot phase from guest uptime.
  - **Modelled:** a measured time multiplied by an assumed price.
  - **Assumed:** an input that isn't measured, such as a price.
  - **Rule:** a verdict from rules fixed in the spec.

  Prose segments (`Seg`) carry their class with each number.

## Shared types

```ts
type Range = [number, number];                       // [min, max]
type StepName = 'home' | 'search' | 'open_product' | 'add_to_cart' | 'verify_cart';
type Subject = StepName | 'task';                     // a latency criterion applies to one step or to the whole task
type HostKind = 'nested' | 'metal';
type Hypervisor = 'firecracker' | 'cloud-hypervisor';
type TrialRole = 'ladder'        // the first trial at each density, climbing the spec's list
               | 'boundary'      // extra trials at the last passing and first failing densities
               | 'warmup' | 'illustration' | 'fault';   // excluded from the result
type Verdict = 'host_cpu' | 'microvm_cpu_allowance' | 'host_memory' | 'io' | 'steal' | 'none' | 'unknown';
type RuleKey = 'host_cpu_pressure_pct' | 'host_cpu_util_pct' | 'microvm_throttled_fraction'
             | 'host_mem_available_fraction' | 'host_mem_pressure_pct' | 'host_io_pressure_pct' | 'host_steal_pct';
type HostConsumer = 'microvm_vcpus' | 'hypervisor' | 'hostd' | 'driver' | 'unattributed';
type GuestGroup = 'renderer' | 'browser' | 'other_chromium' | 'guestd' | 'other';
type MicroVMOutcome = 'completed' | 'startup_timeout' | 'startup_error' | 'lifetime_expired'
                    | 'idle_expired' | 'task_failure_destroyed';
type Cls = 'measured' | 'derived' | 'modelled' | 'assumed' | 'rule';
type TermKey = 'campaign' | 'run' | 'trial' | 'microvm' | 'density' | 'spec' | 'replica' | 'worker_host'
             | 'host_kind' | 'hypervisor' | 'support_host' | 'tested_successfully' | 'p50' | 'p95'
             | 'cpu_pressure' | 'allocated';
type Seg = { t: string }                              // plain text
         | { term: TermKey; t?: string }              // a glossary word with a popover; t overrides the shown text
         | { v: string; cls: Cls; src: string };      // a number the builder formatted; src = "file › field › derivation"
```

`Verdict` is the attribution rule's name for what limited a trial:

| Verdict | Meaning | Rules |
|---|---|---|
| `host_cpu` | Host CPU | CPU pressure ≥ 20% or mean utilisation ≥ 90% |
| `microvm_cpu_allowance` | The microVM's own CPU allowance | mean throttled fraction ≥ 0.10 |
| `host_memory` | Host memory | memory available < 10%, or memory pressure ≥ 5% |
| `io` | Disk and network IO | IO pressure ≥ 10% |
| `steal` | CPU taken by the outer hypervisor under a nested worker host | mean steal ≥ 5% |
| `none` | No rule fired | |
| `unknown` | A signal some rule needs wasn't recorded | |

The thresholds shown are cap-baseline-1's. Each spec fixes its own; they're copied into `SpecDoc.rules`.

## index.json

```ts
interface Index {
  schema: 'fleetkit-site-data/1';
  latest: string | null;          // the campaign the site opens on: the most recently started one; null if none
  campaigns: CampaignEntry[];     // newest first
}

interface CampaignEntry {
  id: string;
  title: string;                  // a short name: "Two host sizes"
  question: string;               // the one question the campaign answers
  started: string;                // its first run's start
  status: 'complete' | 'partial'; // partial: at least one run is incomplete
  synthetic?: true;               // test fixtures only; see Synthetic data
  before_campaigns?: true;        // a single run published before campaigns existed, shown as a campaign of one
  replicas: number;
  specs: { name: string; label: string }[];
  runs: number;                   // runs published; specs × replicas when complete
  outcomes: SpecOutcome[];        // one per spec, in spec order
}

interface SpecOutcome {           // the comparison D52 asks for: one per spec, each array one entry per replica
  spec: string;
  runs: string[];                                          // run ids, in replica order
  tested_successfully: (number | null)[];                  // each run's result
  per_host_vcpu: (number | null)[];                        // derived: result ÷ host vCPUs
  cost_per_1000_tasks: ({ execution: Range; observed: Range } | null)[];   // modelled, USD, at each run's result
}
```

The site shows the spread across replicas as the min and max of these arrays, next to each value. It never shows a mean.

## campaigns/\<campaign\>/campaign.json

The inputs live here. Every run of a spec carried out the same spec, so the spec isn't repeated in each run document.

```ts
interface CampaignDoc {
  schema: 'fleetkit-site-data/1';
  id: string; title: string; question: string;
  started: string; ended: string;
  status: 'complete' | 'partial';
  synthetic?: true;
  before_campaigns?: true;
  provenance: 'recorded' | 'reconstructed';  // reconstructed: the run predates specs, so the builder rebuilt
                                             // the definition and spec from the run's recorded inputs
  preregistration?: { path: string; commit: string };   // repo path of the document that fixed the criteria,
                                                        // and the commit that first held it
  definition: CampaignDefinition;   // exactly as launched (scrubbed)
  spec_fields: SpecField[];         // how to label and group every spec path, in display order
  specs: SpecDoc[];                 // in definition order
  runs: RunEntry[];                 // spec order, then replica order
  outcomes: SpecOutcome[];          // same as the index entry
  answer?: Seg[];                   // the builder's answer paragraph; every number anchored to its source
  does_not_show?: Seg[][];          // one item per limitation of the result
  reading?: string;                 // Evan's own sentence; the builder never writes it
  next?: NextChange[];              // proposed follow-ups, each a change to the base spec
}

interface CampaignDefinition {      // what the experiment builder produces and Evan pastes into chat
  format: string;                   // the input schema's name and version
  id: string;
  question: string;
  base: Spec;
  specs: { name: string; label: string; changes: Record<string, unknown> }[];   // dotted spec path → value
  replicas: number;
}

type Spec = Record<string, unknown>;  // the input schema's run spec; the site never computes from it

interface SpecField { path: string; label: string; group: string; unit?: string; note?: string }

interface SpecDoc {
  name: string;
  label: string;                                          // "m8i.2xlarge (8 vCPUs)"
  changes: { path: string; base: unknown; value: unknown }[];   // every path where it differs from the base;
                                                                // [] when it is the base
  spec: Spec;                                             // the resolved spec, shown and compared, never computed from
  // Typed copies. The site computes only from these. The builder checks that each equals the value in `spec`.
  instance_type: string;
  host_kind: HostKind;                                    // derived from the instance type
  hypervisor: Hypervisor;
  microvm: { vcpus: number; mem_mib: number; mem_overhead_mib: number };
  densities: number[];                                    // ascending, as the run climbs them
  boundary_trials: number;                                // extra trials at the last passing and first failing densities
  criteria: Criteria;
  rules: RuleDef[];                                       // attribution rules and their thresholds
  price_usd_per_hour: number;                             // assumed: the public on-demand price, derived from the
                                                          // instance type, so it's never a spec input (D56)
}

interface Criteria { step_p50_ms: number; step_p95_ms: number; task_p95_ms: number; ready_limit_s: number }

interface RuleDef { key: RuleKey; label: string; verdict: Verdict; op: '>=' | '<'; threshold: number }

interface NextChange { text: string; motivated_by: string; changes: Record<string, unknown> }
```

**What differs between specs** comes from `changes`: one row per path that any spec changes, one column per spec, with the base value muted. Every other path is the same in all specs; the site says how many there are and can list them.

**The spec's shape.** `Spec` is the input schema's document (D41), and that schema is being written now. Until it lands, the fixtures use the interim shape below. The site reads a spec only through `spec_fields` (labels) and `changes` (differences), and computes only from the typed copies, so a change of shape needs no change to the site.

```jsonc
{
  "host":        { "instance_type": "m8i.4xlarge" },
  "hypervisor":  { "name": "firecracker", "version": "v1.17.0" },
  "microvm":     { "vcpus": 2, "mem_mib": 2048, "mem_overhead_mib": 256 },
  "workload":    { "task": "shop-5-steps", "products": 20 },
  "procedure":   { "densities": [1, 2, 4, 8, 12, 16], "boundary_trials": 2, "warmup_trials": 1,
                   "settle_s": 10, "illustration": true, "stop_at_first_failure": true, "release": "all_ready" },
  "criteria":    { "step_p50_ms": 1000, "step_p95_ms": 2000, "task_p95_ms": 5000, "ready_limit_s": 180 },
  "attribution": { "host_cpu_pressure_pct": 20, "host_cpu_util_pct": 90, "microvm_throttled_fraction": 0.1,
                   "host_mem_available_fraction": 0.1, "host_mem_pressure_pct": 5,
                   "host_io_pressure_pct": 10, "host_steal_pct": 5 },
  "measurement": { "host_hz": 5, "guest_interval_ms": 200 },
  "support":     { "instance_type": "m8i.xlarge" }
}
```

Under D56, nothing derivable from another input is a spec input: host kind comes from the instance type, and so does the price. Nothing measured is an input either, such as the CPU model or the Chromium version; those are in `HostFacts`.

### RunEntry: one run, as the campaign page needs it

```ts
interface RunEntry {
  id: string;                        // "<spec>-r<replica>"
  spec: string;
  replica: number;
  status: 'complete' | 'incomplete'; // incomplete: the run stopped before its procedure finished
  note?: string;                     // why it's incomplete, or anything else the reader must know
  started: string;
  duration_s: number;
  host: HostFacts;
  result: RunResult | null;          // null when incomplete: a run that didn't finish has no result
  by_density: DensityBrief[];        // every density in the spec, in order
}

interface HostFacts {                // measured on the worker host, never taken from the spec
  instance_type: string;             // from instance metadata
  host_kind: HostKind;               // derived from the instance type
  vcpus: number;                     // logical CPUs: the "host vCPUs" in density per host vCPU
  cores: number; threads_per_core: number; sockets: number;
  cpu_model: string;
  mem_gib: number;
  kernel_release: string;
  hypervisor_version: string;        // as the hypervisor binary reports it
  chromium_version: string;          // as the guest reports it
}

interface RunResult {
  tested_successfully: number | null;   // the highest density that passed with every lower density passing too;
                                        // null if the lowest density failed
  first_failed: number | null;          // the lowest density that failed; null if none failed
  not_tried: Range | null;              // densities strictly between the two that the spec didn't list: [9, 11]
  not_run: number[];                    // densities in the spec that the run never reached
  per_host_vcpu: number | null;         // derived: tested_successfully ÷ host.vcpus
  vcpus_allocated_per_host_vcpu: number | null;   // derived: tested_successfully × microVM vCPUs ÷ host.vcpus
  cost_per_1000_tasks: CostRanges | null;         // modelled, at tested_successfully, over its trials
  limit: RunLimit | null;               // what limited the run at first_failed; null if none failed
}

interface CostRanges { execution: Range; observed: Range; fixture: Range }   // USD per 1,000 tasks

interface RunLimit {
  density: number;                      // = first_failed
  verdicts: Verdict[];                  // rule; the union over its trials, most severe first
  trials_with_verdict: number;          // how many of its trials had verdicts[0]
  trials: number;
  separated_by?: {                      // the rule whose values don't overlap between the last passing
    rule: RuleKey;                      // and the first failing density, if one exists
    passing: Range; failing: Range; threshold: number;
  };
  host_consumer: HostConsumer;          // the largest user of host CPU at first_failed, over its trials
  guest_group: GuestGroup;              // the largest share of CPU inside the microVMs
  guest_share: Range;                   // that group's share, over its trials
}

interface DensityBrief {
  density: number;
  result: 'passed' | 'failed' | 'not_run';
  passed: number;                       // how many of its counting trials passed
  trials: number;                       // how many counting trials ran at it
}
```

## campaigns/\<campaign\>/runs/\<run\>.json

```ts
interface RunDoc extends RunEntry {
  schema: 'fleetkit-site-data/1';
  campaign: string;
  synthetic?: true;
  code: { commit: string; dirty: boolean };   // the commit the run ran from
  has: Has;                                   // what this run's format recorded
  by_density: DensityResult[];                // every density in the spec, in order
  trials: TrialSummary[];                     // every trial, including those that don't count, in execution order
  tasks: TaskColumns;                         // one row per task
  steps: StepColumns;                         // one row per step
  overview: {                                 // 1 Hz means over the whole run
    t_s: number[]; cpu_util_pct: number[];
    bands: { trial: string; start_s: number; end_s: number }[];
  };
  support?: { t_s: number[]; cpu_util_pct: number[] };   // the support host, on its own clock, not aligned to trials
  answer?: Seg[];
}

interface Has {
  host_hz: 1 | 5;
  boot_phases: boolean; per_microvm_cpu: boolean; guest_series: boolean;
  attribution: boolean; cost: boolean; filmstrip: boolean; fixture_rtt: boolean;
}

interface DensityResult extends DensityBrief {
  trial_ids: string[];                        // its counting trials, by number
  checks: { subject: Subject; stat: 'p50' | 'p95'; target_ms: number; range: Range; met: number }[];
                                              // range over its trials; met = how many trials met it
  host: { cpu_util_pct: Range; cpu_pressure_pct: Range | null; busy_cores: Range; mem_used_gib: Range } | null;
  vcpus_allocated: number;                    // derived: density × microVM vCPUs
  mem_allocated_gib: number;                  // derived: density × (microVM MiB + overhead MiB) ÷ 1024
  verdicts: Verdict[];                        // rule; the union over its trials, most severe first
  cost_per_1000_tasks?: CostRanges;           // modelled; passed densities only
}

interface TrialSummary {
  id: string;                        // "d8-t2" | "warmup" | "illustration" | "fault"
  density: number;
  number: number | null;             // trial number within its density; null unless the trial counts
  role: TrialRole;
  counts: boolean;                   // true exactly when role is ladder or boundary
  order: number;                     // position in the run's execution, from 1; used to order the run's
                                     // timeline, never shown as a trial number
  passed: boolean | null;            // met every criterion; null when the trial doesn't count
  at_limit: boolean;                 // passed while an attribution rule fired
  failed: string[];                  // the criteria it failed, worded by the builder: "home p50 1,279 ms > 1,000 ms"
  ready: { microvms: number; all_ready_ms: number | null; limit_ms: number; met: boolean };
  tasks: { ok: number; of: number };
  checks: Check[];                   // every latency criterion, in spec order
  clean: boolean;                    // nothing was left on the host afterwards
  marks: TrialMarks;
  attribution: TrialAttribution | null;
  host: { cpu_util_pct: number; cpu_pressure_pct: number | null; busy_cores: number;
          mem_used_gib: number; steal_pct: number | null } | null;   // over the task window
  microvm_mem_peak_mib: Range | null;                                 // over its microVMs
  cost_per_1000_tasks: { execution: number; observed: number; fixture: number } | null;   // modelled; counting trials only
  excluded_because?: string;         // trials that don't count: why, without restating the role
                                     // ("the first microVM after setup reads its root filesystem from disk")
  fault?: string;                    // fault trials: what was injected
}

interface Check { subject: Subject; stat: 'p50' | 'p95'; value_ms: number; target_ms: number; met: boolean }

interface TrialMarks {               // ms from the trial's start; null if the trial never got there
  all_ready_ms: number | null; release_ms: number | null; last_return_ms: number | null;
  clean_ms: number | null; end_ms: number;
}

interface TrialAttribution {
  window_ms: Range;                  // the task window: release to last return
  verdicts: Verdict[];               // rule
  rules: { key: RuleKey; value: number | null; fired: boolean }[];
  host_cpu_s: Record<HostConsumer, number> & { busy: number };   // CPU-seconds in the window, by process
  guest_share: Record<GuestGroup, number> | null;                 // shares of busy guest CPU (total − idle), summing to 1
  missing: RuleKey[];                // signals that weren't recorded
}

interface TaskColumns {
  trial: string[]; microvm: number[]; product: string[]; ok: boolean[];
  task_ms: (number | null)[]; wall_ms: (number | null)[];
  failed_step: (StepName | null)[]; failure_category: (string | null)[];
  timing_valid: boolean[];           // false for the illustration, whose task time includes screenshots
  img: (string | null)[];            // sha8 of the final screenshot
}

interface StepColumns { trial: string[]; microvm: number[]; step: StepName[]; duration_ms: number[]; ok: boolean[] }
```

## campaigns/\<campaign\>/runs/\<run\>/\<trial\>.json

This is fetched only when a trial is opened.

```ts
interface TrialDoc {
  schema: 'fleetkit-site-data/1';
  campaign: string; run: string; id: string;
  synthetic?: true;
  density: number; number: number | null; role: TrialRole;
  window_ms: Range;                  // the span drawn: settle start (approximate) to clean + 1,000
  marks: TrialMarks;
  settle?: { seconds: number; cpu_util_mean_pct: number };   // the idle period before the trial (a summary only)
  microvms: MicroVMLane[];           // by index
  series: TrialSeries;
  limit: TrialLimit;                 // the limiting resource, per microVM
  fault?: { microvm: number; at_ms: number; label: string };
  filmstrip?: { microvm: number; frames: { step: StepName; img: string }[] };   // illustration only; untimed
}

interface MicroVMLane {
  index: number;                     // 1..density
  product: string;                   // the product its task shopped for; the same index gets the same product in every trial
  outcome: MicroVMOutcome;
  ready_ms: number | null;           // when it reported its browser ready
  boot?: {                           // derived after kernel_start: guest uptime minus half a round trip
    process_started_ms: number; kernel_start_ms: number; guestd_start_ms: number;
    chromium_launch_ms: number; chromium_ready_ms: number;
  };
  task?: {
    dispatch_ms: number; return_ms: number; task_ms: number; wall_ms: number; ok: boolean;
    failed_step?: StepName; failure_category?: string;
    bytes: number; requests: number; chromium_rss_mib: number;
    clock_offset_ms: number;         // guest clock minus host clock; already applied to steps and guest series
    timing_valid: boolean;
  };
  steps: { name: StepName; start_ms: number; end_ms: number; ok: boolean; bytes?: number; requests?: number }[];
  destroy: { start_ms: number; end_ms: number } | null;   // start is derived: end minus cleanup time
  img?: string;                      // sha8 of the final screenshot
}

interface TrialSeries {
  host: {
    t_ms: number[];                  // 5 Hz inside window_ms (1 Hz in the legacy format)
    cpu_util_pct: number[]; mem_used_gib: number[];
    cpu_pressure_pct?: number[]; mem_pressure_pct?: number[]; io_pressure_pct?: number[]; steal_pct?: number[];
    cores?: Record<HostConsumer, number[]>;   // derived: counters differenced into cores
  };
  microvms?: { index: number; t_ms: number[]; vcpu_cores: number[]; hypervisor_cores: number[];
               throttled_fraction: number[]; mem_mib: number[] }[];
  guest?: ({ index: number; t_ms: number[] } & Record<GuestGroup, number[]>)[];   // cores per 200 ms, host clock
  fixture_rtt?: { t_ms: number[]; ms: number[] };
}

interface TrialLimit {
  verdicts: Verdict[];               // rule; equal to the run document's trial summary
  microvms: { index: number; vcpu_s: number; hypervisor_s: number; throttled_fraction: number;
              cpu_pressure_pct: number | null; mem_peak_mib: number }[];
}
```

## Rules

The builder applies these rules, and `contract.ts` checks them on every document it's given. A document that breaks one is refused, not repaired.

1. **A trial passes** if it meets every criterion in its spec: `ready.met`, and `tasks.ok === tasks.of`, and every `checks[].met`, and `clean`. Only trials that count (`ladder`, `boundary`) have a `passed` value.
2. **Trial numbers** at each density run 1, 2, 3… in execution order with no gaps. Trial `d<n>-t<k>` has `density` n and `number` k. Trials that don't count have `number: null`.
3. **A density passes** if at least one trial ran at it and every trial at it passed. It's `not_run` if no counting trial ran at it, and `failed` otherwise.
4. **A run's result** (`tested_successfully`) is the highest density in the spec's list that passed with every lower listed density passing too. It's always described as "tested successfully", never as a maximum.
   - `first_failed` is the lowest density that failed.
   - `not_tried` is the integers strictly between `tested_successfully` and `first_failed`, if there are any.
   - `not_run` is the listed densities no counting trial reached.
5. **Comparing hosts (D52):**
   - `per_host_vcpu` = `tested_successfully` ÷ `host.vcpus`.
   - Cost per 1,000 tasks = 1,000 × price per hour ÷ 3,600 × window seconds ÷ density. The **execution** window runs from release to last return; the **observed** window from the trial's start to clean. Fixture serving is modelled on its own line.
   - Cost is given only at densities that passed. The price is always shown as assumed.
6. **Replicas:** a spec's outcome lists every replica. The spread is the min and max across them. Differences between specs mean something only when they're larger than the spread across replicas and the variation between trials at one density.
7. **Consistency:**
   - `by_density` lists exactly the spec's `densities`, in order.
   - `RunEntry.by_density` in the campaign document equals the run document's, field by field.
   - `outcomes` equals the runs' results.
   - A campaign whose runs are all complete has `specs × replicas` runs.

## What the builder never ships

Every output object is built field by field from this file. Nothing is copied through wholesale. Never shipped:
- instance, AMI and account ids
- ARNs
- private or bridge IP addresses
- hostnames and URLs
- error texts (only `failure_category` is kept)
- guest logs, spans, console logs, cloud-init output
- the driver's command line
- image metadata (EXIF, ICC, XMP)

A gate scans every published byte for these, using the patterns in the explorer spec, and fails the build on a match.

## Synthetic data

Test fixtures may contain made-up campaigns, marked in six places so they can't be mistaken for results:
- `synthetic: true` on the index entry, the campaign document, and every run and trial document
- an id ending in `-synthetic`
- a title starting with "Synthetic:"

The site shows a banner on every page of a synthetic campaign. `npm run build` fails if any file under `public/data/` contains `"synthetic": true`. Fixtures live only in `tests/fixtures/data/`. They're served in development with `npm run dev:fixtures`, and in end-to-end tests by the preview server's `--data` option. Neither copies them into `public/` or `dist/`.

## Mapping older run directories

<!-- legacy-names:start -->
Runs recorded before the glossary (D56) used different words. The builder translates them. The site never sees an old name. Runs recorded after the harness rename (D45) use the glossary's names, and this table covers the directories recorded before it: cap-baseline-1 is the one that gets published.

- **Campaign:** a run directory published before campaigns existed, `results/<name>/host/capacity`, becomes campaign `<name>`, with `before_campaigns: true` and `provenance: 'reconstructed'`. It has one spec, `baseline`, with no changes, and one run, `baseline-r1`. cap-baseline-1 is the only such campaign.
- **Spec** (reconstructed from `run.json.inputs` and `capacity.env`):

  | Old | New |
  |---|---|
  | `options.n` / `inputs.ladder` | `procedure.densities` |
  | `confirm_repeats` | `procedure.boundary_trials` |
  | `warmup` | `procedure.warmup_trials` |
  | `settle_s`, `illustration` | `procedure.settle_s`, `procedure.illustration` |
  | `stop_at_first_miss` | `procedure.stop_at_first_failure` |
  | `criteria.step_p50_target_ms` / `step_p95_target_ms` / `task_p95_target_ms` | `criteria.step_p50_ms` / `step_p95_ms` / `task_p95_ms` |
  | `ready_timeout_s` | `criteria.ready_limit_s` |
  | `backend` | `hypervisor.name` |
  | `host_info.firecracker.version` ("Firecracker v1.17.0") | `hypervisor.version` ("v1.17.0") |
  | `vcpus`, `mem_mib`, `host_info.firecracker.mem_overhead_mib` | `microvm.vcpus`, `microvm.mem_mib`, `microvm.mem_overhead_mib` |
  | `metrics_hz`, `sample_interval_ms` | `measurement.host_hz`, `measurement.guest_interval_ms` |
  | `capacity.env` `INSTANCE_TYPE_EXPECTED`, `SUPPORT_INSTANCE_TYPE` | `host.instance_type`, `support.instance_type` |
  | `capacity.env` `PRICE_PER_HOUR` | `SpecDoc.price_usd_per_hour` (assumed; not a spec input) |
  | `attribution.THRESHOLDS` | `attribution.*` (keys below) |

- **Trials:**
  - `trial.json` `level_n` → `density`.
  - The run-wide trial ids (`t005-firecracker-n8-r1`, `t007-firecracker-n8-r2`) become ids numbered within their density: `d8-t1`, `d8-t2`. `number` is the trial's position among the counting trials at its density, in execution order. The run-wide sequence (`t005`) survives only as `order`.
  - `t001-warmup-n1-r1` → `warmup`, `t011-illustration-n1` → `illustration`.
  - `kind` → `role`: `ladder` → `ladder`, `confirm` → `boundary`, and `warmup`, `illustration` and `fault` keep their names.
  - Smoke trials are never published.
  - `repeat` is dropped: `number` replaces it.
  - `evaluation.passed` → `passed`. Never `status`, `level_passed` or `manifest.trials[].level_passed`, which only report whether the protocol ran.
  - `evaluation.checks[].name`: "step home p50" → `{subject: 'home', stat: 'p50'}`, and "task p95" → `{subject: 'task', stat: 'p95'}`.
  - `timestamps`: `create_start` → t = 0; `all_ready` → `all_ready_ms`, `barrier_release` → `release_ms`, `last_task_return` → `last_return_ms`, `verify_clean_pass` → `clean_ms`.
  - `pre_trial` → `settle`.
- **MicroVMs:**
  - `sessions.csv` and `trial.sessions[]` → `microvms[]`.
  - `slot` (from 0) → `index` = slot + 1.
  - `session_id` is dropped.
  - `sessions_ready` / `sessions_requested` → `ready.microvms` / `density`.
  - Failure category `session_not_ready` → `microvm_not_ready`.
  - `outcome` values are unchanged.
- **Attribution:**
  - `attribution.sessions[]` → `limit.microvms[]`.
  - `vcpu_s` → `vcpu_s`, `vmm_s` → `hypervisor_s`, `cgroup_cpu_pressure_some_pct` → `cpu_pressure_pct`, `cgroup_memory_peak_bytes` → `mem_peak_mib`.
  - Host CPU-seconds: `vcpu_s` → `microvm_vcpus`, `vmm_s` → `hypervisor`, `hostd_cpu_s` → `hostd`, `driver_cpu_s` → `driver`, `unattributed_cpu_s` → `unattributed`.
  - Series: `cpu_vcpu_usec` → `vcpu_cores`, `cpu_vmm_usec` → `hypervisor_cores`, `cpu_throttled_usec` → `throttled_fraction`, `cgroup_memory_current` → `mem_mib`, all differenced into rates.
  - Verdict `vm_cpu_quota` → `microvm_cpu_allowance`. The others keep their names.
  - Threshold keys:

    | Old | New |
    |---|---|
    | `host_psi_cpu_some_pct` | `host_cpu_pressure_pct` |
    | `host_cpu_util_mean_pct` | `host_cpu_util_pct` |
    | `vm_throttled_fraction_mean` | `microvm_throttled_fraction` |
    | `host_mem_available_min_fraction` | `host_mem_available_fraction` (op `<`) |
    | `host_psi_memory_some_pct` | `host_mem_pressure_pct` |
    | `host_psi_io_some_pct` | `host_io_pressure_pct` |
    | `host_steal_mean_pct` | `host_steal_pct` |

  - Guest groups fold into five:

    | Old | New |
    |---|---|
    | `renderer` | `renderer` |
    | `browser` | `browser` |
    | `gpu`, `network`, `utility`, `zygote`, `chromium_other` | `other_chromium` |
    | `guestd` | `guestd` |
    | `other`, and kernel or unlisted time (total − idle − groups) | `other` |

- **Results:**
  - `report.levels[]` is never copied. `by_density` is recomputed from the trials, and the builder checks it against `report.levels[].passed`.
  - `plan.boundary.last_pass` / `first_miss` → `tested_successfully` / `first_failed`, recomputed and checked.
  - `plan.levels_not_run` → `not_run`.
  - `report.headline.*.highest_n_tested_successfully` is a cross-check only.
- **Cost:**
  - `cost_usd_per_task` × 1,000 → `cost_per_1000_tasks`.
  - `execution_only` → `execution`, `observed` → `observed`, `fixture_serving_estimate` → `fixture`.
- **Screenshots:**
  - `screenshots/<task_id>.jpg` → `img/<sha8>.{t,f}.webp`, deduplicated by the SHA-256 of the JPEG.
  - Illustration step screenshots → `filmstrip`.
- **Format:**
  - Runs from before the capacity format (1 Hz, no boot phases, no per-VM or guest series) get `has.host_hz: 1` and the matching `has` flags set to false.
  - Validation runs (`aws-val-*`) aren't capacity results and aren't published.
<!-- legacy-names:end -->
