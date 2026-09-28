// Checks a dataset against DATA.md. Each function returns a list of problems; an empty list means it holds.
// The builder refuses a dataset with problems; the tests run these over the fixtures.
import {
  costPer1000,
  countsTowardResult,
  densityBriefs,
  fullSizeTrials,
  hostKindOf,
  meanMidpoint,
  meetsEveryCriterion,
  midpoint,
  parseTrialId,
  runResultCore,
} from './derive';
import { filled } from './spec';
import { SCHEMA, type CampaignDoc, type Index, type ReplicaResult, type RunDoc, type RunEntry, type TrialDoc } from './types';
import { retiredWordsInJson } from './words';

const ID = /^[a-z0-9][a-z0-9-]*$/;
const WHEN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$/;
/** A commit as the dataset carries it: its first 10 hex characters (DATA.md, rule 9), never the full id. */
const COMMIT = /^[0-9a-f]{10}$/;
const close = (a: number | null | undefined, b: number | null | undefined, tol = 1e-3) =>
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
/** Words in a spec's short name: the tokens with a letter in them, so "1 vCPU / 1 GiB tuned" is three (catalog.py
 * counts the same way). */
const wordCount = (text: string) => text.split(/\s+/).filter((t) => /[A-Za-z]/.test(t)).length;
/** Sentences in an answer: a full stop before a space ends one; "0.50 microVMs" and "c8i.xlarge" hold none. */
const sentences = (text: string) => (text.match(/[.?!](?=\s)/g)?.length ?? 0) + 1;

/** Deep merge as expand.py does it: objects key by key, lists and values replace. */
function merge(base: object, changes: object): object {
  const out: Record<string, unknown> = structuredClone(base) as Record<string, unknown>;
  for (const [k, v] of Object.entries(changes)) {
    out[k] =
      v && typeof v === 'object' && !Array.isArray(v) && out[k] && typeof out[k] === 'object'
        ? merge(out[k] as object, v as object)
        : structuredClone(v);
  }
  return out;
}

/** A synthetic campaign's title starts with the word Synthetic ("Synthetic host sizes"). */
const SYNTHETIC_TITLE = /^Synthetic\b/;

function syntheticMarks(p: string[], where: string, x: { id: string; title?: string; synthetic?: true }) {
  if (x.synthetic) {
    if (!x.id.endsWith('-synthetic')) p.push(`${where}: synthetic, but the id doesn't end in -synthetic`);
    if (x.title !== undefined && !SYNTHETIC_TITLE.test(x.title)) p.push(`${where}: synthetic, but the title doesn't start with "Synthetic"`);
  } else {
    if (x.id.endsWith('-synthetic')) p.push(`${where}: id ends in -synthetic but synthetic is not true`);
    if (x.title !== undefined && SYNTHETIC_TITLE.test(x.title)) p.push(`${where}: title says Synthetic but synthetic is not true`);
  }
}

/** Rule 6 and the outcome's fields, from a run's entry. */
function replicaOf(r: RunEntry): ReplicaResult {
  return {
    run: r.id,
    host_vcpus: r.host.vcpus,
    tested_successfully: r.result.tested_successfully,
    first_failed: r.result.first_failed,
    stopped_early: r.stopped_early,
    per_host_vcpu: r.result.per_host_vcpu,
    midpoint_per_host_vcpu: r.result.midpoint_per_host_vcpu,
    cost_per_1000_tasks: r.result.cost_per_1000_tasks,
  };
}

