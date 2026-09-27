// SYNTHETIC TEST DATA. Writes a made-up 4-run campaign, nested-sizes-synthetic, that follows DATA.md, then
// rebuilds tests/fixtures/data/index.json from every campaign under tests/fixtures/data/campaigns/.
// Nothing here was measured. It exists to test the site and must never be copied into public/data
// (`npm run build` refuses it). Deterministic: the same seed writes the same bytes.
//
//   node tests/fixtures/make-synthetic.mjs      (or: npm run fixtures)
import { mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const DATA = join(HERE, 'data');
const ID = 'nested-sizes-synthetic';
const SCHEMA = 'fleetkit-site-data/1';
const SPEC_FIELDS = JSON.parse(readFileSync(join(HERE, 'spec-fields.json'), 'utf8'));

// ---- deterministic randomness and rounding ----------------------------------------------------------

function mulberry32(a) {
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rand = mulberry32(20260927);
const between = (lo, hi) => lo + (hi - lo) * rand();
const r4 = (v) => Math.round(v * 1e4) / 1e4;
const sig3 = (v) => (v === 0 ? 0 : Number(v.toPrecision(3)));
const range = (xs) => [Math.min(...xs), Math.max(...xs)];
const r4range = (xs) => range(xs).map(r4);
function percentile(values, q) {
  const xs = [...values].sort((a, b) => a - b);
  if (!xs.length) return null;
  const i = (q / 100) * (xs.length - 1);
  const lo = Math.floor(i);
  const hi = Math.ceil(i);
  return xs[lo] + (xs[hi] - xs[lo]) * (i - lo);
}
const fmtInt = (v) => Math.round(v).toLocaleString('en-US');

// ---- the campaign definition ------------------------------------------------------------------------

const RULES = [
  { key: 'host_cpu_pressure_pct', label: 'Host CPU pressure', verdict: 'host_cpu', op: '>=', threshold: 20 },
  { key: 'host_cpu_util_pct', label: 'Host CPU utilisation', verdict: 'host_cpu', op: '>=', threshold: 90 },
  { key: 'microvm_throttled_fraction', label: 'MicroVM throttled fraction', verdict: 'microvm_cpu_allowance', op: '>=', threshold: 0.1 },
  { key: 'host_mem_available_fraction', label: 'Host memory available', verdict: 'host_memory', op: '<', threshold: 0.1 },
  { key: 'host_mem_pressure_pct', label: 'Host memory pressure', verdict: 'host_memory', op: '>=', threshold: 5 },
  { key: 'host_io_pressure_pct', label: 'Host IO pressure', verdict: 'io', op: '>=', threshold: 10 },
  { key: 'host_steal_pct', label: 'Steal', verdict: 'steal', op: '>=', threshold: 5 },
];
const SEVERITY = ['host_cpu', 'microvm_cpu_allowance', 'host_memory', 'io', 'steal'];

const BASE = {
  host: { instance_type: 'm8i.4xlarge' },
  hypervisor: { name: 'firecracker', version: 'v1.17.0' },
  microvm: { vcpus: 2, mem_mib: 2048, mem_overhead_mib: 256 },
  workload: { task: 'shop-5-steps', products: 20 },
  procedure: {
    densities: [1, 2, 4, 8, 12, 16],
    boundary_trials: 2,
    warmup_trials: 1,
    settle_s: 10,
    illustration: true,
    stop_at_first_failure: true,
    release: 'all_ready',
  },
  criteria: { step_p50_ms: 1000, step_p95_ms: 2000, task_p95_ms: 5000, ready_limit_s: 180 },
  attribution: Object.fromEntries(RULES.map((r) => [r.key, r.threshold])),
  measurement: { host_hz: 5, guest_interval_ms: 200 },
  support: { instance_type: 'm8i.xlarge' },
};

const DEFINITION = {
  format: 'fleetkit-campaign/1',
  id: ID,
  question:
    'Does density per host vCPU stay the same when the worker host doubles from 8 to 16 vCPUs, with 2 vCPU / 2 GiB Firecracker microVMs?',
  base: BASE,
  specs: [
    { name: 'm8i-4xlarge', label: 'm8i.4xlarge (16 vCPUs)', changes: {} },
    {
      name: 'm8i-2xlarge',
      label: 'm8i.2xlarge (8 vCPUs)',
      changes: { 'host.instance_type': 'm8i.2xlarge', 'procedure.densities': [1, 2, 3, 4, 6, 8] },
    },
  ],
  replicas: 2,
};

const HOSTS = {
  'm8i.4xlarge': { vcpus: 16, cores: 8, mem_gib: 61.8, price: 0.84672 },
  'm8i.2xlarge': { vcpus: 8, cores: 4, mem_gib: 30.8, price: 0.42336 },
};

// Each run's trials in execution order: [role, density, pass?, how it fails].
const W = ['warmup', 1];
const I = ['illustration', 1];
const SCRIPTS = {
  'm8i-4xlarge-r1': [W, ['ladder', 1, true], ['ladder', 2, true], ['ladder', 4, true], ['ladder', 8, true],
    ['ladder', 12, false], ['boundary', 8, true], ['boundary', 8, true], ['boundary', 12, false], ['boundary', 12, false], I],
  'm8i-4xlarge-r2': [W, ['ladder', 1, true], ['ladder', 2, true], ['ladder', 4, true], ['ladder', 8, true],
    ['ladder', 12, false], ['boundary', 8, true], ['boundary', 8, true], ['boundary', 12, true], ['boundary', 12, false], I],
  'm8i-2xlarge-r1': [W, ['ladder', 1, true], ['ladder', 2, true], ['ladder', 3, true], ['ladder', 4, true],
    ['ladder', 6, false], ['boundary', 4, true], ['boundary', 4, true], ['boundary', 6, false, 'task'], ['boundary', 6, false], I],
  // The boundary check fails a trial at 4, so 4 becomes a failing density and 3 gets its extra trials.
  'm8i-2xlarge-r2': [W, ['ladder', 1, true], ['ladder', 2, true], ['ladder', 3, true], ['ladder', 4, true],
    ['ladder', 6, false], ['boundary', 4, true], ['boundary', 4, false], ['boundary', 6, false], ['boundary', 6, false],
    ['boundary', 3, true], ['boundary', 3, true], I],
};
const STARTED = {
  'm8i-4xlarge-r1': '2026-09-27T18:00Z',
  'm8i-4xlarge-r2': '2026-09-27T18:00Z',
  'm8i-2xlarge-r1': '2026-09-27T18:01Z',
  'm8i-2xlarge-r2': '2026-09-27T18:01Z',
};

// ---- a load model, so the numbers hang together -------------------------------------------------------

const STEPS = ['home', 'search', 'open_product', 'add_to_cart', 'verify_cart'];
const STEP_LABEL = { home: 'home', search: 'search', open_product: 'open product', add_to_cart: 'add to cart', verify_cart: 'verify cart', task: 'task' };
const STEP_BASE = { home: 520, search: 330, open_product: 260, add_to_cart: 110, verify_cart: 80 };
const STEP_LOAD = { home: 900, search: 520, open_product: 420, add_to_cart: 110, verify_cart: 90 };
const GUEST = ['renderer', 'browser', 'other_chromium', 'guestd', 'other'];
const CONSUMERS = ['microvm_vcpus', 'hypervisor', 'hostd', 'driver', 'unattributed'];
const product = (index) => `p${String(37 + index * 13).padStart(4, '0')}`;

function spec(name) {
  const s = structuredClone(BASE);
  for (const [path, value] of Object.entries(DEFINITION.specs.find((x) => x.name === name).changes)) {
    const keys = path.split('.');
    let o = s;
    for (const k of keys.slice(0, -1)) o = o[k];
    o[keys.at(-1)] = value;
  }
  return s;
}

function makeTrial({ role, density, pass, failMode }, host, sp) {
  const L = (density * sp.microvm.vcpus) / host.vcpus; // vCPUs allocated per host vCPU
  const over = Math.max(0, L - 0.5);
  const microvms = [];
  for (let i = 1; i <= density; i++) {
    const p0 = 180 + 8 * i + between(0, 40);
    const k0 = p0 + between(280, 340);
    const g0 = k0 + 1150 * (1 + 0.1 * L) * between(0.95, 1.05);
    const c0 = g0 + 5;
    const cr = c0 + 900 * (1 + 0.3 * L) * between(0.9, 1.1);
    const ready = cr + between(20, 250);
    microvms.push({
      index: i,
      product: product(i),
      boot: { process_started_ms: p0, kernel_start_ms: k0, guestd_start_ms: g0, chromium_launch_ms: c0, chromium_ready_ms: cr },
      ready_ms: ready,
    });
  }
  const allReady = Math.max(...microvms.map((m) => m.ready_ms));
  const release = allReady + 30;
  const failIndex = failMode === 'task' ? Math.ceil(density / 2) : null;
  const illustration = role === 'illustration';
  for (const m of microvms) {
    const dispatch = release + between(2, 12);
    let t = dispatch + 16;
    m.steps = [];
    for (const step of STEPS) {
      let d = (STEP_BASE[step] + STEP_LOAD[step] * over) * between(0.8, 1.2);
      if (m.index === failIndex && step === 'search') {
        m.steps.push({ name: step, start_ms: t, end_ms: t + 10000, ok: false });
        t += 10000;
        break;
      }
      m.steps.push({ name: step, start_ms: t, end_ms: t + d, ok: true, bytes: Math.round(between(0.6, 1.5) * 1e6), requests: Math.round(between(20, 36)) });
      t += d + 20 + (illustration ? 300 : 0);
    }
    const ok = m.index !== failIndex;
    const taskMs = t - (dispatch + 16);
    const wall = taskMs + 250 + between(0, 80);
    m.task = {
      dispatch_ms: dispatch,
      return_ms: dispatch + wall,
      task_ms: taskMs,
      wall_ms: wall,
      ok,
      ...(ok ? {} : { failed_step: 'search', failure_category: 'step_timeout' }),
      bytes: m.steps.reduce((a, s) => a + (s.bytes ?? 0), 0),
      requests: m.steps.reduce((a, s) => a + (s.requests ?? 0), 0),
      chromium_rss_mib: between(780, 860),
      clock_offset_ms: between(280, 550),
      timing_valid: !illustration,
    };
    m.outcome = 'completed';
    m.mem_peak_mib = between(690, 735);
  }

  // Scripted outcome: nudge the home step so the median lands on the scripted side of its target.
  const target = sp.criteria.step_p50_ms;
  const homes = () => microvms.filter((m) => m.task.ok).map((m) => m.steps[0].end_ms - m.steps[0].start_ms);
  const p50 = percentile(homes(), 50);
  const want = pass === false && failMode !== 'task' ? Math.max(p50, target * between(1.04, 1.3)) : pass ? Math.min(p50, target * 0.985) : p50;
  if (want !== p50) {
    const scale = want / p50;
    for (const m of microvms) {
      const h = m.steps[0];
      const grow = (h.end_ms - h.start_ms) * (scale - 1);
      h.end_ms += grow;
      for (const s of m.steps.slice(1)) {
        s.start_ms += grow;
        s.end_ms += grow;
      }
      m.task.task_ms += grow;
      m.task.wall_ms += grow;
      m.task.return_ms += grow;
    }
  }

  const lastReturn = Math.max(...microvms.map((m) => m.task.return_ms));
  for (const m of microvms) {
    const end = lastReturn + 20 + 15 * m.index + between(400, 750);
    m.destroy = { start_ms: end - between(400, 700), end_ms: end };
  }
  const clean = Math.max(...microvms.map((m) => m.destroy.end_ms)) + 80;
  const marks = { all_ready_ms: Math.round(allReady), release_ms: Math.round(release), last_return_ms: Math.round(lastReturn), clean_ms: Math.round(clean), end_ms: Math.round(clean + 1000) };

  // Criteria, over the tasks that returned ok.
  const okVms = microvms.filter((m) => m.task.ok);
  const checks = [];
  for (const step of STEPS) {
    const ds = okVms.map((m) => m.steps.find((s) => s.name === step)).filter(Boolean).map((s) => s.end_ms - s.start_ms);
    checks.push({ subject: step, stat: 'p50', value_ms: r4(percentile(ds, 50)), target_ms: sp.criteria.step_p50_ms, met: percentile(ds, 50) <= sp.criteria.step_p50_ms });
    checks.push({ subject: step, stat: 'p95', value_ms: r4(percentile(ds, 95)), target_ms: sp.criteria.step_p95_ms, met: percentile(ds, 95) <= sp.criteria.step_p95_ms });
  }
  const tp95 = percentile(okVms.map((m) => m.task.task_ms), 95);
  checks.push({ subject: 'task', stat: 'p95', value_ms: r4(tp95), target_ms: sp.criteria.task_p95_ms, met: tp95 <= sp.criteria.task_p95_ms });
  const tasks = { ok: okVms.length, of: density };
  const ready = { microvms: density, all_ready_ms: Math.round(allReady), limit_ms: sp.criteria.ready_limit_s * 1000, met: allReady <= sp.criteria.ready_limit_s * 1000 };
  const meets = ready.met && tasks.ok === tasks.of && checks.every((c) => c.met);
  const failed = [
    ...(tasks.ok < tasks.of ? [`${tasks.of - tasks.ok} of ${tasks.of} tasks failed (step_timeout at search)`] : []),
    ...checks.filter((c) => !c.met).map((c) => `${STEP_LABEL[c.subject]} ${c.stat} ${fmtInt(c.value_ms)} ms > ${fmtInt(c.target_ms)} ms`),
  ];

  // Attribution over the task window.
  const util = Math.min(99.5, 20 + 70 * L * between(0.95, 1.05));
  const pressure = 1 + 60 * Math.max(0, L - 0.7) ** 1.5 * between(0.85, 1.15);
  const memAvail = 1 - (density * 0.72 + 1.6) / host.mem_gib;
  const values = {
    host_cpu_pressure_pct: r4(pressure),
    host_cpu_util_pct: r4(util),
    microvm_throttled_fraction: r4(between(0, 0.0004)),
    host_mem_available_fraction: r4(memAvail),
    host_mem_pressure_pct: 0,
    host_io_pressure_pct: r4(between(0, 0.05)),
    host_steal_pct: r4(between(0, 0.3)),
  };
  const rules = RULES.map((r) => {
    const v = values[r.key];
    return { key: r.key, value: v, fired: r.op === '>=' ? v >= r.threshold : v < r.threshold };
  });
  const firedVerdicts = SEVERITY.filter((v) => rules.some((r) => r.fired && RULES.find((d) => d.key === r.key).verdict === v));
  const verdicts = firedVerdicts.length ? firedVerdicts : ['none'];
  const windowS = (lastReturn - release) / 1000;
  const busyCores = (util / 100) * host.vcpus;
  const busy = busyCores * windowS;
  const hostCpu = { microvm_vcpus: busy * 0.965, hypervisor: busy * 0.009, hostd: busy * 0.004, driver: busy * 0.002 };
  hostCpu.unattributed = Math.max(0, busy - Object.values(hostCpu).reduce((a, b) => a + b, 0));
  const renderer = 0.62 - 0.12 * Math.max(0, L - 1);
  const share = { renderer, browser: 0.17, other_chromium: 0.1 + 0.05 * Math.max(0, L - 1), guestd: 0.03 };
  share.other = 1 - Object.values(share).reduce((a, b) => a + b, 0);
  const attribution = {
    window_ms: [marks.release_ms, marks.last_return_ms],
    verdicts,
    rules,
    host_cpu_s: { ...Object.fromEntries(CONSUMERS.map((k) => [k, r4(hostCpu[k])])), busy: r4(busy) },
    guest_share: Object.fromEntries(GUEST.map((g) => [g, r4(share[g])])),
    missing: [],
  };
  const counts = role === 'ladder' || role === 'boundary';
  const price = HOSTS[sp.host.instance_type].price;
  const cost = counts
    ? {
        execution: r4((1000 * price * ((marks.last_return_ms - marks.release_ms) / 1000)) / 3600 / density),
        observed: r4((1000 * price * (marks.clean_ms / 1000)) / 3600 / density),
        fixture: 0.05,
      }
    : null;

  return {
    L, microvms, marks, checks, tasks, ready, meets, failed, attribution, cost, counts, util, pressure, busyCores,
    memUsed: (1 - memAvail) * host.mem_gib, steal: values.host_steal_pct, hostCpu, share,
  };
}

// ---- trial documents ----------------------------------------------------------------------------------

function trialSeries(t, host, sp, settleS) {
  const start = -settleS * 1000;
  const end = t.marks.end_ms;
  const host5 = { t_ms: [], cpu_util_pct: [], mem_used_gib: [], cpu_pressure_pct: [], mem_pressure_pct: [], io_pressure_pct: [], steal_pct: [], cores: Object.fromEntries(CONSUMERS.map((k) => [k, []])) };
  const alive = (m, x) => x >= m.boot.process_started_ms && x <= m.destroy.end_ms;
  for (let x = start; x <= end; x += 200) {
    const inTask = x >= t.marks.release_ms && x <= t.marks.last_return_ms;
    const booting = x >= 0 && x < t.marks.all_ready_ms;
    const util = x < 0 ? 0.4 : inTask ? t.util * between(0.96, 1.03) : booting ? 15 + 25 * t.L : 3;
    const cores = (Math.min(100, util) / 100) * host.vcpus;
    const n = t.microvms.filter((m) => alive(m, x)).length;
    host5.t_ms.push(Math.round(x));
    host5.cpu_util_pct.push(sig3(Math.min(100, util)));
    host5.mem_used_gib.push(sig3(1.3 + n * 0.72));
    host5.cpu_pressure_pct.push(sig3(inTask ? t.pressure * between(0.8, 1.2) : booting ? t.pressure * 0.3 : 0));
    host5.mem_pressure_pct.push(0);
    host5.io_pressure_pct.push(sig3(between(0, 0.05)));
    host5.steal_pct.push(sig3(between(0, 0.3)));
    const vm = n ? cores * 0.95 : 0;
    host5.cores.microvm_vcpus.push(sig3(vm));
    host5.cores.hypervisor.push(sig3(n ? cores * 0.012 : 0));
    host5.cores.hostd.push(sig3(0.02 + (n ? cores * 0.004 : 0)));
    host5.cores.driver.push(sig3(0.01 + (inTask ? 0.03 : 0)));
    host5.cores.unattributed.push(sig3(Math.max(0, cores - vm - (n ? cores * 0.016 : 0) - 0.03)));
  }
  const microvms = t.microvms.map((m) => {
    const s = { index: m.index, t_ms: [], vcpu_cores: [], hypervisor_cores: [], throttled_fraction: [], mem_mib: [] };
    for (let x = Math.ceil(m.boot.process_started_ms / 200) * 200; x <= m.destroy.end_ms; x += 200) {
      const inTask = x >= m.task.dispatch_ms && x <= m.task.return_ms;
      const booting = x < m.ready_ms;
      s.t_ms.push(x);
      s.vcpu_cores.push(sig3(inTask ? (1.6 / Math.max(1, t.L)) * between(0.85, 1.1) : booting ? 0.6 : 0.02));
      s.hypervisor_cores.push(sig3(between(0.005, 0.015)));
      s.throttled_fraction.push(0);
      s.mem_mib.push(sig3(Math.min(m.mem_peak_mib, 180 + ((x - m.boot.process_started_ms) / 5000) * m.mem_peak_mib)));
    }
    return s;
  });
  const guest = t.microvms.map((m) => {
    const g = { index: m.index, t_ms: [], ...Object.fromEntries(GUEST.map((k) => [k, []])) };
    for (let x = Math.ceil(m.task.dispatch_ms / 200) * 200; x <= m.task.return_ms; x += 200) {
      const total = (1.6 / Math.max(1, t.L)) * between(0.85, 1.1);
      g.t_ms.push(x);
      for (const k of GUEST) g[k].push(sig3(total * t.share[k]));
    }
    return g;
  });
  const fixture = { t_ms: [], ms: [] };
  for (let x = Math.ceil(start / 1000) * 1000; x <= end; x += 1000) {
    fixture.t_ms.push(x);
    fixture.ms.push(sig3(between(0.3, 0.6)));
  }
  return { host: host5, microvms, guest, fixture_rtt: fixture };
}

function lane(m) {
  const round = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, typeof v === 'number' ? Math.round(v) : v]));
  return {
    index: m.index,
    product: m.product,
    outcome: m.outcome,
    ready_ms: Math.round(m.ready_ms),
    boot: round(m.boot),
    task: {
      ...round(m.task),
      chromium_rss_mib: Math.round(m.task.chromium_rss_mib),
    },
    steps: m.steps.map((s) => ({ ...s, start_ms: Math.round(s.start_ms), end_ms: Math.round(s.end_ms) })),
    destroy: round(m.destroy),
  };
}

