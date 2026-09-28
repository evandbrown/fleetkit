<script lang="ts">
  // At the limit: for each spec, one trial at its first failing density (limitPick), shown the way the trial page
  // shows it but compact: the steps against their limits, the host CPU by process over time, the rule that ran out,
  // and every microVM's lane. A switch sets the last density that passed beside it, in the same run, so the jump is
  // visible. Tabs choose the spec when there are several. A trial's own document loads only when this comes near the
  // screen, and only for the trial shown.
  import { onMount } from 'svelte';
  import { HOST_CONSUMER_COLOR, specColor } from '../lib/colors';
  import { loadTrial } from '../lib/data';
  import * as f from '../lib/format';
  import { HOST_CONSUMER_LABEL, RULE_LABEL, trialLabel, VERDICT_SHORT } from '../lib/glossary';
  import { href } from '../lib/router';
  import { coresStack, firedRule, lanes, limitPanels, limitPick, type SpecRef } from '../lib/shape';
  import type { CampaignDoc, RuleDef, RunDoc } from '../lib/types';
  import LaneChart from './LaneChart.svelte';
  import Mark from './Mark.svelte';
  import StepChart from './StepChart.svelte';
  import TimeChart from './TimeChart.svelte';

  let { refs, c, runs }: { refs: SpecRef[]; c: CampaignDoc; runs: Map<string, RunDoc> } = $props();

  const picks = $derived(refs.map((r, i) => ({ i, pick: limitPick(r, c, runs) })).filter((x) => x.pick !== null));
  let sel = $state(0);
  const cur = $derived(picks[Math.min(sel, picks.length - 1)] ?? null);
  const pick = $derived(cur?.pick ?? null);
  const many = $derived(picks.length > 1);
  /** Which trial is shown: the first failure, or the last pass beside it. */
  let view = $state<'fail' | 'pass'>('fail');
  const shown = $derived(
    pick && view === 'pass' && pick.pass ? { trial: pick.pass.trial, density: pick.pass.density, atFailure: false } : pick ? { trial: pick.trial, density: pick.density, atFailure: pick.failed } : null,
  );
  /** The rule that ran out at the failure: the chart shows its metric for both densities. */
  const rule = $derived(
    pick ? firedRule(pick.run.trials.filter((t) => t.counts && t.density === pick.density), c.rules, pick.ranOut) : null,
  );
  const ruleText = (r: RuleDef) => {
    const pct = r.key.endsWith('_pct') || r.key.endsWith('_fraction');
    const v = r.key.endsWith('_fraction') ? r.threshold * 100 : r.threshold;
    return `${RULE_LABEL[r.key]} ${r.op === '>=' ? '≥' : '<'} ${pct ? `${f.num(v)}%` : f.num(v, 2)}`;
  };

  let root: HTMLElement | undefined = $state();
  let near = $state(false);
  onMount(() => {
    if (!root || typeof IntersectionObserver === 'undefined') {
      near = true;
      return;
    }
    const io = new IntersectionObserver((es) => {
      if (es.some((e) => e.isIntersecting)) {
        near = true;
        io.disconnect();
      }
    }, { rootMargin: '600px 0px' });
    io.observe(root);
    return () => io.disconnect();
  });

  const detail = $derived(near && pick && shown ? loadTrial(c.id, pick.run.id, shown.trial.id) : null);

  let tablist: HTMLElement | undefined = $state();
  function key(e: KeyboardEvent) {
    const n = picks.length;
    let next = -1;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (sel + 1) % n;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (sel - 1 + n) % n;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = n - 1;
    if (next < 0) return;
    e.preventDefault();
    sel = next;
    tablist?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  }
</script>

