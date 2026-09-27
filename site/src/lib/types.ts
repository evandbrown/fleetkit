// The dataset contract. A copy of the types in DATA.md; when the two disagree, DATA.md wins.

export const SCHEMA = 'fleetkit-site-data/1' as const;

// ---- shared -------------------------------------------------------------------------------------

export type Range = [number, number];
export const STEP_NAMES = ['home', 'search', 'open_product', 'add_to_cart', 'verify_cart'] as const;
export type StepName = (typeof STEP_NAMES)[number];
export type Subject = StepName | 'task';
export type HostKind = 'nested' | 'metal';
export type Hypervisor = 'firecracker' | 'cloud-hypervisor';
export type TrialRole = 'ladder' | 'boundary' | 'warmup' | 'illustration' | 'fault';
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
export type Cls = 'measured' | 'derived' | 'modelled' | 'assumed' | 'rule';
export type TermKey =
  | 'campaign' | 'run' | 'trial' | 'microvm' | 'density' | 'spec' | 'replica' | 'worker_host' | 'host_kind'
  | 'hypervisor' | 'support_host' | 'tested_successfully' | 'p50' | 'p95' | 'cpu_pressure' | 'allocated';
export type Seg = { t: string } | { term: TermKey; t?: string } | { v: string; cls: Cls; src: string };

// ---- index.json ---------------------------------------------------------------------------------

export interface Index {
  schema: typeof SCHEMA;
  latest: string | null;
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
  specs: { name: string; label: string }[];
  runs: number;
  outcomes: SpecOutcome[];
}

export interface SpecOutcome {
  spec: string;
  runs: string[];
  tested_successfully: (number | null)[];
  per_host_vcpu: (number | null)[];
  cost_per_1000_tasks: ({ execution: Range; observed: Range } | null)[];
}

// ---- campaign.json ------------------------------------------------------------------------------

export type Spec = Record<string, unknown>;

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
  provenance: 'recorded' | 'reconstructed';
  preregistration?: { path: string; commit: string };
  definition: CampaignDefinition;
  spec_fields: SpecField[];
  specs: SpecDoc[];
  runs: RunEntry[];
  outcomes: SpecOutcome[];
  answer?: Seg[];
  does_not_show?: Seg[][];
  reading?: string;
  next?: NextChange[];
}

export interface CampaignDefinition {
  format: string;
  id: string;
  question: string;
  base: Spec;
  specs: { name: string; label: string; changes: Record<string, unknown> }[];
  replicas: number;
}

export interface SpecField {
  path: string;
  label: string;
  group: string;
  unit?: string;
  note?: string;
}

export interface SpecDoc {
  name: string;
  label: string;
  changes: { path: string; base: unknown; value: unknown }[];
  spec: Spec;
  instance_type: string;
  host_kind: HostKind;
  hypervisor: Hypervisor;
  microvm: { vcpus: number; mem_mib: number; mem_overhead_mib: number };
  densities: number[];
  boundary_trials: number;
  criteria: Criteria;
  rules: RuleDef[];
  price_usd_per_hour: number;
}

export interface Criteria {
  step_p50_ms: number;
  step_p95_ms: number;
  task_p95_ms: number;
  ready_limit_s: number;
}

export interface RuleDef {
  key: RuleKey;
  label: string;
  verdict: Verdict;
  op: '>=' | '<';
  threshold: number;
}

export interface NextChange {
  text: string;
  motivated_by: string;
  changes: Record<string, unknown>;
}

export interface RunEntry {
  id: string;
  spec: string;
  replica: number;
  status: 'complete' | 'incomplete';
  note?: string;
  started: string;
  duration_s: number;
  host: HostFacts;
  result: RunResult | null;
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
  not_tried: Range | null;
  not_run: number[];
  per_host_vcpu: number | null;
  vcpus_allocated_per_host_vcpu: number | null;
  cost_per_1000_tasks: CostRanges | null;
  limit: RunLimit | null;
}

export interface CostRanges {
  execution: Range;
  observed: Range;
  fixture: Range;
}

export interface RunLimit {
  density: number;
  verdicts: Verdict[];
  trials_with_verdict: number;
  trials: number;
  separated_by?: { rule: RuleKey; passing: Range; failing: Range; threshold: number };
  host_consumer: HostConsumer;
  guest_group: GuestGroup;
  guest_share: Range;
}

export interface DensityBrief {
  density: number;
  result: 'passed' | 'failed' | 'not_run';
  passed: number;
  trials: number;
}

// ---- runs/<run>.json ----------------------------------------------------------------------------

export interface RunDoc extends RunEntry {
  schema: typeof SCHEMA;
  campaign: string;
  synthetic?: true;
  code: { commit: string; dirty: boolean };
  has: Has;
  by_density: DensityResult[];
  trials: TrialSummary[];
  tasks: TaskColumns;
  steps: StepColumns;
  overview: { t_s: number[]; cpu_util_pct: number[]; bands: { trial: string; start_s: number; end_s: number }[] };
  support?: { t_s: number[]; cpu_util_pct: number[] };
  answer?: Seg[];
}

export interface Has {
  host_hz: 1 | 5;
  boot_phases: boolean;
  per_microvm_cpu: boolean;
  guest_series: boolean;
  attribution: boolean;
  cost: boolean;
  filmstrip: boolean;
  fixture_rtt: boolean;
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
  cost_per_1000_tasks: { execution: number; observed: number; fixture: number } | null;
  excluded_because?: string;
  fault?: string;
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
  microvms: MicroVMLane[];
  series: TrialSeries;
  limit: TrialLimit;
  fault?: { microvm: number; at_ms: number; label: string };
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
  fixture_rtt?: { t_ms: number[]; ms: number[] };
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
