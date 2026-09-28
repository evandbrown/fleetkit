// The dataset contract. A copy of the types in DATA.md; when the two disagree, DATA.md wins.

export const SCHEMA = 'fleetkit-site-data/2' as const;

// ---- shared -------------------------------------------------------------------------------------

export type Range = [number, number];
export const STEP_NAMES = ['home', 'search', 'open_product', 'add_to_cart', 'verify_cart'] as const;
export type StepName = (typeof STEP_NAMES)[number];
export type Subject = StepName | 'task';
export type HostKind = 'nested' | 'metal';
export type Hypervisor = 'firecracker' | 'cloud-hypervisor';
export type TrialRole = 'ladder' | 'boundary' | 'warmup' | 'illustration';
export const COUNTING_ROLES: readonly TrialRole[] = ['ladder', 'boundary'];
export const VERDICTS = [
  'host_cpu', 'microvm_cpu_allowance', 'host_memory', 'io', 'steal', 'none', 'unknown',
] as const;
export type Verdict = (typeof VERDICTS)[number];
export const RULE_KEYS = [
  'host_cpu_pressure_pct', 'host_cpu_util_pct', 'microvm_throttled_fraction',
  'host_mem_available_fraction', 'host_mem_pressure_pct', 'host_io_pressure_pct', 'host_steal_pct',
] as const;
export type RuleKey = (typeof RULE_KEYS)[number];
export const HOST_CONSUMERS = ['microvm_vcpus', 'hypervisor', 'hostd', 'driver', 'unattributed'] as const;
export type HostConsumer = (typeof HOST_CONSUMERS)[number];
export const GUEST_GROUPS = ['renderer', 'browser', 'other_chromium', 'guestd', 'other'] as const;
export type GuestGroup = (typeof GUEST_GROUPS)[number];
export type MicroVMOutcome =
  | 'completed' | 'startup_timeout' | 'startup_error' | 'lifetime_expired' | 'idle_expired' | 'task_failure_destroyed';

/** A spec, exactly as experiments/schema/spec.schema.json defines it. */
export interface Spec {
  worker_host: { instance_type: string };
  hypervisor: { name: Hypervisor; virtio_transport: 'mmio' | 'pci'; virtio_rng: boolean };
  /** console and memory_pages are optional: specs recorded before them leave them out, and absent is verbose and 4k. */
  microvm: { vcpus: number; memory_mib: number; console?: 'verbose' | 'quiet' | 'quiet-i8042'; memory_pages?: '4k' | 'thp' };
  densities: number[];
  criteria: {
    step_p50_target_ms: number;
    step_p95_target_ms: number;
    task_p95_target_ms: number;
    ready_timeout_s: number;
    step_timeout_ms: number;
    task_timeout_ms: number;
  };
  /** release_after_ready_s is optional: specs recorded before it existed leave it out, and absent is 0. */
  procedure: { trials_per_density: number; boundary_trials: number; settle_s: number; release_after_ready_s?: number };
  support_host: { instance_type: string };
  /** Optional: specs recorded before it existed leave it out, and absent is no extra Chromium flags. */
  workload?: { chromium_extra_flags?: string[] };
}

/** A spec leaf's value, as `flatten` gives it: a scalar, or a list (densities, workload.chromium_extra_flags), which is
 * one value, compared item by item in order. */
export type SpecValue = string | number | boolean | number[] | string[];

/** Where a named spec differs from its campaign's base (DATA.md, SpecDoc.changes). An optional field the base leaves
 * out is at its default: `base` is then the default ([] for no extra Chromium flags). */
export interface SpecChange {
  path: string;
  base: SpecValue;
  value: SpecValue;
}

export type DeepPartial<T> = { [K in keyof T]?: T[K] extends object ? (T[K] extends unknown[] ? T[K] : DeepPartial<T[K]>) : T[K] };

// ---- index.json ---------------------------------------------------------------------------------

export interface Index {
  schema: typeof SCHEMA;
  featured: string | null;
  campaigns: CampaignEntry[];
}

export interface CampaignEntry {
  id: string;
  title: string;
  question: string;
  started: string;
  status: 'complete' | 'partial';
  synthetic?: true;
  before_campaigns?: true;
  replicas: number;
  /** One or two plain sentences from the catalog: the campaign's answer, with its figures (D93). */
  answer?: string;
  /** From the catalog: the spec (by name) About's featured card leads with; else its best spec by midpoint. */
  featured_spec?: string;
  specs: { name: string; label: string }[];
  runs: number;
  outcomes: SpecOutcome[];
}

export interface SpecOutcome {
  spec: string;
  replicas: ReplicaResult[];
  midpoint_per_host_vcpu: number | null;
}

export interface ReplicaResult {
  run: string;
  host_vcpus: number;
  tested_successfully: number | null;
  first_failed: number | null;
  stopped_early: boolean;
  per_host_vcpu: number | null;
  midpoint_per_host_vcpu: number | null;
  cost_per_1000_tasks: CostRanges | null;
}