<div class="atlimit" bind:this={root}>
  {#if many}
    <div class="choose">
      <span class="tl" id="limit-label">Spec</span>
      <!-- svelte-ignore a11y_interactive_supports_focus -->
      <div class="tabs" role="tablist" aria-labelledby="limit-label" bind:this={tablist} onkeydown={key}>
        {#each picks as p, k (p.pick!.ref.spec.name)}
          <button
            type="button"
            role="tab"
            id="limit-tab-{k}"
            aria-selected={k === sel}
            aria-controls="limit-panel"
            tabindex={k === sel ? 0 : -1}
            onclick={() => {
              sel = k;
              view = 'fail';
            }}
          >
            <i class="sw" style:background={specColor(p.i)}></i>
            <span class="tn">{p.pick!.ref.groupLabel}</span>
            <span class="ta">{p.pick!.failed ? `fails at ${p.pick!.density}` : `passed ${p.pick!.density}`}</span>
          </button>
        {/each}
      </div>
    </div>
  {/if}

  {#if pick && shown}
    {@const t = shown.trial}
    {@const r = pick.run}
    {@const crit = pick.ref.spec.spec.criteria}
    {@const trialHref = href({ name: 'trial', campaign: c.id, run: r.id, trial: t.id, microvm: null })}
    <div class="card" id="limit-panel" role={many ? 'tabpanel' : undefined} aria-labelledby={many ? `limit-tab-${sel}` : undefined}>
      {#if pick.pass}
        <div class="switch" role="group" aria-label="Density shown">
          <span class="sl">Show</span>
          <button type="button" aria-pressed={view === 'pass'} onclick={() => (view = 'pass')}>
            <Mark kind="pass" size={10} />Last pass <strong>{pick.pass.density}</strong>
          </button>
          <button type="button" aria-pressed={view === 'fail'} onclick={() => (view = 'fail')}>
            <Mark kind="fail" size={9} />First failure <strong>{pick.density}</strong>
          </button>
        </div>
      {/if}
      <div class="head">
        <div class="who">
          <p class="title">
            <strong>Density {shown.density}</strong>
            <span class="muted">{trialLabel(t).replace(/ at density \d+$/, '')}{c.definition.replicas > 1 ? ` · replica ${r.replica}` : ''}</span>
          </p>
          <p class="result">
            {#if t.passed}
              <span class="pass"><Mark kind="pass" size={12} /> Passed</span>
            {:else}
              <span class="fail"><Mark kind="fail" size={11} /> Failed</span>
              <span class="why">{t.failed.join('; ')}</span>
            {/if}
            {#if pick.ranOut && view === 'fail'}
              <span class="out">Ran out <strong>{VERDICT_SHORT[pick.ranOut]}</strong>{#if rule}<span class="rule">{ruleText(rule)}</span>{/if}</span>
            {/if}
          </p>
        </div>
        <a class="button open" href={trialHref}>Open this trial →</a>
      </div>

      <div class="grid">
        <div class="cell">
          <h4>Steps (ms)</h4>
          {#if t.checks.length}
            <StepChart checks={t.checks} criteria={crit} />
          {:else}
            <p class="muted">not recorded</p>
          {/if}
        </div>
        <div class="cell">
          {#if detail}
            {#await detail}
              <p class="status wait">Loading the trial…</p>
            {:then d}
              {@const L = lanes(d)}
              {@const domain = [0, Math.max(L.domain[1], d.window_ms[1])] as [number, number]}
              {@const stack = coresStack(d, r.host.vcpus)}
              {@const panel = limitPanels(d, t, c.rules, rule?.key ?? null)[0]}
              {@const layers = stack ? stack.layers.filter((l) => l.values.some((v) => v > 0.005)) : []}
              {#if stack}
                <TimeChart
                  title="Host CPU by process"
                  unit="vCPUs"
                  t={[stack.t]}
                  series={layers.map((l) => ({ label: HOST_CONSUMER_LABEL[l.key], values: l.values, color: HOST_CONSUMER_COLOR[l.key] }))}
                  stack
                  {domain}
                  window={t.attribution?.window_ms ?? null}
                  ceiling={{ value: r.host.vcpus, label: `${r.host.vcpus} vCPUs` }}
                  height={130}
                />
                <ul class="key" aria-label="Host CPU key">
                  {#each layers as l (l.key)}<li><i class="ksw" style:background={HOST_CONSUMER_COLOR[l.key]}></i>{HOST_CONSUMER_LABEL[l.key]}</li>{/each}
                  <li><i class="ksw window"></i>tasks running</li>
                </ul>
              {/if}
              {#if panel}
                <TimeChart
                  title={panel.threshold ? `${panel.title}, the rule that ran out` : panel.title}
                  unit={panel.unit}
                  t={panel.t}
                  series={panel.series.map((x) => ({ ...x, color: 'var(--series-1)' }))}
                  many={panel.many}
                  {domain}
                  window={t.attribution?.window_ms ?? null}
                  threshold={panel.threshold ?? null}
                  digits={panel.unit === '' ? 2 : 1}
                  height={130}
                />
              {/if}
              {#if !stack && !panel}<p class="muted">not recorded</p>{/if}
            {:catch}
              <p class="muted">not recorded</p>
            {/await}
          {:else}
            <p class="status wait">Loading the trial…</p>
          {/if}
        </div>
        <div class="cell wide">
          <h4>MicroVMs</h4>
          {#if detail}
            {#await detail}
              <p class="status wait lanes-wait">Loading the trial…</p>
            {:then d}
              {@const L = lanes(d)}
              {@const domain = [0, Math.max(L.domain[1], d.window_ms[1])] as [number, number]}
              <LaneChart
                lanes={L.lanes}
                {domain}
                marks={d.marks}
                compact
                hrefFor={(i) => href({ name: 'trial', campaign: c.id, run: r.id, trial: t.id, microvm: i })}
              />
            {:catch}
              <p class="muted">not recorded</p>
            {/await}
          {:else}
            <p class="status wait lanes-wait">Loading the trial…</p>
          {/if}
        </div>
      </div>
      {#if t.host}
        <p class="facts">
          <span>CPU busy <strong>{f.num(t.host.busy_cores, 1)}</strong> of {r.host.vcpus} vCPUs</span>
          {#if t.host.cpu_pressure_pct !== null}<span>CPU pressure <strong>{f.pct(t.host.cpu_pressure_pct, 0)}</strong></span>{/if}
          <span>Memory used <strong>{f.num(t.host.mem_used_gib, 1)}</strong> of {f.num(pick.ref.spec.host.memory_gib, 0)} GiB</span>
        </p>
      {/if}
    </div>
  {:else}
    <p class="muted">not run yet</p>
  {/if}
</div>

<style>
  .atlimit {
    margin-top: 8px;
  }
  .choose {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 12px;
    margin-bottom: 10px;
  }
  .tl {
    font-size: 0.82rem;
    font-weight: 650;
    color: var(--ink-2);
  }
  .tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding: 3px;
    border-radius: 999px;
    background: var(--surface-2);
    max-width: 100%;
  }
  /* A phone: the specs stay one row, and scroll sideways. */
  @media (max-width: 560px) {
    .choose {
      flex-wrap: nowrap;
    }
    .tabs {
      flex-wrap: nowrap;
      overflow-x: auto;
      scrollbar-width: none;
      min-width: 0;
    }
    .tabs button {
      flex: none;
      white-space: nowrap;
    }
  }
  .switch {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin: 0 0 12px;
  }
  .sl {
    font-size: 0.82rem;
    font-weight: 650;
    color: var(--ink-2);
    margin-right: 2px;
  }
  .switch button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border: 1px solid var(--rule);
    border-radius: 999px;
    background: var(--bg);
    color: var(--ink-2);
    font: inherit;
    font-size: 0.86rem;
    cursor: pointer;
  }
  .switch button:hover {
    border-color: var(--accent);
    color: var(--ink);
  }
  .switch button[aria-pressed='true'] {
    border-color: var(--accent);
    background: var(--highlight);
    color: var(--ink);
    box-shadow: inset 0 0 0 1px var(--accent);
  }
  .switch strong {
    font-variant-numeric: tabular-nums;
  }
  .rule {
    margin-left: 6px;
    font-size: 0.82rem;
    color: var(--ink-2);
  }
  .key {
    list-style: none;
    margin: 4px 0 10px;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 2px 14px;
    font-size: 0.78rem;
    color: var(--ink-2);
    max-width: none;
  }
  .key li {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .ksw {
    display: inline-block;
    width: 12px;
    height: 10px;
    border-radius: 2px;
  }
  .ksw.window {
    background: var(--surface-2);
  }
  .tabs button {
    display: inline-flex;
    align-items: baseline;
    gap: 6px;
    padding: 5px 12px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--ink-2);
    font: inherit;
    font-size: 0.86rem;
    cursor: pointer;
    min-width: 0;
  }
  .tabs button:hover {
    background: var(--bg);
    color: var(--ink);
  }
  .tabs button[aria-selected='true'] {
    background: var(--bg);
    color: var(--ink);
    box-shadow: 0 0 0 1.5px var(--accent);
  }
  .tn {
    font-weight: 650;
  }
  .ta {
    font-size: 0.78rem;
    color: var(--muted);
  }
  .sw {
    align-self: center;
    display: inline-block;
    width: 9px;
    height: 9px;
    border-radius: 50%;
  }
  .card {
    border: 1px solid var(--rule);
    border-radius: 12px;
    padding: 16px 18px 12px;
  }
  @media (max-width: 640px) {
    .card {
      padding: 12px;
    }
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: flex-start;
    gap: 8px 16px;
    margin-bottom: 8px;
  }
  .who {
    min-width: 0;
  }
  .title,
  .result {
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 2px 10px;
    max-width: none;
  }
  .title strong {
    font-size: 1.15rem;
  }
  .result {
    font-size: 0.9rem;
  }
  .result .pass,
  .result .fail {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-weight: 650;
  }
  .why {
    color: var(--critical);
    font-variant-numeric: tabular-nums;
  }
  .out {
    color: var(--ink-2);
  }
  .out strong {
    color: var(--ink);
  }
  .open {
    color: var(--accent-ink);
    border-color: var(--accent);
  }
  .open:hover {
    background: var(--highlight);
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px 32px;
  }
  @media (max-width: 800px) {
    .grid {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  .cell {
    min-width: 0;
  }
  .cell.wide {
    grid-column: 1 / -1;
  }
  h4 {
    margin: 4px 0 6px;
    font-size: 0.9rem;
    font-weight: 600;
  }
  .status {
    padding: 12px 0;
    font-size: 0.88rem;
  }
  /* Room for the charts while the trial loads, so the page below doesn't jump. */
  .wait {
    min-height: 300px;
  }
  .wait.lanes-wait {
    min-height: 200px;
  }
  .facts {
    margin: 10px 0 0;
    padding-top: 10px;
    border-top: 1px solid var(--rule);
    display: flex;
    flex-wrap: wrap;
    gap: 4px 22px;
    font-size: 0.85rem;
    color: var(--ink-2);
    max-width: none;
  }
  .facts strong {
    color: var(--ink);
    font-variant-numeric: tabular-nums;
  }
</style>
