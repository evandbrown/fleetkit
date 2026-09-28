# The site's dataset

This is the contract between the dataset builder ([build/](build/README.md)), which reads `results/` and writes `public/data/`, and the site, which reads only `public/data/`. [src/lib/types.ts](src/lib/types.ts) copies the types below; if the two disagree, this file wins. [src/lib/contract.ts](src/lib/contract.ts) checks every rule in [Rules](#rules), and the tests run it over the fixtures and every build.

## Words

The words are the method's ([docs/method.md](../docs/method.md)).

- **Units**, each containing the next: campaign > run > trial > microVM. A **campaign** is a named set of runs launched together to answer one question. A **run** is one worker host carrying out one spec, start to finish. A **trial** starts N fresh microVMs at the same moment, runs one task in each, and destroys them; N is the trial's density.
- **Density is a value, not a unit.** The spec lists the densities to test, and each trial records its own. `by_density` in a run summarises trials grouped by their density.
- **Inputs:** a **spec** is everything fixed about one run ([docs/spec.md](../docs/spec.md)). A **campaign definition** is the question, a base spec, the named specs written as changes from the base, and the number of **replicas** (runs per spec, each on its own worker host).
- **Missing data is labelled where it would be**, with no other result states: *not tested* (a density the run didn't reach or couldn't test cleanly, or the densities between a result and the first failure), *warm-up, not judged* and *illustration, not judged* (the labelled trials), *stopped early* (a run that ended before its procedure finished).
<!-- legacy-names:start -->
- **Never in field names, ids, file names or values:** level, repeat, factor, condition, session (meaning a microVM), VMM (say hypervisor), or experiment as a unit. Old run directories use some of these; the builder reads them through the harness's one alias map (`harness/driver/driver/legacy.py`), so the site never sees them.
<!-- legacy-names:end -->

## Files

```
data/
  index.json                                      every campaign, newest first, and the featured one
  campaigns/<campaign>/campaign.json              the definition, the specs, each run's summary, the results per spec
  campaigns/<campaign>/runs/<run>.json            one run: results by density, every trial's summary, every task and step
  campaigns/<campaign>/runs/<run>/<trial>.json    one trial, fetched only when opened: microVM lanes and time series
  img/<sha8>.t.webp                               every screenshot as a 320 px thumbnail
  img/<sha8>.f.webp                               and at full size (1,280 px), only where rule 10 publishes it
```

Every URL is relative (`./data/...`), because the site is served under `evan.mx/fleetkit/`. The site loads `index.json`, then a campaign, then a run, then a trial. A run document includes its `RunEntry` from the campaign, and a trial document its marks from the run, so the contract can check them against each other.

## Conventions

- **Schema:** every document carries `"schema": "fleetkit-site-data/2"`.
- **Ids,** unique within their parent, all `[a-z0-9-]`: campaign (its name, `nested-sizes-1`); spec (its name in the definition, `m8i-2xlarge`); run `<spec>-r<k>`, replicas from 1 (`m8i-2xlarge-r2`); a trial that counts `d<density>-t<k>`, numbered from 1 within its density in execution order (`d8-t2`, "trial 2 at density 8"); a labelled trial `warmup` or `illustration` (`-2`, `-3` for a second or third); a microVM its `index`, 1 to N within its trial.
- **Time:** a trial document gives integer ms from the trial's start (t = 0: all N microVMs requested). The only absolute times are `started` and `ended`, ISO 8601 UTC to the minute (`2026-09-27T16:49Z`).
- **Ranges** are `[min, max]`. **Tables** are objects of equal-length arrays.
- **Precision:** summary numbers keep at most 4 decimals, so the site never rounds a value across its threshold (89.98% stays below 90%); time series keep 3 significant figures.
- **Numbers are measured** unless marked: *derived* (computed from measured values), *modelled* (a measured time at an assumed price), *assumed* (a price).

## Shared types

```ts
type Range = [number, number];
type StepName = 'home' | 'search' | 'open_product' | 'add_to_cart' | 'verify_cart';
type Subject = StepName | 'task';
type HostKind = 'nested' | 'metal';
type Hypervisor = 'firecracker' | 'cloud-hypervisor';
type TrialRole = 'ladder' | 'boundary'          // count toward the result
               | 'warmup' | 'illustration';     // labelled, not judged
type Verdict = 'host_cpu' | 'microvm_cpu_allowance' | 'host_memory' | 'io' | 'steal' | 'none' | 'unknown';
type RuleKey = 'host_cpu_pressure_pct' | 'host_cpu_util_pct' | 'microvm_throttled_fraction'
             | 'host_mem_available_fraction' | 'host_mem_pressure_pct' | 'host_io_pressure_pct' | 'host_steal_pct';
type HostConsumer = 'microvm_vcpus' | 'hypervisor' | 'hostd' | 'driver' | 'unattributed';
type GuestGroup = 'renderer' | 'browser' | 'other_chromium' | 'guestd' | 'other';
type MicroVMOutcome = 'completed' | 'startup_timeout' | 'startup_error' | 'lifetime_expired'
                    | 'idle_expired' | 'task_failure_destroyed';

// The spec, exactly as experiments/schema/spec.schema.json defines it.
interface Spec {
  worker_host: { instance_type: string };
  hypervisor: { name: Hypervisor; virtio_transport: 'mmio' | 'pci'; virtio_rng: boolean };
  microvm: { vcpus: number; memory_mib: number;
             console?: 'verbose' | 'quiet' | 'quiet-i8042';  // optional: absent is verbose
             memory_pages?: '4k' | 'thp' };                  // optional: absent is 4k
  densities: number[];
  criteria: { step_p50_target_ms: number; step_p95_target_ms: number; task_p95_target_ms: number;
              ready_timeout_s: number; step_timeout_ms: number; task_timeout_ms: number };
  procedure: { trials_per_density: number; boundary_trials: number; settle_s: number;
               release_after_ready_s?: number };            // optional: absent is 0
  support_host: { instance_type: string };
  workload?: { chromium_extra_flags?: string[] };            // optional: absent is no extra Chromium flags
}
```

A `Verdict` is what the attribution rules say limited a trial: host CPU (CPU pressure ≥ 20% or utilisation ≥ 90%), the microVM's own CPU allowance, host memory, disk and network IO, or CPU taken by the outer hypervisor under a nested host (steal). The thresholds are fixed in the harness; each campaign document lists the ones its runs were judged by.

## index.json

```ts
interface Index {
  schema: 'fleetkit-site-data/2';
  featured: string | null;         // the campaign the site opens on (D58): the catalog's choice, else the
                                   // newest complete campaign, else the newest; null when there are none
  campaigns: CampaignEntry[];      // newest first
}

interface CampaignEntry {
  id: string;
  title: string;                   // a short name, from the catalog: "Two host sizes"
  question: string;
  started: string;                 // its first run's start
  status: 'complete' | 'partial';  // partial: fewer runs than specs × replicas, or a run stopped early
  synthetic?: true;                // test fixtures only
  before_campaigns?: true;         // one run published before campaigns existed (the site shows it like any other)
  replicas: number;
  specs: { name: string; label: string }[];
  runs: number;                    // runs published
  outcomes: SpecOutcome[];         // one per spec, in spec order
}

interface SpecOutcome {            // a spec's results across its replicas (D52, D60)
  spec: string;
  replicas: ReplicaResult[];       // one per published run, in replica order
  midpoint_per_host_vcpu: number | null;   // derived: the mean of the replicas' midpoints; null if any has none
}

interface ReplicaResult {
  run: string;
  host_vcpus: number;              // measured
  tested_successfully: number | null;
  first_failed: number | null;
  stopped_early: boolean;
  per_host_vcpu: number | null;            // derived: tested_successfully ÷ host_vcpus
  midpoint_per_host_vcpu: number | null;   // derived: rule 6
  cost_per_1000_tasks: CostRanges | null;  // modelled, at tested_successfully
}

interface CostRanges { execution: Range; observed: Range }   // USD per 1,000 tasks, over the trials at a density
```

## campaigns/\<campaign\>/campaign.json

```ts
interface CampaignDoc {
  schema: 'fleetkit-site-data/2';
  id: string; title: string; question: string;
  started: string; ended: string;
  status: 'complete' | 'partial';
  synthetic?: true;
  before_campaigns?: true;
  reconstructed?: true;            // the definition was rebuilt afterwards from what the run recorded
  preregistration?: { path: string; commit: string };   // the document that fixed the criteria first, and the commit
                                   // that added it, abbreviated to 10 hex characters (rule 9)
  definition_path: string | null;  // D73: the definition's file, experiments/campaigns/<campaign>.json, when GitHub's main
                                   // branch has it and it equals the definition as launched; for a reconstructed
                                   // definition, its pre-registration's path; else null (always null when synthetic)
  definition: CampaignDefinition;  // as launched
  specs: SpecDoc[];                // in definition order
  runs: RunEntry[];                // spec order, then replica order
  outcomes: SpecOutcome[];         // as in the index entry
  rules: RuleDef[];                // the attribution rules the runs were judged by
  notes?: string[];                // from the catalog: the limits of this result, in plain words
}

interface CampaignDefinition {     // experiments/schema/campaign.schema.json, verbatim
  name: string;
  question: string;
  replicas: number;
  shutdown_after_minutes?: number; // absent only in a reconstructed definition
  base: Spec;
  specs: Record<string, Partial<Spec>>;   // each named spec's changes from the base, in order
  why?: Record<string, string>;
}

interface SpecDoc {
  name: string;
  label: string;                   // what sets it apart from the campaign's other specs ("m8i.2xlarge"),
                                   // or, for a campaign's only spec, a summary ("m8i.4xlarge, Firecracker, 2 vCPU / 2 GiB")
  why?: string;                    // the definition's reason for it
  changes: { path: string; base: unknown; value: unknown }[];   // every leaf where it differs from the base
  spec: Spec;                      // resolved: the base merged with its changes
  host: {                          // follows from the instance type (instance-types.json); never an input (D56)
    instance_type: string; host_kind: HostKind; vcpus: number; memory_gib: number;
    price_usd_per_hour: number;    // assumed: the public on-demand price
    price_estimated: boolean;      // the price is an estimate, not yet from the AWS price list
  };
}

interface RuleDef { key: RuleKey; label: string; verdict: Verdict; op: '>=' | '<'; threshold: number }
```

Labels, units and order for spec paths come from `spec.schema.json`'s `x-builder`, which the site bundles; the dataset doesn't carry them.

### RunEntry

```ts
interface RunEntry {
  id: string; spec: string; replica: number;
  stopped_early: boolean;          // it ended before its procedure finished: its shutdown timer fired, or it was
                                   // interrupted (not when a density couldn't be tested cleanly: rule 7)
  started: string;
  duration_s: number;
  harness_commit: string | null;   // D73: the commit of the harness code the run used, as it recorded it (run.json's
                                   // harness.git_commit; cap-baseline-1's manifest), abbreviated to its first 10 hex
                                   // characters (rule 9); null when it recorded none, one with uncommitted changes
                                   // ("-dirty"), or one on none of origin's branches (never pushed, so not on GitHub), or
                                   // when the 10 characters don't name that commit alone; always null when synthetic
  host: HostFacts;
  result: RunResult;               // from the densities it did test
  by_density: DensityBrief[];      // every density in the spec, in order
}

interface HostFacts {              // measured on the worker host
  instance_type: string; host_kind: HostKind;
  vcpus: number;                   // logical CPUs: the "host vCPUs" in density per host vCPU
  cores: number; threads_per_core: number; sockets: number;
  cpu_model: string; mem_gib: number; kernel_release: string;
  hypervisor_version: string; chromium_version: string;
}

interface RunResult {
  tested_successfully: number | null;  // rule 4; null if the lowest density failed or none was tested
  first_failed: number | null;
  gap: Range | null;                   // the integers strictly between the two, which weren't tested: [9, 11]
  not_tested: number[];                // listed densities no counting trial tested
  per_host_vcpu: number | null;
  midpoint_per_host_vcpu: number | null;
  cost_per_1000_tasks: CostRanges | null;
  limit: RunLimit | null;              // what limited it at first_failed; null if nothing failed
}

interface RunLimit {
  density: number;
  verdicts: Verdict[];                 // the union over its trials, most severe first
  trials_with_verdict: number; trials: number;
  missed: string[];                    // the criteria its trials missed, in words, most often missed first:
                                       // "the home step's median took 1,063–1,563 ms against 1,000 (3 of 3 trials)"
  separated_by?: { rule: RuleKey; passing: Range; failing: Range; threshold: number };
                                       // the rule whose values at the last passing and the first failing density
                                       // don't overlap, if one does
  host_consumer?: HostConsumer;        // the largest user of host CPU at first_failed
  guest_group?: GuestGroup;            // the largest share of CPU inside the microVMs
  guest_share?: Range;
}

interface DensityBrief {
  density: number;
  result: 'passed' | 'failed' | 'not_tested';
  passed: number;                      // counting trials that passed
  trials: number;                      // counting trials at it
  trial_results: boolean[];            // each counting trial's pass value, by trial number
}
```

## campaigns/\<campaign\>/runs/\<run\>.json

```ts
interface RunDoc extends RunEntry {
  schema: 'fleetkit-site-data/2';
  campaign: string;
  synthetic?: true;
  has: { boot_phases: boolean; per_microvm_cpu: boolean; guest_series: boolean; attribution: boolean; filmstrip: boolean };
  by_density: DensityResult[];
  trials: TrialSummary[];              // every published trial, in execution order
  tasks: { trial: string[]; microvm: number[]; product: string[]; ok: boolean[]; task_ms: (number | null)[];
           wall_ms: (number | null)[]; failed_step: (StepName | null)[]; failure_category: (string | null)[];
           timing_valid: boolean[]; img: (string | null)[] };
  steps: { trial: string[]; microvm: number[]; step: StepName[]; duration_ms: number[]; ok: boolean[] };
}

interface DensityResult extends DensityBrief {
  trial_ids: string[];                 // its counting trials, by number
  checks: { subject: Subject; stat: 'p50' | 'p95'; target_ms: number; range: Range; met: number }[];
  host: { cpu_util_pct: Range; cpu_pressure_pct: Range | null; busy_cores: Range; mem_used_gib: Range } | null;
  vcpus_allocated: number;             // derived: density × microVM vCPUs
  mem_allocated_gib: number;           // derived: density × (microVM memory + the hypervisor's allowance)
  verdicts: Verdict[];
  cost_per_1000_tasks?: CostRanges;    // passed densities only
}

interface TrialSummary {
  id: string; density: number;
  number: number | null;               // within its density; null for a labelled trial
  role: TrialRole;
  counts: boolean;                     // ladder and boundary
  order: number;                       // position in the run, from 1; orders the timeline, never shown as a number
  passed: boolean | null;              // null when it doesn't count
  at_limit: boolean;                   // passed while an attribution rule fired
  failed: string[];                    // the criteria it failed, in words: "home p50 1,279 ms > 1,000 ms"
  ready: { microvms: number; all_ready_ms: number | null; limit_ms: number; met: boolean };
  tasks: { ok: number; of: number };
  checks: { subject: Subject; stat: 'p50' | 'p95'; value_ms: number; target_ms: number; met: boolean }[];
  clean: boolean;
  marks: TrialMarks;
  attribution: TrialAttribution | null;
  host: { cpu_util_pct: number; cpu_pressure_pct: number | null; busy_cores: number;
          mem_used_gib: number; steal_pct: number | null } | null;
  microvm_mem_peak_mib: Range | null;
  cost_per_1000_tasks: { execution: number; observed: number } | null;   // counting trials only
  excluded_because?: string;           // a labelled trial: why it isn't judged
}

interface TrialMarks { all_ready_ms: number | null; release_ms: number | null; last_return_ms: number | null;
                       clean_ms: number | null; end_ms: number }

interface TrialAttribution {
  window_ms: Range;                    // the task window: release to last return
  verdicts: Verdict[];
  rules: { key: RuleKey; value: number | null; fired: boolean }[];
  host_cpu_s: Record<HostConsumer, number> & { busy: number };
  guest_share: Record<GuestGroup, number> | null;
  missing: RuleKey[];
}
```

## campaigns/\<campaign\>/runs/\<run\>/\<trial\>.json

```ts
interface TrialDoc {
  schema: 'fleetkit-site-data/2';
  campaign: string; run: string; id: string;
  synthetic?: true;
  density: number; number: number | null; role: TrialRole;
  window_ms: Range;                    // the span drawn: settle start to clean + 1,000
  marks: TrialMarks;
  settle?: { seconds: number; cpu_util_mean_pct: number };
  full_size_screenshots: boolean;      // rule 10: its screenshots (final screens, filmstrip) have full-size copies;
                                       // if false, its page shows their thumbnails with no full-size link
  microvms: {
    index: number; product: string; outcome: MicroVMOutcome; ready_ms: number | null;
    boot?: { process_started_ms: number; kernel_start_ms: number; guestd_start_ms: number;
             chromium_launch_ms: number; chromium_ready_ms: number };
    task?: { dispatch_ms: number; return_ms: number; task_ms: number; wall_ms: number; ok: boolean;
             failed_step?: StepName; failure_category?: string; bytes: number; requests: number;
             chromium_rss_mib: number; clock_offset_ms: number; timing_valid: boolean };
    steps: { name: StepName; start_ms: number; end_ms: number; ok: boolean; bytes?: number; requests?: number }[];
    destroy: { start_ms: number; end_ms: number } | null;
    img?: string;
  }[];
  series: {
    host: { t_ms: number[]; cpu_util_pct: number[]; mem_used_gib: number[]; cpu_pressure_pct?: number[];
            mem_pressure_pct?: number[]; io_pressure_pct?: number[]; steal_pct?: number[];
            cores?: Record<HostConsumer, number[]> };
    microvms?: { index: number; t_ms: number[]; vcpu_cores: number[]; hypervisor_cores: number[];
                 throttled_fraction: number[]; mem_mib: number[] }[];
    guest?: ({ index: number; t_ms: number[] } & Record<GuestGroup, number[]>)[];
  };
  limit: { verdicts: Verdict[]; microvms: { index: number; vcpu_s: number; hypervisor_s: number;
           throttled_fraction: number; cpu_pressure_pct: number | null; mem_peak_mib: number }[] };
  filmstrip?: { microvm: number; frames: { step: StepName; img: string }[] };   // the illustration trial; untimed
}
```

## Rules

The builder applies these rules, and `contract.ts` checks them on every document. A document that breaks one is refused, not repaired.

1. **A trial passes** if it meets every criterion: every microVM ready in time, every task succeeded, every latency check met, and nothing left on the host. Only trials that count (`ladder`, `boundary`) have a `passed` value.
2. **Trial numbers** at each density run 1, 2, 3… in execution order with no gaps.
3. **A density passes** if at least one trial ran at it and every trial at it passed. It's `not_tested` if no counting trial ran at it, and `failed` otherwise.
4. **A run's result** (`tested_successfully`) is the highest listed density that passed with every lower listed density passing too; it's never called a maximum. `first_failed` is the lowest density that failed; `gap` the integers strictly between the two; `not_tested` the listed densities no counting trial reached. A run that stopped early has a result from the densities it did test.
5. **Cost** per 1,000 tasks = 1,000 × price per hour ÷ 3,600 × window seconds ÷ density. The **execution** window runs from release to last return, the **observed** window from the trial's start to a clean host. Cost is given only at densities that passed, at the assumed price for the instance type.
6. **Comparing specs (D52, D60):** `per_host_vcpu` = `tested_successfully` ÷ host vCPUs. A replica's span runs from `tested_successfully` (0 if nothing passed) to `first_failed`, each ÷ host vCPUs, and its midpoint is the middle of that span; with no failure there is no midpoint, and the result reads "at least". A spec's midpoint is the mean of its replicas' midpoints, and null if any replica has none. Specs are compared by their midpoints, with every replica's span shown beside them: on Results and Compare, each replica is a bar to its span's end and the spec's midpoint a tick across its bars. Where a spec's midpoint is null but some replicas have one, the tick is the mean of those replicas, and its label says how many have none ("1 replica stopped early"); the dataset's value stays null.
7. **Failures outside the experiment are never results (D63).** When a trial fails because of something the spec doesn't test (the support host overloaded, a harness error, an AWS problem), the harness sets it aside under the run's `ops/` directory, notes the cause in its operational log, and runs it again. The builder reads only the run's own trials, so a set-aside trial never appears and leaves no gap in the numbering. If no clean trial was possible at a density, that density and those above it are `not_tested`; the run isn't "stopped early". Smoke, case and fault trials test the harness and are never published either.
8. **Consistency:** `by_density` lists exactly the spec's densities; a run document's entry equals the campaign's; `outcomes` equal the runs' results; a complete campaign has specs × replicas runs, none stopped early.
9. **Links (D73):** `definition_path` is `experiments/campaigns/<campaign>.json`, or for a reconstructed definition the `preregistration` path, or null; `harness_commit` is 10 lowercase hex characters or null. Both are present (null, not absent) and always null in synthetic data, which has nothing on GitHub. **A commit is never published in full**, here or in `preregistration.commit`: only its first 10 hex characters, which GitHub resolves. A full 40-character id sometimes holds 12 digits in a row, which the scrubber would take for an account id and the repository's leak check refuses; 10 characters can't. The builder checks the full id (the run's records agree, it is a full id, `origin` has it) and that the 10 characters name it alone, then abbreviates.
10. **Full-size screenshots** are published only for the trials whose pages open them: the last pass, trial 1 at the run's `tested_successfully` (every trial there passed); the first failure, the first trial that failed at its `first_failed`, which Results opens at the limit (trial 1, unless it passed there); and every illustration trial (the filmstrip, which About shows too). Those trials have `full_size_screenshots: true`. Every other trial's screenshots are thumbnails only, and its page shows them with no full-size link, labelled "Thumbnails only". Every screenshot a document names has a thumbnail, and `img/` holds nothing else: no full-size copy that no such trial names. This keeps the dataset within its budget as campaigns accumulate.

## Links to the repository (D73)

Campaign, run and trial pages end with a **Source** row of links to what is already public on GitHub (`github.com/evandbrown/fleetkit`); the raw evidence under `results/` stays private. The dataset carries only paths and commits, never a URL; `src/lib/repo.ts` makes the links, each opening in a new tab. The builder links only what GitHub has, judged from the clone's remote-tracking branches: a definition must be on `origin/main` as launched, and a commit on one of `origin`'s branches; a file or commit that exists only locally is left null, with a note, rather than published as a broken link. Fetch before building.

| Link | Points to | On |
|---|---|---|
| Definition | `blob/main/<definition_path>` | every page of a campaign with a `definition_path` |
| Data | `blob/main/site/public/data/<the page's document>`: `campaign.json`, the run's or the trial's file | every page |
| Harness @ `<first 7 characters>` | `tree/<harness_commit>` (its 10 characters) | a run and its trials: the run's commit; a campaign: each distinct commit its runs used, in run order |

A synthetic campaign shows no Source row.

## What the builder never ships

Every output object is built field by field; nothing is copied through wholesale. Never shipped: instance, AMI and account ids; ARNs; IP addresses; hostnames and URLs; error texts (only `failure_category`); logs, spans, console output, cloud-init output; the driver's command line; image metadata (EXIF, ICC, XMP); anything about the support host; and set-aside trials and the operational log. A gate scans every published byte and fails the build on a match.

## Synthetic data

Test fixtures may contain made-up campaigns, marked in three ways so they can't be mistaken for results: `synthetic: true` on the index entry and every document, an id ending in `-synthetic`, and a title starting with the word "Synthetic" ("Synthetic host sizes"). The site shows a banner on every page of a synthetic campaign. `npm run build` fails if any file under `public/data/` contains `"synthetic": true`. Fixtures live only in `tests/fixtures/data/`, served by `npm run dev:fixtures` and by the end-to-end tests' preview server; neither copies them into `public/` or `dist/`.

## Builder handoff

The experiment builder starts a new campaign from a published one through the URL, so a link can be reloaded or shared:

| Link | Opens the builder with |
|---|---|
| `#/builder?from=campaign:<campaign>` | that campaign's `definition`, from `data/campaigns/<campaign>/campaign.json` |
| `#/builder?from=run:<campaign>/<run>` | a campaign of one spec: the run's resolved spec (`specs[].spec` for the run's `spec`) as the base, and one named spec `{}` |

The router parses these into `{ name: 'builder', from: 'campaign:nested-sizes-1' }` (or `null`), and `Builder.svelte` reads it from `nav.route` (`src/lib/nav.svelte.ts`); it takes no props. The builder fetches the campaign document itself with `loadCampaign` (`src/lib/data.ts`). A reconstructed definition (cap-baseline-1) has no `shutdown_after_minutes`: the builder uses the default. Campaign and run pages link here as "New campaign from this".

## Routes

Hash routes, so every URL stays relative under `/fleetkit/`. `src/lib/router.ts` parses them; `nav.route` (`src/lib/nav.svelte.ts`) holds the current one.

| Address | Route | Page |
|---|---|---|
| `#/` | `{ name: 'about' }` | About, the home page (D72) |
| `#/results` | `{ name: 'results', campaign: null }` | Results, on the featured campaign (D58) |
| `#/results/<campaign>` | `{ name: 'results', campaign }` | Results, on that campaign |
| `#/results/<campaign>/runs/<run>?density=N` | `{ name: 'run', campaign, run, density }` | one run; `density` optional |
| `#/results/<campaign>/runs/<run>/trials/<trial>?microvm=N` | `{ name: 'trial', campaign, run, trial, microvm }` | one trial; `microvm` optional |
| `#/results/compare?specs=<campaign>/<spec>,...` | `{ name: 'compare', specs }` | Compare specs (D61); `specs` null when absent |
| `#/builder?from=...` | `{ name: 'builder', from }` | the builder (Builder handoff, above) |

`compare` is reserved: `#/results/compare` is never read as a campaign, so no campaign may be named `compare`.

**Links.** Build every link with `href(link)`; never write an address by hand. The home page is `href({ name: 'about' })` (`#/`), and Results on the featured campaign is `href({ name: 'results', campaign: null })` (`#/results`). `area(route)` gives the navigation item a route belongs to (`about`, `results` or `builder`); the navigation reads About | Results | Builder, and the wordmark goes home.

**Older addresses.** `#/about`, `#/campaigns`, `#/campaigns/<campaign>` and its runs and trials, `#/compare?specs=...` and `#/method[/<anchor>]` open their new page at once, and the address bar is rewritten to the new address without a history entry (`resolve(hash)` returns the route and the address to show).