// ---- runs ---------------------------------------------------------------------------------------------

const EXCLUDED = {
  warmup: 'the first microVM after setup reads its root filesystem from disk; later ones read it from memory.',
  illustration: 'screenshots after every step add time inside the task.',
};

function unionVerdicts(lists) {
  const seen = new Set(lists.flat());
  const fired = SEVERITY.filter((v) => seen.has(v));
  if (fired.length) return fired;
  return seen.has('unknown') || !seen.size ? ['unknown'] : ['none'];
}

function makeRun(specName, replica) {
  const sp = spec(specName);
  const hostSpec = HOSTS[sp.host.instance_type];
  const runId = `${specName}-r${replica}`;
  const host = {
    instance_type: sp.host.instance_type,
    host_kind: 'nested',
    vcpus: hostSpec.vcpus,
    cores: hostSpec.cores,
    threads_per_core: 2,
    sockets: 1,
    cpu_model: 'Intel(R) Xeon(R) 6975P-C',
    mem_gib: hostSpec.mem_gib,
    kernel_release: '6.18.48-109.150.amzn2023.x86_64',
    hypervisor_version: 'Firecracker v1.17.0',
    chromium_version: '154.0.8037.57',
  };
  const numbers = new Map();
  const trials = [];
  const docs = [];
  const tasks = { trial: [], microvm: [], product: [], ok: [], task_ms: [], wall_ms: [], failed_step: [], failure_category: [], timing_valid: [], img: [] };
  const steps = { trial: [], microvm: [], step: [], duration_ms: [], ok: [] };
  const overview = { t_s: [], cpu_util_pct: [], bands: [] };
  let clock = 0;
  SCRIPTS[runId].forEach(([role, density, pass, failMode], i) => {
    const t = makeTrial({ role, density, pass, failMode }, hostSpec, sp);
    let id;
    let number = null;
    if (t.counts) {
      number = (numbers.get(density) ?? 0) + 1;
      numbers.set(density, number);
      id = `d${density}-t${number}`;
      if (t.meets !== pass) throw new Error(`${runId} ${id}: scripted ${pass}, model gave ${t.meets}`);
    } else {
      id = role;
    }
    const summary = {
      id,
      density,
      number,
      role,
      counts: t.counts,
      order: i + 1,
      passed: t.counts ? t.meets : null,
      at_limit: t.counts && t.meets && !['none', 'unknown'].includes(t.attribution.verdicts[0]),
      failed: t.counts ? t.failed : [],
      ready: t.ready,
      tasks: t.tasks,
      checks: t.checks,
      clean: true,
      marks: t.marks,
      attribution: t.attribution,
      host: {
        cpu_util_pct: r4(t.util),
        cpu_pressure_pct: r4(t.pressure),
        busy_cores: r4(t.busyCores),
        mem_used_gib: r4(t.memUsed),
        steal_pct: t.steal,
      },
      microvm_mem_peak_mib: r4range(t.microvms.map((m) => m.mem_peak_mib)),
      cost_per_1000_tasks: t.cost,
      ...(t.counts ? {} : { excluded_because: EXCLUDED[role] }),
    };
    trials.push(summary);

    for (const m of t.microvms) {
      tasks.trial.push(id);
      tasks.microvm.push(m.index);
      tasks.product.push(m.product);
      tasks.ok.push(m.task.ok);
      tasks.task_ms.push(r4(m.task.task_ms));
      tasks.wall_ms.push(r4(m.task.wall_ms));
      tasks.failed_step.push(m.task.failed_step ?? null);
      tasks.failure_category.push(m.task.failure_category ?? null);
      tasks.timing_valid.push(m.task.timing_valid);
      tasks.img.push(null);
      for (const s of m.steps) {
        steps.trial.push(id);
        steps.microvm.push(m.index);
        steps.step.push(s.name);
        steps.duration_ms.push(r4(s.end_ms - s.start_ms));
        steps.ok.push(s.ok);
      }
    }

    // The run's timeline: settle, then the trial.
    const settle = sp.procedure.settle_s;
    for (let s = 0; s < settle; s++) {
      overview.t_s.push(clock + s);
      overview.cpu_util_pct.push(sig3(between(0.2, 0.8)));
    }
    const startS = clock + settle;
    const endS = startS + Math.ceil(t.marks.end_ms / 1000);
    for (let s = startS; s < endS; s++) {
      const x = (s - startS) * 1000;
      const u = x < t.marks.all_ready_ms ? 15 + 25 * t.L : x <= t.marks.last_return_ms ? t.util : 4;
      overview.t_s.push(s);
      overview.cpu_util_pct.push(sig3(Math.min(100, u * between(0.95, 1.05))));
    }
    overview.bands.push({ trial: id, start_s: startS, end_s: endS });
    clock = endS;

    const series = trialSeries(t, hostSpec, sp, settle);
    docs.push({
      schema: SCHEMA,
      campaign: ID,
      run: runId,
      id,
      synthetic: true,
      density,
      number,
      role,
      window_ms: [-settle * 1000, t.marks.end_ms],
      marks: t.marks,
      settle: { seconds: settle, cpu_util_mean_pct: r4(between(0.2, 0.6)) },
      microvms: t.microvms.map(lane),
      series,
      limit: {
        verdicts: t.attribution.verdicts,
        microvms: t.microvms.map((m) => ({
          index: m.index,
          vcpu_s: r4((1.6 / Math.max(1, t.L)) * ((m.task.return_ms - m.task.dispatch_ms) / 1000)),
          hypervisor_s: r4(0.01 * ((m.task.return_ms - m.task.dispatch_ms) / 1000)),
          throttled_fraction: 0,
          cpu_pressure_pct: r4(t.pressure * between(0.5, 0.9)),
          mem_peak_mib: r4(m.mem_peak_mib),
        })),
      },
    });
  });

  // By density (rule 3), then the run's result (rule 4).
  const by_density = sp.procedure.densities.map((density) => {
    const at = trials.filter((t) => t.counts && t.density === density).sort((a, b) => a.number - b.number);
    const passed = at.filter((t) => t.passed).length;
    const result = at.length === 0 ? 'not_run' : passed === at.length ? 'passed' : 'failed';
    const d = {
      density,
      result,
      passed,
      trials: at.length,
      trial_ids: at.map((t) => t.id),
      checks: at.length
        ? at[0].checks.map((c, k) => ({
            subject: c.subject,
            stat: c.stat,
            target_ms: c.target_ms,
            range: r4range(at.map((t) => t.checks[k].value_ms)),
            met: at.filter((t) => t.checks[k].met).length,
          }))
        : [],
      host: at.length
        ? {
            cpu_util_pct: r4range(at.map((t) => t.host.cpu_util_pct)),
            cpu_pressure_pct: r4range(at.map((t) => t.host.cpu_pressure_pct)),
            busy_cores: r4range(at.map((t) => t.host.busy_cores)),
            mem_used_gib: r4range(at.map((t) => t.host.mem_used_gib)),
          }
        : null,
      vcpus_allocated: density * sp.microvm.vcpus,
      mem_allocated_gib: r4((density * (sp.microvm.mem_mib + sp.microvm.mem_overhead_mib)) / 1024),
      verdicts: at.length ? unionVerdicts(at.map((t) => t.attribution.verdicts)) : [],
    };
    if (result === 'passed') {
      d.cost_per_1000_tasks = {
        execution: r4range(at.map((t) => t.cost_per_1000_tasks.execution)),
        observed: r4range(at.map((t) => t.cost_per_1000_tasks.observed)),
        fixture: r4range(at.map((t) => t.cost_per_1000_tasks.fixture)),
      };
    }
    return d;
  });

  let tested = null;
  for (const b of by_density) {
    if (b.result !== 'passed') break;
    tested = b.density;
  }
  const failedDs = by_density.filter((b) => b.result === 'failed').map((b) => b.density);
  const firstFailed = failedDs.length ? Math.min(...failedDs) : null;
  const atTested = by_density.find((b) => b.density === tested);
  const atFailed = by_density.find((b) => b.density === firstFailed);
  let limit = null;
  if (atFailed) {
    const ft = trials.filter((t) => atFailed.trial_ids.includes(t.id));
    const pt = trials.filter((t) => atTested?.trial_ids.includes(t.id));
    const rv = (ts, key) => ts.map((t) => t.attribution.rules.find((r) => r.key === key).value);
    const sepRule = RULES.find((r) => {
      if (r.op !== '>=' || !pt.length) return false;
      const [pmax, fmin] = [Math.max(...rv(pt, r.key)), Math.min(...rv(ft, r.key))];
      return pmax < r.threshold && fmin >= r.threshold;
    });
    const sums = Object.fromEntries(CONSUMERS.map((k) => [k, ft.reduce((a, t) => a + t.attribution.host_cpu_s[k], 0)]));
    const shares = Object.fromEntries(GUEST.map((g) => [g, ft.reduce((a, t) => a + t.attribution.guest_share[g], 0)]));
    const top = GUEST.reduce((a, b) => (shares[b] > shares[a] ? b : a));
    limit = {
      density: firstFailed,
      verdicts: atFailed.verdicts,
      trials_with_verdict: ft.filter((t) => t.attribution.verdicts.includes(atFailed.verdicts[0])).length,
      trials: ft.length,
      ...(sepRule
        ? { separated_by: { rule: sepRule.key, passing: r4range(rv(pt, sepRule.key)), failing: r4range(rv(ft, sepRule.key)), threshold: sepRule.threshold } }
        : {}),
      host_consumer: CONSUMERS.reduce((a, b) => (sums[b] > sums[a] ? b : a)),
      guest_group: top,
      guest_share: r4range(ft.map((t) => t.attribution.guest_share[top])),
    };
  }
  const result = {
    tested_successfully: tested,
    first_failed: firstFailed,
    not_tried: tested !== null && firstFailed !== null && firstFailed - tested > 1 ? [tested + 1, firstFailed - 1] : null,
    not_run: by_density.filter((b) => b.result === 'not_run').map((b) => b.density),
    per_host_vcpu: tested === null ? null : r4(tested / host.vcpus),
    vcpus_allocated_per_host_vcpu: tested === null ? null : r4((tested * sp.microvm.vcpus) / host.vcpus),
    cost_per_1000_tasks: atTested?.cost_per_1000_tasks ?? null,
    limit,
  };

  const entry = {
    id: runId,
    spec: specName,
    replica,
    status: 'complete',
    started: STARTED[runId],
    duration_s: clock,
    host,
    result,
    by_density: by_density.map(({ density, result: r, passed, trials: n }) => ({ density, result: r, passed, trials: n })),
  };
  const support = { t_s: [], cpu_util_pct: [] };
  for (let s = 0; s < clock; s += 1) {
    support.t_s.push(s);
    support.cpu_util_pct.push(sig3(between(2, 8)));
  }
  const doc = {
    schema: SCHEMA,
    ...entry,
    campaign: ID,
    synthetic: true,
    code: { commit: '0000000000000000000000000000000000000000', dirty: false },
    has: { host_hz: 5, boot_phases: true, per_microvm_cpu: true, guest_series: true, attribution: true, cost: true, filmstrip: false, fixture_rtt: true },
    by_density,
    trials,
    tasks,
    steps,
    overview,
    support,
  };
  return { entry, doc, docs, specDoc: sp };
}

