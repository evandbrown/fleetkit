// Checks a dataset against DATA.md. Each function returns a list of problems; an empty list means it holds.
// The builder refuses a document with problems; the tests run these over the fixtures.
import {
  costPer1000,
  countsTowardResult,
  densityBriefs,
  hostKindOf,
  meetsEveryCriterion,
  parseTrialId,
  runResultCore,
} from './derive';
import { getPath, SPEC_PATHS, typedCopy, type TypedField } from './spec';
import { SCHEMA, type CampaignDoc, type Index, type RunDoc, type TrialDoc } from './types';
import { retiredWordsInJson } from './words';

const ID = /^[a-z0-9][a-z0-9-]*$/;
const WHEN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$/;
const close = (a: number | null | undefined, b: number | null | undefined, tol = 1e-6) =>
  a === b || (typeof a === 'number' && typeof b === 'number' && Math.abs(a - b) <= tol * Math.max(1, Math.abs(b)));
/** Deep equality that ignores key order. */
function canon(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(canon);
  if (v && typeof v === 'object') {
    return Object.fromEntries(Object.keys(v).sort().map((k) => [k, canon((v as Record<string, unknown>)[k])]));
  }
  return v;
}
const same = (a: unknown, b: unknown) => JSON.stringify(canon(a)) === JSON.stringify(canon(b));


function syntheticMarks(p: string[], where: string, x: { id: string; title?: string; synthetic?: true }) {
  if (x.synthetic) {
    if (!x.id.endsWith('-synthetic')) p.push(`${where}: synthetic, but the id doesn't end in -synthetic`);
    if (x.title !== undefined && !x.title.startsWith('Synthetic:')) p.push(`${where}: synthetic, but the title doesn't start with "Synthetic:"`);
  } else {
    if (x.id.endsWith('-synthetic')) p.push(`${where}: id ends in -synthetic but synthetic is not true`);
    if (x.title?.startsWith('Synthetic:')) p.push(`${where}: title says Synthetic but synthetic is not true`);
  }
}

export function checkIndex(index: Index): string[] {
  const p: string[] = [];
  if (index.schema !== SCHEMA) p.push(`index: schema ${index.schema}`);
  const ids = index.campaigns.map((c) => c.id);
  if (new Set(ids).size !== ids.length) p.push('index: duplicate campaign ids');
  if (index.latest !== null && !ids.includes(index.latest)) p.push(`index: latest ${index.latest} is not listed`);
  if (index.latest === null && ids.length) p.push('index: latest is null but campaigns are listed');
  const newest = [...index.campaigns].sort((a, b) => b.started.localeCompare(a.started))[0];
  if (newest && index.latest !== newest.id) p.push(`index: latest should be ${newest.id}, the most recently started`);
  for (let i = 1; i < index.campaigns.length; i++) {
    if (index.campaigns[i - 1].started < index.campaigns[i].started) p.push('index: campaigns are not newest first');
  }
  for (const c of index.campaigns) {
    const w = `index ${c.id}`;
    if (!ID.test(c.id)) p.push(`${w}: bad id`);
    if (!WHEN.test(c.started)) p.push(`${w}: started ${c.started} is not ISO UTC to the minute`);
    syntheticMarks(p, w, c);
    if (c.outcomes.length !== c.specs.length) p.push(`${w}: one outcome per spec`);
  }
  p.push(...retiredWordsInJson(index).map((h) => `index: retired word at ${h}`));
  return p;
}

