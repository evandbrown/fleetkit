// The topline figure (D102): one cost per spec, and which spec a campaign leads with. Pure functions over the
// index's entries, so the picker and About's card need no other document.
//
// A spec's cost is the middle of each replica's steady-state range (D81), then the median across replicas: it sits
// inside every replica's range, one odd replica doesn't move it, and it is the statistic the specs are ranked by, so
// the spec crowned and the number shown agree. It is shown as "≈ $0.043", the ≈ saying estimate. Where no replica's
// host was full there is no steady-state figure, and the burst cost is not put in its place: it is a different,
// higher quantity that would be read against fleet costs.
import * as f from './format';
import type { Range, ReplicaResult, SpecOutcome } from './types';

/** What the helpers need: the index's entry or the campaign's own document, both carry it. */
export type CostSource = { specs: { name: string; label: string }[]; outcomes: SpecOutcome[]; featured_spec?: string };

export interface SpecCost {
  spec: string;
  label: string;
  /** The median of the replicas' range middles, USD per 1,000 tasks; null with no steady-state figure. */
  cost: number | null;
  /** Each replica's middle, for the winner rule. */
  middles: number[];
  /** The count of browsers per host most replicas held ("6", "≥ 192"); null when nothing ran or passed. */
  count: string | null;
  /** That count as a number, for a scale; null when nothing ran or passed. */
  countN: number | null;
  /** That count per host vCPU (a 6 on a 4-vCPU host is 1.5), so hosts of different sizes sit on one scale. */
  perVcpu: number | null;
  hostVcpus: number | null;
  /** The steady-state range joined over the replicas that have one, USD per 1,000 tasks; null with none. */
  steady: Range | null;
  /** What one burst is charged, joined over every replica with a cost; null with none. */
  burst: Range | null;
  /** How many replicas ran. */
  replicas: number;
  /** Ran, but no replica's host was full, so there is no fleet cost. */
  noFleet: boolean;
  ran: boolean;
}

export interface CampaignCost {
  /** Every spec, cheapest first; specs without a cost after them in the entry's order. */
  specs: SpecCost[];
  /** The spec the campaign leads with: the catalog's featured spec when it has a cost, else the cheapest. */
  lead: SpecCost | null;
  /** Whether the lead is a clear winner: every replica middle of it below every one of the runner-up's. */
  clear: boolean;
  /** How many specs have a cost. */
  costed: number;
}

const median = (xs: number[]): number => {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

/** The count most replicas held; on a tie, the lower. A floor ("≥") when no replica reached a failure. */
function modeCount(reps: ReplicaResult[]): { n: number; floor: boolean } | null {
  const counts = reps.map((r) => r.tested_successfully).filter((v): v is number => v !== null);
  if (!counts.length) return null;
  const tally = new Map<number, number>();
  for (const c of counts) tally.set(c, (tally.get(c) ?? 0) + 1);
  const n = [...tally.entries()].sort((a, b) => b[1] - a[1] || a[0] - b[0])[0][0];
  return { n, floor: reps.every((r) => r.first_failed === null) };
}

/** [min of the lows, max of the highs] over ranges; null with none. */
const join = (rs: Range[]): Range | null => (rs.length ? [Math.min(...rs.map((r) => r[0])), Math.max(...rs.map((r) => r[1]))] : null);

export function specCost(e: CostSource, spec: string): SpecCost {
  const s = e.specs.find((x) => x.name === spec);
  const reps = e.outcomes.find((o) => o.spec === spec)?.replicas ?? [];
  const steadies = reps.map((r) => r.cost_per_1000_tasks?.steady_state ?? null).filter((c): c is Range => c !== null);
  const middles = steadies.map(([lo, hi]) => (lo + hi) / 2);
  const mode = modeCount(reps);
  const hostVcpus = reps[0]?.host_vcpus ?? null;
  return {
    spec,
    label: s?.label ?? spec,
    cost: middles.length ? median(middles) : null,
    middles,
    count: mode ? `${mode.floor ? '≥ ' : ''}${f.num(mode.n)}` : null,
    countN: mode?.n ?? null,
    perVcpu: mode && hostVcpus ? mode.n / hostVcpus : null,
    hostVcpus,
    steady: join(steadies),
    burst: join(reps.map((r) => r.cost_per_1000_tasks?.observed_charged ?? null).filter((c): c is Range => c !== null)),
    replicas: reps.length,
    noFleet: reps.length > 0 && !middles.length,
    ran: reps.length > 0,
  };
}

export function campaignCost(e: CostSource): CampaignCost {
  const all = e.specs.map((s) => specCost(e, s.name));
  const costed = all.filter((s) => s.cost !== null).sort((a, b) => a.cost! - b.cost!);
  const rest = all.filter((s) => s.cost === null);
  const featured = e.featured_spec ? costed.find((s) => s.spec === e.featured_spec) : undefined;
  const lead = featured ?? costed[0] ?? null;
  const runnerUp = lead ? costed.find((s) => s.spec !== lead.spec) : undefined;
  const clear = !!lead && (!runnerUp || Math.max(...lead.middles) < Math.min(...runnerUp.middles));
  return { specs: [...costed, ...rest], lead, clear, costed: costed.length };
}

/** "$0.043": a cost to three decimals under a dollar, as the site writes costs. */
export const costText = (v: number) => f.usd(v);

/**
 * The full ranges behind a spec's one number, for a tooltip on the figure: "Steady state over 5 replicas:
 * $0.038–0.047 per 1,000 tasks; one burst $0.039–0.049". With no steady-state figure, the burst and why it stands
 * alone; null when nothing has a cost.
 */
export function costNote(s: SpecCost): string | null {
  if (!s.burst) return null;
  if (!s.steady) return `One burst: ${f.usdRange(s.burst)} per 1,000 tasks. The host was never full, so there is no steady-state figure.`;
  const over = s.replicas === 1 ? 'one replica' : `${f.num(s.replicas)} replicas`;
  return `Steady state over ${over}: ${f.usdRange(s.steady)} per 1,000 tasks; one burst ${f.usdRange(s.burst)}`;
}