// ---- the campaign -------------------------------------------------------------------------------------

function getPath(o, path) {
  return path.split('.').reduce((v, k) => (v == null ? undefined : v[k]), o);
}

function specDoc(s) {
  const sp = spec(s.name);
  return {
    name: s.name,
    label: s.label,
    changes: Object.entries(s.changes).map(([path, value]) => ({ path, base: getPath(BASE, path), value })),
    spec: sp,
    instance_type: sp.host.instance_type,
    host_kind: 'nested',
    hypervisor: sp.hypervisor.name,
    microvm: sp.microvm,
    densities: sp.procedure.densities,
    boundary_trials: sp.procedure.boundary_trials,
    criteria: sp.criteria,
    rules: RULES,
    price_usd_per_hour: HOSTS[sp.host.instance_type].price,
  };
}

const runs = [];
for (const s of DEFINITION.specs) for (let r = 1; r <= DEFINITION.replicas; r++) runs.push(makeRun(s.name, r));

const outcomes = DEFINITION.specs.map((s) => {
  const rs = runs.filter((r) => r.entry.spec === s.name).map((r) => r.entry);
  return {
    spec: s.name,
    runs: rs.map((r) => r.id),
    tested_successfully: rs.map((r) => r.result?.tested_successfully ?? null),
    per_host_vcpu: rs.map((r) => r.result?.per_host_vcpu ?? null),
    cost_per_1000_tasks: rs.map((r) =>
      r.result?.cost_per_1000_tasks ? { execution: r.result.cost_per_1000_tasks.execution, observed: r.result.cost_per_1000_tasks.observed } : null,
    ),
  };
});