export function checkCampaign(doc: CampaignDoc, entry?: Index['campaigns'][number]): string[] {
  const p: string[] = [];
  const w = `campaign ${doc.id}`;
  if (doc.schema !== SCHEMA) p.push(`${w}: schema ${doc.schema}`);
  syntheticMarks(p, w, doc);
  if (!WHEN.test(doc.started) || !WHEN.test(doc.ended)) p.push(`${w}: started/ended not ISO UTC to the minute`);
  if (entry) {
    for (const k of ['id', 'title', 'question', 'started', 'status', 'synthetic', 'before_campaigns', 'outcomes'] as const) {
      if (!same(entry[k], doc[k])) p.push(`${w}: ${k} differs from the index entry`);
    }
    if (entry.replicas !== doc.definition.replicas) p.push(`${w}: replicas differ from the index entry`);
    if (entry.runs !== doc.runs.length) p.push(`${w}: runs differ from the index entry`);
    if (!same(entry.specs, doc.specs.map((s) => ({ name: s.name, label: s.label })))) p.push(`${w}: specs differ from the index entry`);
  }
  if (doc.before_campaigns && (doc.specs.length !== 1 || doc.definition.replicas !== 1)) {
    p.push(`${w}: a campaign of one has one spec and one replica`);
  }
  if (doc.definition.id !== doc.id || doc.definition.question !== doc.question) p.push(`${w}: definition id/question differ`);

  // Specs: names, changes from the base, typed copies.
  const names = doc.specs.map((s) => s.name);
  if (new Set(names).size !== names.length) p.push(`${w}: duplicate spec names`);
  if (!same(names, doc.definition.specs.map((s) => s.name))) p.push(`${w}: specs differ from the definition's`);
  const fieldPaths = new Set(doc.spec_fields.map((f) => f.path));
  for (const s of doc.specs) {
    const ws = `${w} spec ${s.name}`;
    if (!ID.test(s.name)) p.push(`${ws}: bad name`);
    const def = doc.definition.specs.find((d) => d.name === s.name);
    const declared = Object.entries(def?.changes ?? {});
    if (!same(declared.map(([k]) => k).sort(), s.changes.map((c) => c.path).sort())) p.push(`${ws}: changes differ from the definition`);
    for (const c of s.changes) {
      if (!fieldPaths.has(c.path)) p.push(`${ws}: change ${c.path} has no spec_fields entry`);
      if (!same(getPath(doc.definition.base, c.path), c.base)) p.push(`${ws}: change ${c.path}: base value differs from the definition's base`);
      if (!same(getPath(s.spec, c.path), c.value)) p.push(`${ws}: change ${c.path}: value differs from the resolved spec`);
      if (!same(def?.changes[c.path], c.value)) p.push(`${ws}: change ${c.path}: value differs from the definition`);
      if (same(c.base, c.value)) p.push(`${ws}: change ${c.path} doesn't change anything`);
    }
    for (const [field, path] of Object.entries(SPEC_PATHS) as [TypedField, string][]) {
      const v = getPath(s.spec, path);
      if (v !== undefined && !same(v, typedCopy(s, field))) p.push(`${ws}: typed copy of ${path} differs from the spec`);
    }
    if (s.host_kind !== hostKindOf(s.instance_type)) p.push(`${ws}: host_kind isn't derived from the instance type`);
    if (s.densities.some((d, i) => !Number.isInteger(d) || d < 1 || (i > 0 && d <= s.densities[i - 1]))) {
      p.push(`${ws}: densities must be ascending positive integers`);
    }
  }

  // Runs: ids, counts, results recomputed from their densities.
  const runIds = doc.runs.map((r) => r.id);
  if (new Set(runIds).size !== runIds.length) p.push(`${w}: duplicate run ids`);
  const expectedOrder = doc.specs.flatMap((s) =>
    doc.runs.filter((r) => r.spec === s.name).sort((a, b) => a.replica - b.replica).map((r) => r.id),
  );
  if (!same(runIds, expectedOrder)) p.push(`${w}: runs are not in spec order, then replica order`);
  const incomplete = doc.runs.some((r) => r.status === 'incomplete');
  if (doc.status !== (incomplete || doc.runs.length < doc.specs.length * doc.definition.replicas ? 'partial' : 'complete')) {
    p.push(`${w}: status should be ${incomplete ? 'partial' : 'complete'}`);
  }
  for (const r of doc.runs) {
    const wr = `${w} run ${r.id}`;
    const spec = doc.specs.find((s) => s.name === r.spec);
    if (!spec) {
      p.push(`${wr}: unknown spec ${r.spec}`);
      continue;
    }
    if (r.id !== `${r.spec}-r${r.replica}`) p.push(`${wr}: id should be ${r.spec}-r${r.replica}`);
    if (r.replica < 1 || r.replica > doc.definition.replicas) p.push(`${wr}: replica out of range`);
    if (r.host.host_kind !== hostKindOf(r.host.instance_type)) p.push(`${wr}: host_kind isn't derived from the instance type`);
    if (r.host.instance_type !== spec.instance_type) p.push(`${wr}: measured instance type ${r.host.instance_type} isn't the spec's ${spec.instance_type}`);
    if (!same(r.by_density.map((b) => b.density), spec.densities)) p.push(`${wr}: by_density doesn't list the spec's densities`);
    if (r.status === 'incomplete') {
      if (r.result !== null) p.push(`${wr}: an incomplete run has no result`);
      continue;
    }
    if (!r.result) {
      p.push(`${wr}: a complete run needs a result`);
      continue;
    }
    const core = runResultCore(r.by_density);
    for (const k of ['tested_successfully', 'first_failed', 'not_tried', 'not_run'] as const) {
      if (!same(core[k], r.result[k])) p.push(`${wr}: result.${k} is ${JSON.stringify(r.result[k])}, rule 4 gives ${JSON.stringify(core[k])}`);
    }
    const t = r.result.tested_successfully;
    if (!close(r.result.per_host_vcpu, t === null ? null : t / r.host.vcpus)) p.push(`${wr}: per_host_vcpu ≠ tested ÷ host vCPUs`);
    if (!close(r.result.vcpus_allocated_per_host_vcpu, t === null ? null : (t * spec.microvm.vcpus) / r.host.vcpus)) {
      p.push(`${wr}: vcpus_allocated_per_host_vcpu is wrong`);
    }
    if ((r.result.first_failed === null) !== (r.result.limit === null)) p.push(`${wr}: limit exists exactly when a density failed`);
    if (r.result.limit && r.result.limit.density !== r.result.first_failed) p.push(`${wr}: limit.density ≠ first_failed`);
    if ((t === null) !== (r.result.cost_per_1000_tasks === null)) p.push(`${wr}: cost exists exactly when a density passed`);
  }

  // Outcomes per spec, from the runs.
  if (!same(doc.outcomes.map((o) => o.spec), names)) p.push(`${w}: one outcome per spec, in spec order`);
  for (const o of doc.outcomes) {
    const runs = doc.runs.filter((r) => r.spec === o.spec).sort((a, b) => a.replica - b.replica);
    const want = {
      spec: o.spec,
      runs: runs.map((r) => r.id),
      tested_successfully: runs.map((r) => r.result?.tested_successfully ?? null),
      per_host_vcpu: runs.map((r) => r.result?.per_host_vcpu ?? null),
      cost_per_1000_tasks: runs.map((r) =>
        r.result?.cost_per_1000_tasks
          ? { execution: r.result.cost_per_1000_tasks.execution, observed: r.result.cost_per_1000_tasks.observed }
          : null,
      ),
    };
    if (!same(o, want)) p.push(`${w}: outcome for ${o.spec} doesn't match its runs`);
  }
  p.push(...retiredWordsInJson(doc).map((h) => `${w}: retired word at ${h}`));
  return p;
}

