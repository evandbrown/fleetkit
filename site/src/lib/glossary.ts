// The labels the pages use for the glossary's words (D56). About defines each word once.
import type { GuestGroup, HostConsumer, RuleKey, Subject, TrialRole, Verdict } from './types';

export const ROLE_LABEL: Record<TrialRole, string> = {
  ladder: 'first trial at its browser count',
  boundary: 'boundary check',
  warmup: 'warm-up',
  illustration: 'illustration',
};

/** The plain label for data deliberately missing or not judged (D62). */
export const NOT_JUDGED: Partial<Record<TrialRole, string>> = {
  warmup: 'warm-up, not judged',
  illustration: 'illustration, not judged',
};
export const NOT_TESTED = 'not tested';
export const STOPPED_EARLY = 'stopped early';

export const VERDICT_LABEL: Record<Verdict, string> = {
  host_cpu: 'host CPU',
  microvm_cpu_allowance: "the microVM's CPU allowance",
  host_memory: 'host memory',
  io: 'disk and network IO',
  steal: 'CPU taken by the outer hypervisor',
  none: 'no rule fired',
  unknown: 'not recorded',
};

/** A verdict as a stat's value: two or three words. */
export const VERDICT_SHORT: Record<Verdict, string> = {
  host_cpu: 'Host CPU',
  microvm_cpu_allowance: 'MicroVM CPU cap',
  host_memory: 'Host memory',
  io: 'Disk and network IO',
  steal: 'CPU steal',
  none: 'No rule fired',
  unknown: 'Not recorded',
};

export const RULE_LABEL: Record<RuleKey, string> = {
  host_cpu_pressure_pct: 'Host CPU pressure',
  host_cpu_util_pct: 'Host CPU utilization',
  microvm_throttled_fraction: 'MicroVM CPU throttled',
  host_mem_available_fraction: 'Host memory free',
  host_mem_pressure_pct: 'Host memory pressure',
  host_io_pressure_pct: 'Host IO pressure',
  host_steal_pct: 'CPU taken by outer hypervisor',
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

/** "8 browsers", "1 browser": how many browsers a trial runs on the host, each in its own microVM. */
export function browsers(n: number | string): string {
  return `${n} ${String(n) === '1' ? 'browser' : 'browsers'}`;
}

/** "trial 2 at 8 browsers", "warm-up at 1 browser". */
export function trialLabel(t: { number: number | null; density: number; role: TrialRole; id: string }): string {
  if (t.number !== null) return `trial ${t.number} at ${browsers(t.density)}`;
  const extra = t.id.match(/-(\d+)$/);
  return `${ROLE_LABEL[t.role]}${extra ? ` (${extra[1]})` : ''} at ${browsers(t.density)}`;
}