const endMinute = (e) => {
  const d = new Date(e.started.replace('Z', ':00Z'));
  d.setUTCSeconds(d.getUTCSeconds() + e.duration_s);
  return d.toISOString().slice(0, 16) + 'Z';
};
const perVcpu = outcomes.flatMap((o) => o.per_host_vcpu).filter((v) => v !== null);
const campaign = {
  schema: SCHEMA,
  id: ID,
  title: 'Synthetic: two host sizes',
  question: DEFINITION.question,
  started: runs.map((r) => r.entry.started).sort()[0],
  ended: runs.map((r) => endMinute(r.entry)).sort().at(-1),
  status: 'complete',
  synthetic: true,
  provenance: 'recorded',
  definition: DEFINITION,
  spec_fields: SPEC_FIELDS,
  specs: DEFINITION.specs.map(specDoc),
  runs: runs.map((r) => r.entry),
  outcomes,
  answer: [
    { t: 'Synthetic, for testing the site. Across both sizes and both ' },
    { term: 'replica', t: 'replicas' },
    { t: ', ' },
    { term: 'density' },
    { t: ' per host vCPU came out at ' },
    { v: `${Math.min(...perVcpu)}–${Math.max(...perVcpu)}`, cls: 'derived', src: 'runs › result.per_host_vcpu' },
    { t: '. One 8-vCPU replica failed a boundary trial at density 4 and walked down to 3, so the spread between replicas is as large as the difference between sizes.' },
  ],
  does_not_show: [[{ t: 'Anything real: every number on this page is made up by tests/fixtures/make-synthetic.mjs.' }]],
  next: [
    {
      text: 'Narrow the boundary on the 16-vCPU host.',
      motivated_by: 'densities 9 to 11 were not tried',
      changes: { 'procedure.densities': [1, 2, 4, 8, 9, 10, 11, 12] },
    },
  ],
};