/** USD per 1,000 tasks, each the range over the trials at a density (DATA.md, rule 5). */
export interface CostRanges {
  execution: Range;
  observed: Range;
  /** The burst, minus a warm start's wait plus the CPU used in it: what one burst is charged. */
  observed_charged: Range;
  /** What a full fleet pays per task (D81); null where the host wasn't full or the samples fall short. */
  steady_state: Range | null;
}

/** One counting trial's costs (DATA.md, rule 5) and the steady-state model's inputs. */
export interface TrialCost {
  execution: number;
  observed: number;
  observed_charged: number;
  steady_state: number | null;
  /** vCPU-seconds of host CPU per task over the microVMs' lives; null when the samples don't cover them. */
  host_cpu_per_task_s: number | null;
  /** Host CPU busy over the task window, 0 to 1; null without host attribution. */
  host_busy_fraction: number | null;
}

// ---- campaign.json ------------------------------------------------------------------------------

export interface CampaignDoc {
  schema: typeof SCHEMA;
  id: string;
  title: string;
  question: string;
  started: string;
  ended: string;
  status: 'complete' | 'partial';
  synthetic?: true;
  before_campaigns?: true;
  reconstructed?: true;
  /** The document that fixed the criteria first, and the commit that added it (10 characters). */
  preregistration?: { path: string; commit: string };
  /** D73: the definition's path in the repository, experiments/campaigns/<campaign>.json; for a reconstructed
   *  definition, its pre-registration's; null when neither is public. */
  definition_path: string | null;
  /** The catalog's answer, as on the index entry (D93). */
  answer?: string;
  definition: CampaignDefinition;
  specs: SpecDoc[];
  runs: RunEntry[];
  outcomes: SpecOutcome[];
  rules: RuleDef[];
  notes?: string[];
}

export interface CampaignDefinition {
  name: string;
  question: string;
  replicas: number;
  shutdown_after_minutes?: number;
  base: Spec;
  specs: Record<string, DeepPartial<Spec>>;
  why?: Record<string, string>;
}

export interface SpecDoc {
  name: string;
  label: string;
  /** The catalog's short name for the spec, at most four words ("Cloud Hypervisor", "quiet console"; D93). */
  short?: string;
  /** Why it is in the campaign: the definition's reason, or the catalog's rewording of it (D93). */
  why?: string;
  changes: SpecChange[];
  spec: Spec;
  host: {
    instance_type: string;
    host_kind: HostKind;
    vcpus: number;
    memory_gib: number;
    price_usd_per_hour: number;
    price_estimated: boolean;
  };
}

export interface RuleDef {
  key: RuleKey;
  label: string;
  verdict: Verdict;
  op: '>=' | '<';
  threshold: number;
}

export interface RunEntry {
  id: string;
  spec: string;
  replica: number;
  stopped_early: boolean;
  started: string;
  duration_s: number;
  /** D73: the commit of the harness code the run used, abbreviated to its first 10 hex characters; null when it
   *  recorded none, one with uncommitted changes, or one GitHub doesn't have. */
  harness_commit: string | null;
  host: HostFacts;
  result: RunResult;
  by_density: DensityBrief[];
}

export interface HostFacts {
  instance_type: string;
  host_kind: HostKind;
  vcpus: number;
  cores: number;
  threads_per_core: number;
  sockets: number;
  cpu_model: string;
  mem_gib: number;
  kernel_release: string;
  hypervisor_version: string;
  chromium_version: string;
}

export interface RunResult {
  tested_successfully: number | null;
  first_failed: number | null;
  gap: Range | null;
  not_tested: number[];
  per_host_vcpu: number | null;
  midpoint_per_host_vcpu: number | null;
  cost_per_1000_tasks: CostRanges | null;
  limit: RunLimit | null;
}

export interface RunLimit {
  density: number;
  verdicts: Verdict[];
  trials_with_verdict: number;
  trials: number;
  missed: string[];
  separated_by?: { rule: RuleKey; passing: Range; failing: Range; threshold: number };
  host_consumer?: HostConsumer;
  guest_group?: GuestGroup;
  guest_share?: Range;
}

export interface DensityBrief {
  density: number;
  result: 'passed' | 'failed' | 'not_tested';
  passed: number;
  trials: number;
  trial_results: boolean[];
}

// ---- runs/<run>.json ----------------------------------------------------------------------------

export interface RunDoc extends RunEntry {
  schema: typeof SCHEMA;
  campaign: string;
  synthetic?: true;
  has: Has;
  by_density: DensityResult[];
  trials: TrialSummary[];
  tasks: TaskColumns;
  steps: StepColumns;
}

export interface Has {
  boot_phases: boolean;
  per_microvm_cpu: boolean;
  guest_series: boolean;
  attribution: boolean;
  filmstrip: boolean;
}