export function checkIndex(index: Index): string[] {
  const p: string[] = [];
  if (index.schema !== SCHEMA) p.push(`index: schema ${index.schema}`);
  const ids = index.campaigns.map((c) => c.id);
  if (new Set(ids).size !== ids.length) p.push('index: duplicate campaign ids');
  if (index.featured !== null && !ids.includes(index.featured)) p.push(`index: featured ${index.featured} is not listed`);
  if (index.featured === null && ids.length) p.push('index: featured is null but campaigns are listed');
  for (let i = 1; i < index.campaigns.length; i++) {
    if (index.campaigns[i - 1].started < index.campaigns[i].started) p.push('index: campaigns are not newest first');
  }
  for (const c of index.campaigns) {
    const w = `index ${c.id}`;
    if (!ID.test(c.id)) p.push(`${w}: bad id`);
    if (!WHEN.test(c.started)) p.push(`${w}: started ${c.started} is not ISO UTC to the minute`);
    syntheticMarks(p, w, c);
    if (!same(c.outcomes.map((o) => o.spec), c.specs.map((s) => s.name))) p.push(`${w}: one outcome per spec, in spec order`);
    if (c.featured_spec !== undefined && !c.specs.some((s) => s.name === c.featured_spec)) {
      p.push(`${w}: featured_spec ${c.featured_spec} is not one of its specs`);
    }
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
    for (const k of ['id', 'title', 'question', 'answer', 'started', 'status', 'synthetic', 'before_campaigns', 'outcomes'] as const) {
      if (!same(entry[k], doc[k])) p.push(`${w}: ${k} differs from the index entry`);
    }
    if (entry.replicas !== doc.definition.replicas) p.push(`${w}: replicas differ from the index entry`);
    if (entry.runs !== doc.runs.length) p.push(`${w}: runs differ from the index entry`);
    if (!same(entry.specs, doc.specs.map((s) => ({ name: s.name, label: s.label })))) p.push(`${w}: specs differ from the index entry`);
  }
  const def = doc.definition;
  // The document's question is the definition's or the catalog's rewording of it (D93), so only the name must match.
  if (def.name !== doc.id) p.push(`${w}: the definition's name differs`);
  if (typeof doc.question !== 'string' || !doc.question.trim()) p.push(`${w}: the question is empty`);
  if (doc.answer !== undefined && (typeof doc.answer !== 'string' || !/\.$/.test(doc.answer.trim()) || sentences(doc.answer.trim()) > 2)) {
    p.push(`${w}: the answer is one or two plain sentences ending in a full stop`);
  }
  if (doc.before_campaigns && (doc.specs.length !== 1 || def.replicas !== 1)) p.push(`${w}: a campaign of one has one spec and one replica`);
  if (def.shutdown_after_minutes === undefined && !doc.reconstructed) p.push(`${w}: only a reconstructed definition lacks shutdown_after_minutes`);

  // D73: what the site links on GitHub. A synthetic campaign links nothing; a real one its definition's file (a
  // reconstructed definition, its pre-registration), and each run the commit of the harness it used, abbreviated.
  if (doc.preregistration && !COMMIT.test(doc.preregistration.commit)) {
    p.push(`${w}: preregistration.commit should be a commit abbreviated to 10 characters`);
  }
  const defPath = doc.reconstructed ? doc.preregistration?.path : `experiments/campaigns/${doc.id}.json`;
  if (doc.definition_path === undefined) p.push(`${w}: definition_path is missing (null when there is none)`);
  else if (doc.definition_path !== null && (doc.synthetic || doc.definition_path !== defPath)) {
    p.push(`${w}: definition_path should be ${doc.synthetic || !defPath ? 'null' : defPath}`);
  }
  for (const r of doc.runs) {
    if (r.harness_commit === undefined) p.push(`${w} run ${r.id}: harness_commit is missing (null when there is none)`);
    else if (r.harness_commit !== null && (doc.synthetic || !COMMIT.test(r.harness_commit))) {
      p.push(`${w} run ${r.id}: harness_commit should be ${doc.synthetic ? 'null in a synthetic campaign' : 'a commit abbreviated to 10 characters, or null'}`);
    }
  }

  // Specs: the definition's, in order, each the base merged with its changes.
  const names = doc.specs.map((s) => s.name);
  if (!same(names, Object.keys(def.specs))) p.push(`${w}: specs differ from the definition's`);
  // Changes are between what the base and the spec run: an optional field either leaves out is at its default, so a
  // list the spec writes equal to the default ([] extra Chromium flags) is no change, and one that isn't is compared
  // against the default, item by item in order.
  const baseFlat = filled(def.base);
  for (const s of doc.specs) {
    const ws = `${w} spec ${s.name}`;
    if (!ID.test(s.name)) p.push(`${ws}: bad name`);
    if (!same(merge(def.base, def.specs[s.name] ?? {}), s.spec)) p.push(`${ws}: spec isn't the base merged with its changes`);
    const flat = filled(s.spec);
    const differ = [...new Set([...Object.keys(flat), ...Object.keys(baseFlat)])].filter((k) => !same(flat[k], baseFlat[k]));
    if (!same(s.changes.map((c) => c.path).sort(), differ.sort())) p.push(`${ws}: changes don't list exactly where it differs from the base`);
    for (const c of s.changes) {
      if (!same(c.base, baseFlat[c.path]) || !same(c.value, flat[c.path])) p.push(`${ws}: change ${c.path} has the wrong values`);
    }
    // The why is the definition's or the catalog's rewording of it (D93), so only its shape is checked here.
    if (s.why !== undefined && (typeof s.why !== 'string' || !s.why.trim())) p.push(`${ws}: why is a phrase or absent`);
    if (s.short !== undefined && (typeof s.short !== 'string' || !s.short.trim() || wordCount(s.short) > 4)) {
      p.push(`${ws}: short is a name of at most four words`);
    }
    if (s.host.instance_type !== s.spec.worker_host.instance_type) p.push(`${ws}: host isn't the spec's instance type`);
    if (s.host.host_kind !== hostKindOf(s.host.instance_type)) p.push(`${ws}: host kind doesn't follow from the instance type`);
    if (s.spec.densities.some((d, i) => !Number.isInteger(d) || d < 1 || (i > 0 && d <= s.spec.densities[i - 1]))) {
      p.push(`${ws}: densities must be ascending positive integers`);
    }
  }

  // Runs: ids, order, results recomputed from their densities (rules 4 and 6).
  const runIds = doc.runs.map((r) => r.id);
  if (new Set(runIds).size !== runIds.length) p.push(`${w}: duplicate run ids`);
  const expectedOrder = doc.specs.flatMap((s) =>
    doc.runs.filter((r) => r.spec === s.name).sort((a, b) => a.replica - b.replica).map((r) => r.id),
  );
  if (!same(runIds, expectedOrder)) p.push(`${w}: runs are not in spec order, then replica order`);
  const complete = doc.runs.length === doc.specs.length * def.replicas && !doc.runs.some((r) => r.stopped_early);
  if (doc.status !== (complete ? 'complete' : 'partial')) p.push(`${w}: status should be ${complete ? 'complete' : 'partial'}`);
  for (const r of doc.runs) {
    const wr = `${w} run ${r.id}`;
    const spec = doc.specs.find((s) => s.name === r.spec);
    if (!spec) {
      p.push(`${wr}: unknown spec ${r.spec}`);
      continue;
    }
    if (r.id !== `${r.spec}-r${r.replica}`) p.push(`${wr}: id should be ${r.spec}-r${r.replica}`);
    if (r.replica < 1 || r.replica > def.replicas) p.push(`${wr}: replica out of range`);
    if (r.host.instance_type !== spec.host.instance_type) p.push(`${wr}: ran on ${r.host.instance_type}, its spec says ${spec.host.instance_type}`);
    if (r.host.host_kind !== spec.host.host_kind) p.push(`${wr}: host kind differs from its spec's`);
    if (!same(r.by_density.map((b) => b.density), spec.spec.densities)) p.push(`${wr}: by_density doesn't list the spec's densities`);
    for (const b of r.by_density) {
      if (b.trial_results.length !== b.trials || b.trial_results.filter(Boolean).length !== b.passed) {
        p.push(`${wr} density ${b.density}: trial_results disagree with passed and trials`);
      }
    }
    const core = runResultCore(r.by_density);
    for (const k of ['tested_successfully', 'first_failed', 'gap', 'not_tested'] as const) {
      if (!same(core[k], r.result[k])) p.push(`${wr}: result.${k} is ${JSON.stringify(r.result[k])}, rule 4 gives ${JSON.stringify(core[k])}`);
    }
    const t = r.result.tested_successfully;
    if (!close(r.result.per_host_vcpu, t === null ? null : t / r.host.vcpus)) p.push(`${wr}: per_host_vcpu ≠ result ÷ host vCPUs`);
    if (!close(r.result.midpoint_per_host_vcpu, midpoint(t, r.result.first_failed, r.host.vcpus))) p.push(`${wr}: midpoint doesn't follow rule 6`);
    if ((r.result.first_failed === null) !== (r.result.limit === null)) p.push(`${wr}: a limit exists exactly when a density failed`);
    if (r.result.limit && r.result.limit.density !== r.result.first_failed) p.push(`${wr}: limit.density ≠ first_failed`);
    if (t === null && r.result.cost_per_1000_tasks !== null) p.push(`${wr}: cost only when a density passed`);
  }

  // Outcomes per spec, from the runs.
  if (!same(doc.outcomes.map((o) => o.spec), names)) p.push(`${w}: one outcome per spec, in spec order`);
  for (const o of doc.outcomes) {
    const want = doc.runs.filter((r) => r.spec === o.spec).sort((a, b) => a.replica - b.replica).map(replicaOf);
    if (!same(o.replicas, want)) p.push(`${w}: outcome for ${o.spec} doesn't match its runs`);
    if (!close(o.midpoint_per_host_vcpu, meanMidpoint(want.map((x) => x.midpoint_per_host_vcpu)))) {
      p.push(`${w}: ${o.spec}'s midpoint isn't the mean of its replicas'`);
    }
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
  for (const k of ['spec', 'replica', 'stopped_early', 'started', 'duration_s', 'harness_commit', 'host', 'result'] as const) {
    if (!same(entry[k], run[k])) p.push(`${w}: ${k} differs from the campaign's entry`);
  }
  const briefOf = (b: RunEntry['by_density'][number]) =>
    ({ density: b.density, result: b.result, passed: b.passed, trials: b.trials, trial_results: b.trial_results });
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
      if (!spec.spec.densities.includes(t.density)) p.push(`${wt}: density ${t.density} isn't in the spec`);
    } else {
      if (parsed.role !== t.role) p.push(`${wt}: id doesn't match role ${t.role}`);
      const k = (nth.get(t.role) ?? 0) + 1;
      if (parsed.nth !== k) p.push(`${wt}: should be ${k === 1 ? t.role : `${t.role}-${k}`}`);
      nth.set(t.role, k);
      if (t.number !== null || t.passed !== null) p.push(`${wt}: a labelled trial has no number and no pass value`);
      if (!t.excluded_because) p.push(`${wt}: say why it isn't judged`);
      if (t.cost_per_1000_tasks !== null) p.push(`${wt}: no cost for a labelled trial`);
    }
    if (t.tasks.of > t.density || t.ready.microvms > t.density) p.push(`${wt}: more tasks or microVMs than its density`);
  }

  // Per density: rule 3 and the derived columns.
  const briefs = densityBriefs(spec.spec.densities, run.trials);
  if (!same(briefs, run.by_density.map(briefOf))) {
    p.push(`${w}: by_density doesn't follow rule 3 from the trials`);
  }
  for (const d of run.by_density) {
    const wd = `${w} density ${d.density}`;
    const at = run.trials.filter((t) => t.counts && t.density === d.density).sort((a, b) => a.number! - b.number!);
    if (!same(d.trial_ids, at.map((t) => t.id))) p.push(`${wd}: trial_ids`);
    if (!same(d.trial_results, at.map((t) => t.passed))) p.push(`${wd}: trial_results`);
    if (d.vcpus_allocated !== d.density * spec.spec.microvm.vcpus) p.push(`${wd}: vcpus_allocated`);
    if (d.cost_per_1000_tasks && d.result !== 'passed') p.push(`${wd}: cost only at densities that passed`);
  }
  const t = run.result.tested_successfully;
  if (t !== null) {
    const d = run.by_density.find((b) => b.density === t);
    if (!same(d?.cost_per_1000_tasks ?? null, run.result.cost_per_1000_tasks)) p.push(`${w}: result cost ≠ cost at its result's density`);
  }
  for (const tr of run.trials.filter((x) => x.counts && x.cost_per_1000_tasks && x.marks.release_ms !== null && x.marks.last_return_ms !== null)) {
    const secs = (tr.marks.last_return_ms! - tr.marks.release_ms!) / 1000;
    if (!close(tr.cost_per_1000_tasks!.execution, costPer1000(spec.host.price_usd_per_hour, secs, tr.density), 0.02)) {
      p.push(`${w} trial ${tr.id}: execution cost doesn't follow rule 5`);
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
  const full = fullSizeTrials(run.trials, run.result.tested_successfully, run.result.first_failed).has(doc.id);
  if (doc.full_size_screenshots !== full) p.push(`${w}: full_size_screenshots should be ${full} (rule 10)`);
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