// ---- write ----------------------------------------------------------------------------------------------

const dir = join(DATA, 'campaigns', ID);
rmSync(dir, { recursive: true, force: true });
const write = (path, value, pretty = true) => {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(value, null, pretty ? 2 : undefined) + '\n');
};
write(join(dir, 'campaign.json'), campaign);
for (const r of runs) {
  write(join(dir, 'runs', `${r.entry.id}.json`), r.doc, false);
  for (const t of r.docs) write(join(dir, 'runs', r.entry.id, `${t.id}.json`), t, false);
}

// index.json, from every campaign in the fixtures (including any real snapshot written by another script).
const entries = [];
for (const c of readdirSync(join(DATA, 'campaigns'))) {
  const p = join(DATA, 'campaigns', c, 'campaign.json');
  if (!existsSync(p)) continue;
  const d = JSON.parse(readFileSync(p, 'utf8'));
  entries.push({
    id: d.id,
    title: d.title,
    question: d.question,
    started: d.started,
    status: d.status,
    ...(d.synthetic ? { synthetic: true } : {}),
    ...(d.before_campaigns ? { before_campaigns: true } : {}),
    replicas: d.definition.replicas,
    specs: d.specs.map((s) => ({ name: s.name, label: s.label })),
    runs: d.runs.length,
    outcomes: d.outcomes,
  });
}
entries.sort((a, b) => b.started.localeCompare(a.started) || a.id.localeCompare(b.id));
write(join(DATA, 'index.json'), { schema: SCHEMA, latest: entries[0]?.id ?? null, campaigns: entries });
console.log(`wrote ${ID}: ${runs.length} runs, ${runs.reduce((a, r) => a + r.docs.length, 0)} trial documents; index lists ${entries.length} campaigns`);