export function checkRun(run: RunDoc, campaign: CampaignDoc): string[] {
  const p: string[] = [];
  const w = `run ${campaign.id}/${run.id}`;
  if (run.schema !== SCHEMA) p.push(`${w}: schema ${run.schema}`);
  if (run.campaign !== campaign.id) p.push(`${w}: campaign ${run.campaign}`);
  if (Boolean(run.synthetic) !== Boolean(campaign.synthetic)) p.push(`${w}: synthetic differs from its campaign`);
  const entry = campaign.runs.find((r) => r.id === run.id);
  const spec = campaign.specs.find((s) => s.name === run.spec);
  if (!entry || !spec) return [...p, `${w}: not in its campaign`];
  for (const k of ['spec', 'replica', 'status', 'note', 'started', 'duration_s', 'host', 'result'] as const) {
    if (!same(entry[k], run[k])) p.push(`${w}: ${k} differs from the campaign's entry`);
  }
  const briefOf = (b: { density: number; result: string; passed: number; trials: number }) =>
    ({ density: b.density, result: b.result, passed: b.passed, trials: b.trials });
  if (!same(entry.by_density, run.by_density.map(briefOf))) p.push(`${w}: by_density differs from the campaign's entry`);

  // Trials: ids, order, numbering, pass values.
  const ids = run.trials.map((t) => t.id);
  if (new Set(ids).size !== ids.length) p.push(`${w}: duplicate trial ids`);
  if (!same(run.trials.map((t) => t.order), run.trials.map((_, i) => i + 1))) p.push(`${w}: trials aren't in execution order 1..n`);
  const numbers = new Map<number, number>();
  const nth = new Map<string, number>();
  for (const t of run.trials) {
    const wt = `${w} trial ${t.id}`;
    const parsed = parseTrialId(t.id);
    if (!parsed) {
      p.push(`${wt}: bad id`);
      continue;
    }
    if (t.counts !== countsTowardResult(t.role)) p.push(`${wt}: counts must be true exactly for ladder and boundary trials`);
    if (parsed.counts) {
      if (!t.counts) p.push(`${wt}: a d<n>-t<k> id is only for trials that count`);
      if (parsed.density !== t.density || parsed.number !== t.number) p.push(`${wt}: id doesn't match density and number`);
      const next = (numbers.get(t.density) ?? 0) + 1;
      if (t.number !== next) p.push(`${wt}: number should be ${next} (numbered from 1 within its density, no gaps)`);
      numbers.set(t.density, next);
      if (t.passed === null) p.push(`${wt}: a trial that counts has a pass value`);
      else if (t.passed !== meetsEveryCriterion(t)) p.push(`${wt}: passed is ${t.passed} but rule 1 gives ${!t.passed}`);
      if (t.passed === false && t.failed.length === 0) p.push(`${wt}: a failed trial says which criteria it failed`);
      if (t.passed === true && t.failed.length) p.push(`${wt}: a passed trial lists no failures`);
      if (!spec.densities.includes(t.density)) p.push(`${wt}: density ${t.density} isn't in the spec`);
    } else {
      if (parsed.role !== t.role) p.push(`${wt}: id doesn't match role ${t.role}`);
      const k = (nth.get(t.role) ?? 0) + 1;
      if (parsed.nth !== k) p.push(`${wt}: should be ${k === 1 ? t.role : `${t.role}-${k}`}`);
      nth.set(t.role, k);
      if (t.number !== null || t.passed !== null) p.push(`${wt}: a trial that doesn't count has no number and no pass value`);
      if (!t.excluded_because) p.push(`${wt}: say why it's excluded`);
      if (t.cost_per_1000_tasks !== null) p.push(`${wt}: no cost for a trial that doesn't count`);
    }
    if (t.tasks.of > t.density || t.ready.microvms > t.density) p.push(`${wt}: more tasks or microVMs than its density`);
  }

  // Per density: rule 3 and the derived columns.
  const briefs = densityBriefs(spec.densities, run.trials);
  if (!same(briefs, run.by_density.map(briefOf))) p.push(`${w}: by_density doesn't follow rule 3 from the trials`);
  for (const d of run.by_density) {
    const wd = `${w} density ${d.density}`;
    const at = run.trials.filter((t) => t.counts && t.density === d.density).sort((a, b) => a.number! - b.number!);
    if (!same(d.trial_ids, at.map((t) => t.id))) p.push(`${wd}: trial_ids`);
    if (d.vcpus_allocated !== d.density * spec.microvm.vcpus) p.push(`${wd}: vcpus_allocated`);
    if (!close(d.mem_allocated_gib, (d.density * (spec.microvm.mem_mib + spec.microvm.mem_overhead_mib)) / 1024, 1e-3)) {
      p.push(`${wd}: mem_allocated_gib`);
    }
    if (d.cost_per_1000_tasks && d.result !== 'passed') p.push(`${wd}: cost only at densities that passed`);
  }
  const t = run.result?.tested_successfully ?? null;
  if (t !== null && run.has.cost) {
    const d = run.by_density.find((b) => b.density === t);
    if (!same(d?.cost_per_1000_tasks, run.result?.cost_per_1000_tasks)) p.push(`${w}: result cost ≠ cost at the density tested successfully`);
  }
  if (run.has.cost) {
    for (const tr of run.trials.filter((x) => x.counts && x.cost_per_1000_tasks && x.marks.release_ms !== null && x.marks.last_return_ms !== null)) {
      const secs = (tr.marks.last_return_ms! - tr.marks.release_ms!) / 1000;
      if (!close(tr.cost_per_1000_tasks!.execution, costPer1000(spec.price_usd_per_hour, secs, tr.density), 0.02)) {
        p.push(`${w} trial ${tr.id}: execution cost doesn't follow rule 5`);
      }
    }
  }

  // Columns are equal length and point at real trials.
  const cols = (name: string, c: Record<string, unknown[]>) => {
    const lens = new Set(Object.values(c).map((a) => a.length));
    if (lens.size > 1) p.push(`${w}: ${name} columns differ in length`);
  };
  cols('tasks', run.tasks as unknown as Record<string, unknown[]>);
  cols('steps', run.steps as unknown as Record<string, unknown[]>);
  const known = new Set(ids);
  if (run.tasks.trial.some((x) => !known.has(x)) || run.steps.trial.some((x) => !known.has(x))) p.push(`${w}: a task or step names an unknown trial`);
  if (run.overview.t_s.length !== run.overview.cpu_util_pct.length) p.push(`${w}: overview columns differ in length`);
  p.push(...retiredWordsInJson(run).map((h) => `${w}: retired word at ${h}`));
  return p;
}

