// The glossary (D56, docs/method.md), for term popovers and labels.
import type { GuestGroup, HostConsumer, RuleKey, Subject, TermKey, TrialRole, Verdict } from './types';

export const TERMS: Record<TermKey, { label: string; text: string }> = {
  campaign: { label: 'campaign', text: 'A named set of runs launched together to answer one question.' },
  run: { label: 'run', text: 'One worker host carrying out one spec, start to finish.' },
  trial: {
    label: 'trial',
    text: "Start N fresh microVMs at the same moment, run one task in each, destroy them. N is the trial's density.",
  },
  microvm: { label: 'microVM', text: 'One of the N in a trial. It runs one headless Chromium, which runs one shopping task of five steps.' },
  density: {
    label: 'density',
    text: 'How many microVMs a trial starts at once. The spec lists the densities to test; each trial records its own.',
  },
  spec: { label: 'spec', text: 'Everything fixed about one run, including the list of densities to test.' },
  replica: { label: 'replica', text: 'Another run of the same spec, on its own worker host.' },
  worker_host: { label: 'worker host', text: 'The EC2 instance whose capacity is measured.' },
  host_kind: {
    label: 'host kind',
    text: 'Metal: microVMs run directly on the hardware. Nested: microVMs run inside an ordinary EC2 instance through nested virtualization.',
  },
  hypervisor: { label: 'hypervisor', text: 'The program on the worker host that runs each microVM: Firecracker or Cloud Hypervisor.' },
  support_host: {
    label: 'support host',
    text: "A separate instance that serves the test shopping site and collects telemetry, so neither competes for the worker host's CPU.",
  },
  tested_successfully: {
    label: 'tested successfully',
    text: 'The highest density that passed with every lower density passing too. Densities between it and the first failure were not tried, so it is never a maximum.',
  },
  p50: { label: 'p50', text: 'The median over the tasks in a trial.' },
  p95: { label: 'p95', text: 'Over at most a few dozen tasks, p95 is effectively the slowest task.' },
  cpu_pressure: {
    label: 'CPU pressure',
    text: 'The share of time some task on the host was waiting for a CPU (Linux PSI, cpu some).',
  },
  allocated: { label: 'allocated', text: "What the microVMs were given, not what they used: density × each microVM's size." },
};

export const ROLE_LABEL: Record<TrialRole, string> = {
  ladder: 'first trial at its density',
  boundary: 'boundary check',
  warmup: 'warm-up',
  illustration: 'illustration',
  fault: 'fault',
};

export const VERDICT_LABEL: Record<Verdict, string> = {
  host_cpu: 'host CPU',
  microvm_cpu_allowance: "the microVM's CPU allowance",
  host_memory: 'host memory',
  io: 'disk and network IO',
  steal: 'CPU taken by the outer hypervisor',
  none: 'no rule fired',
  unknown: 'not recorded',
};

export const RULE_LABEL: Record<RuleKey, string> = {
  host_cpu_pressure_pct: 'Host CPU pressure',
  host_cpu_util_pct: 'Host CPU utilisation',
  microvm_throttled_fraction: 'MicroVM throttled fraction',
  host_mem_available_fraction: 'Host memory available',
  host_mem_pressure_pct: 'Host memory pressure',
  host_io_pressure_pct: 'Host IO pressure',
  host_steal_pct: 'Steal',
};

export const HOST_CONSUMER_LABEL: Record<HostConsumer, string> = {
  microvm_vcpus: "microVMs' vCPUs",
  hypervisor: "the hypervisor's own threads",
  hostd: 'host daemon',
  driver: 'driver',
  unattributed: 'unattributed',
};

export const GUEST_GROUP_LABEL: Record<GuestGroup, string> = {
  renderer: 'Chromium renderer',
  browser: 'Chromium browser process',
  other_chromium: 'other Chromium processes',
  guestd: 'guest daemon',
  other: 'kernel and other',
};

export const SUBJECT_LABEL: Record<Subject, string> = {
  home: 'home',
  search: 'search',
  open_product: 'open product',
  add_to_cart: 'add to cart',
  verify_cart: 'verify cart',
  task: 'whole task',
};

/** "trial 2 at density 8", "warm-up at density 1". */
export function trialLabel(t: { number: number | null; density: number; role: TrialRole; id: string }): string {
  if (t.number !== null) return `trial ${t.number} at density ${t.density}`;
  const extra = t.id.match(/-(\d+)$/);
  return `${ROLE_LABEL[t.role]}${extra ? ` (${extra[1]})` : ''} at density ${t.density}`;
}