export interface DensityResult extends DensityBrief {
  trial_ids: string[];
  checks: { subject: Subject; stat: 'p50' | 'p95'; target_ms: number; range: Range; met: number }[];
  host: { cpu_util_pct: Range; cpu_pressure_pct: Range | null; busy_cores: Range; mem_used_gib: Range } | null;
  vcpus_allocated: number;
  mem_allocated_gib: number;
  verdicts: Verdict[];
  cost_per_1000_tasks?: CostRanges;
}

export interface TrialSummary {
  id: string;
  density: number;
  number: number | null;
  role: TrialRole;
  counts: boolean;
  order: number;
  passed: boolean | null;
  at_limit: boolean;
  failed: string[];
  ready: { microvms: number; all_ready_ms: number | null; limit_ms: number; met: boolean };
  tasks: { ok: number; of: number };
  checks: Check[];
  clean: boolean;
  marks: TrialMarks;
  attribution: TrialAttribution | null;
  host: {
    cpu_util_pct: number;
    cpu_pressure_pct: number | null;
    busy_cores: number;
    mem_used_gib: number;
    steal_pct: number | null;
  } | null;
  microvm_mem_peak_mib: Range | null;
  cost_per_1000_tasks: TrialCost | null;
  excluded_because?: string;
}

export interface Check {
  subject: Subject;
  stat: 'p50' | 'p95';
  value_ms: number;
  target_ms: number;
  met: boolean;
}

export interface TrialMarks {
  all_ready_ms: number | null;
  release_ms: number | null;
  last_return_ms: number | null;
  clean_ms: number | null;
  end_ms: number;
}

export interface TrialAttribution {
  window_ms: Range;
  verdicts: Verdict[];
  rules: { key: RuleKey; value: number | null; fired: boolean }[];
  host_cpu_s: Record<HostConsumer, number> & { busy: number };
  guest_share: Record<GuestGroup, number> | null;
  missing: RuleKey[];
}

export interface TaskColumns {
  trial: string[];
  microvm: number[];
  product: string[];
  ok: boolean[];
  task_ms: (number | null)[];
  wall_ms: (number | null)[];
  failed_step: (StepName | null)[];
  failure_category: (string | null)[];
  timing_valid: boolean[];
  img: (string | null)[];
}

export interface StepColumns {
  trial: string[];
  microvm: number[];
  step: StepName[];
  duration_ms: number[];
  ok: boolean[];
}

// ---- runs/<run>/<trial>.json --------------------------------------------------------------------

export interface TrialDoc {
  schema: typeof SCHEMA;
  campaign: string;
  run: string;
  id: string;
  synthetic?: true;
  density: number;
  number: number | null;
  role: TrialRole;
  window_ms: Range;
  marks: TrialMarks;
  settle?: { seconds: number; cpu_util_mean_pct: number };
  /** Rule 10: its screenshots have full-size copies; if false, its page shows thumbnails with no full-size link. */
  full_size_screenshots: boolean;
  microvms: MicroVMLane[];
  series: TrialSeries;
  limit: TrialLimit;
  filmstrip?: { microvm: number; frames: { step: StepName; img: string }[] };
}

export interface MicroVMLane {
  index: number;
  product: string;
  outcome: MicroVMOutcome;
  ready_ms: number | null;
  boot?: {
    process_started_ms: number;
    kernel_start_ms: number;
    guestd_start_ms: number;
    chromium_launch_ms: number;
    chromium_ready_ms: number;
  };
  task?: {
    dispatch_ms: number;
    return_ms: number;
    task_ms: number;
    wall_ms: number;
    ok: boolean;
    failed_step?: StepName;
    failure_category?: string;
    bytes: number;
    requests: number;
    chromium_rss_mib: number;
    clock_offset_ms: number;
    timing_valid: boolean;
  };
  steps: { name: StepName; start_ms: number; end_ms: number; ok: boolean; bytes?: number; requests?: number }[];
  destroy: { start_ms: number; end_ms: number } | null;
  img?: string;
}

export interface TrialSeries {
  host: {
    t_ms: number[];
    cpu_util_pct: number[];
    mem_used_gib: number[];
    cpu_pressure_pct?: number[];
    mem_pressure_pct?: number[];
    io_pressure_pct?: number[];
    steal_pct?: number[];
    cores?: Record<HostConsumer, number[]>;
  };
  microvms?: {
    index: number;
    t_ms: number[];
    vcpu_cores: number[];
    hypervisor_cores: number[];
    throttled_fraction: number[];
    mem_mib: number[];
  }[];
  guest?: ({ index: number; t_ms: number[] } & Record<GuestGroup, number[]>)[];
}

export interface TrialLimit {
  verdicts: Verdict[];
  microvms: {
    index: number;
    vcpu_s: number;
    hypervisor_s: number;
    throttled_fraction: number;
    cpu_pressure_pct: number | null;
    mem_peak_mib: number;
  }[];
}