export function checkTrial(doc: TrialDoc, run: RunDoc): string[] {
  const p: string[] = [];
  const w = `trial ${doc.campaign}/${doc.run}/${doc.id}`;
  if (doc.schema !== SCHEMA) p.push(`${w}: schema ${doc.schema}`);
  if (doc.campaign !== run.campaign || doc.run !== run.id) p.push(`${w}: wrong parent`);
  if (Boolean(doc.synthetic) !== Boolean(run.synthetic)) p.push(`${w}: synthetic differs from its run`);
  const s = run.trials.find((t) => t.id === doc.id);
  if (!s) return [...p, `${w}: not in its run`];
  if (s.density !== doc.density || s.number !== doc.number || s.role !== doc.role) p.push(`${w}: density/number/role differ from the run's summary`);
  if (!same(s.marks, doc.marks)) p.push(`${w}: marks differ from the run's summary`);
  if (!same(doc.microvms.map((m) => m.index), doc.microvms.map((_, i) => i + 1))) p.push(`${w}: microVMs are indexed 1..N`);
  if (doc.microvms.length !== doc.density) p.push(`${w}: ${doc.microvms.length} microVMs at density ${doc.density}`);
  if (s.attribution && !same(s.attribution.verdicts, doc.limit.verdicts)) p.push(`${w}: limit verdicts differ from the run's summary`);
  const h = doc.series.host;
  for (const [k, v] of Object.entries(h)) {
    if (Array.isArray(v) && v.length !== h.t_ms.length) p.push(`${w}: series.host.${k} length`);
  }
  for (const m of doc.series.microvms ?? []) {
    for (const [k, v] of Object.entries(m)) if (Array.isArray(v) && v.length !== m.t_ms.length) p.push(`${w}: series.microvms[${m.index}].${k} length`);
  }
  for (const m of doc.microvms) {
    for (const st of m.steps) if (st.end_ms < st.start_ms) p.push(`${w}: microVM ${m.index} step ${st.name} ends before it starts`);
  }
  p.push(...retiredWordsInJson(doc).map((x) => `${w}: retired word at ${x}`));
  return p;
}
