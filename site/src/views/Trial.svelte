<script lang="ts">
  // One trial: its verdict against each criterion, every microVM's start and steps on one clock, the limiting
  // resource over time with the rule that judged it, the final screens, and (for the illustration) the filmstrip.
  import { loadCampaign, loadRun, loadTrial } from '../lib/data';
  import { href } from '../lib/router';
  import * as f from '../lib/format';
  import { coresStack, lanes, limitPanels } from '../lib/shape';
  import {
    GUEST_GROUP_LABEL,
    HOST_CONSUMER_LABEL,
    RULE_LABEL,
    SUBJECT_LABEL,
    trialLabel,
    VERDICT_LABEL,
  } from '../lib/glossary';
  import { GUEST_GROUPS, HOST_CONSUMERS, STEP_NAMES, type RuleKey, type SpecDoc } from '../lib/types';
  import Failed from '../components/Failed.svelte';
  import SyntheticBanner from '../components/SyntheticBanner.svelte';
  import Term from '../components/Term.svelte';
  import LaneChart from '../components/LaneChart.svelte';
  import TimeChart from '../components/TimeChart.svelte';
  import ShareBar from '../components/ShareBar.svelte';
  import Screenshot from '../components/Screenshot.svelte';

  let { campaign, run, trial, microvm }: { campaign: string; run: string; trial: string; microvm: number | null } =
    $props();
  const data = $derived(Promise.all([loadCampaign(campaign), loadRun(campaign, run)]));
  const detail = $derived(loadTrial(campaign, run, trial));

  const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  const rule = (spec: SpecDoc, key: RuleKey) => spec.rules.find((x) => x.key === key);
  /** A rule's value, never rounded across its threshold. */
  function ruleValue(spec: SpecDoc, key: RuleKey, v: number | null): string {
    if (v === null) return 'not recorded';
    const t = rule(spec, key)?.threshold;
    if (key.endsWith('_fraction')) return t === undefined ? f.num(v, 3) : f.vs(v, t, 2);
    return `${t === undefined ? f.num(v, 1) : f.vs(v, t, 1)}%`;
  }
  const kib = (bytes: number) => (bytes >= 1024 * 1024 ? `${f.num(bytes / 1024 / 1024, 1)} MiB` : `${f.num(bytes / 1024)} KiB`);
</script>

{#await data}
  <p class="status">Loading…</p>
{:then [c, r]}
  {@const s = r.trials.find((t) => t.id === trial)}
  {@const spec = c.specs.find((x) => x.name === r.spec)!}
  {#if !s}
    <Failed error={new Error(`Run ${r.id} has no trial ${trial}.`)} />
  {:else}
    {@const microvmHref = (i: number) => href({ name: 'trial', campaign: c.id, run: r.id, trial: s.id, microvm: i })}
    <p class="crumbs">
      <a href={href({ name: 'campaigns' })}>Campaigns</a> ›
      <a href={href({ name: 'campaign', campaign: c.id })}>{c.title}</a> ›
      <a href={href({ name: 'run', campaign: c.id, run: r.id, density: null })}>{r.id}</a> ›
      <a href={href({ name: 'run', campaign: c.id, run: r.id, density: s.density })}>density {s.density}</a> ›
      {s.id}
    </p>
    {#if r.synthetic}<SyntheticBanner />{/if}

    <h1>{capital(trialLabel(s))}</h1>
    {#if !s.counts}
      <p class="lead">This <Term k="trial" /> doesn't count toward the result: {s.excluded_because}</p>
    {:else if s.passed}
      <p class="lead pass">✓ Passed every criterion{s.at_limit ? ', while a limit rule fired' : ''}.</p>
    {:else}
      <p class="lead fail">× Failed: {s.failed.join('; ')}.</p>
    {/if}
    <p class="meta">
      {f.count(s.density, 'microVM')} requested at the same moment (t = 0).
      {#if s.marks.all_ready_ms !== null}All ready at {f.seconds(s.marks.all_ready_ms)}{:else}Never all ready{/if}{#if s.marks.release_ms !== null}; tasks released at {f.seconds(s.marks.release_ms)}{/if}{#if s.marks.last_return_ms !== null}; last task back at {f.seconds(s.marks.last_return_ms)}{/if}{#if s.marks.clean_ms !== null}; host clean at {f.seconds(s.marks.clean_ms)}{/if}.
      {s.tasks.ok} of {s.tasks.of} tasks ok.
      {#if s.fault}Fault injected: {s.fault}.{/if}
    </p>

    <h2>Criteria</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Criterion</th><th class="num">This trial</th><th class="num">Target</th><th>Met</th></tr></thead>
        <tbody>
          <tr>
            <td>{s.density === 1 ? 'The microVM' : `All ${s.density} microVMs`} ready</td>
            <td class="num">{s.ready.all_ready_ms !== null ? f.seconds(s.ready.all_ready_ms) : `${s.ready.microvms} ready`}</td>
            <td class="num">within {f.seconds(s.ready.limit_ms, 0)}</td>
            <td class={s.ready.met ? 'pass' : 'fail'}>{s.ready.met ? '✓ yes' : '× no'}</td>
          </tr>
          <tr>
            <td>Every task succeeded</td>
            <td class="num">{s.tasks.ok} of {s.tasks.of}</td>
            <td class="num">all</td>
            <td class={s.tasks.ok === s.tasks.of ? 'pass' : 'fail'}>{s.tasks.ok === s.tasks.of ? '✓ yes' : '× no'}</td>
          </tr>
          {#each s.checks as ch (`${ch.subject} ${ch.stat}`)}
            <tr>
              <td>{capital(SUBJECT_LABEL[ch.subject])} <Term k={ch.stat} /></td>
              <td class="num">{f.vs(ch.value_ms, ch.target_ms)} ms</td>
              <td class="num">≤ {f.num(ch.target_ms)} ms</td>
              <td class={ch.met ? 'pass' : 'fail'}>{ch.met ? '✓ yes' : '× no'}</td>
            </tr>
          {/each}
          <tr>
            <td>Nothing left on the host afterwards</td><td></td><td></td>
            <td class={s.clean ? 'pass' : 'fail'}>{s.clean ? '✓ yes' : '× no'}</td>
          </tr>
        </tbody>
      </table>
    </div>

    {#await detail}
      <p class="status">Loading the trial…</p>
    {:then d}
      {@const L = lanes(d)}
      {@const domain = [0, Math.max(L.domain[1], d.window_ms[1])] as [number, number]}
      {@const sel = microvm === null ? null : d.microvms.find((m) => m.index === microvm) ?? null}
      {@const panels = limitPanels(d, s, spec)}
      {@const stack = coresStack(d)}
      <h2>MicroVMs</h2>
      <p class="muted">
        Each row is one <Term k="microvm" />: grey while it starts, then its task's five steps. Select a number for that
        microVM's detail.
      </p>
      <LaneChart lanes={L.lanes} {domain} marks={d.marks} selected={microvm} hrefFor={microvmHref} />

      {#if sel}
        {@const lim = d.limit.microvms.find((x) => x.index === sel.index)}
        <section class="panel selected" aria-labelledby="microvm-{sel.index}">
          <h3 id="microvm-{sel.index}">
            MicroVM {sel.index}
            <a class="close" href={href({ name: 'trial', campaign: c.id, run: r.id, trial: s.id, microvm: null })}>close</a>
          </h3>
          <div class="sel-grid">
            <div>
              <p>
                Shopped for {sel.product}. {sel.task ? (sel.task.ok ? 'Task succeeded' : `Task failed at ${sel.task.failed_step ? SUBJECT_LABEL[sel.task.failed_step] : 'an unknown step'} (${sel.task.failure_category ?? 'no category'})`) : 'No task ran'}.
                {sel.ready_ms !== null ? `Ready at ${f.seconds(sel.ready_ms)}.` : 'Never ready.'}
              </p>
              <table>
                <tbody>
                  {#if sel.boot}
                    <tr><th scope="row">Guest kernel started</th><td class="num">{f.seconds(sel.boot.kernel_start_ms)}</td></tr>
                    <tr><th scope="row">Chromium ready</th><td class="num">{f.seconds(sel.boot.chromium_ready_ms)}</td></tr>
                  {/if}
                  {#each sel.steps as st (st.name)}
                    <tr>
                      <th scope="row">{capital(SUBJECT_LABEL[st.name])}</th>
                      <td class="num {st.end_ms - st.start_ms > spec.criteria.step_p50_ms ? 'fail' : ''}">
                        {f.ms(st.end_ms - st.start_ms)}{st.ok ? '' : ', failed'}
                      </td>
                    </tr>
                  {/each}
                  {#if sel.task}
                    <tr><th scope="row">Whole task</th><td class="num">{f.ms(sel.task.task_ms)}</td></tr>
                    <tr><th scope="row">Downloaded</th><td class="num">{kib(sel.task.bytes)} in {f.num(sel.task.requests)} requests</td></tr>
                    <tr><th scope="row">Chromium memory</th><td class="num">{f.num(sel.task.chromium_rss_mib)} MiB</td></tr>
                  {/if}
                  {#if lim}
                    <tr><th scope="row">Its vCPUs used</th><td class="num">{f.num(lim.vcpu_s, 2)} CPU-s</td></tr>
                    <tr><th scope="row">Memory peak</th><td class="num">{f.num(lim.mem_peak_mib)} MiB</td></tr>
                  {/if}
                </tbody>
              </table>
            </div>
            <Screenshot sha={sel.img ?? null} alt="The final screen of microVM {sel.index}'s task" caption="Final screen" />
          </div>
        </section>
      {/if}

      <details>
        <summary>Show every microVM's times as a table</summary>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="num">MicroVM</th>
                <th>Product</th>
                <th>Outcome</th>
                <th class="num">Ready, s</th>
                <th class="num">Task, ms</th>
                {#each STEP_NAMES as n (n)}<th class="num">{capital(SUBJECT_LABEL[n])}, ms</th>{/each}
                <th class="num">Memory peak</th>
              </tr>
            </thead>
            <tbody>
              {#each d.microvms as m (m.index)}
                {@const lim = d.limit.microvms.find((x) => x.index === m.index)}
                <tr class:selected={microvm === m.index}>
                  <td class="num"><a href={microvmHref(m.index)}>{m.index}</a></td>
                  <td>{m.product}</td>
                  <td>{m.task && !m.task.ok ? `${m.task.failure_category} at ${m.task.failed_step}` : m.outcome}</td>
                  <td class="num">{m.ready_ms !== null ? f.num(m.ready_ms / 1000, 2) : 'never'}</td>
                  <td class="num">{m.task ? f.num(m.task.task_ms) : '–'}</td>
                  {#each STEP_NAMES as n (n)}
                    {@const st = m.steps.find((x) => x.name === n)}
                    {@const over = st && st.end_ms - st.start_ms > spec.criteria.step_p50_ms}
                    <td class="num {over ? 'fail' : ''}">{st ? f.num(st.end_ms - st.start_ms) : '–'}</td>
                  {/each}
                  <td class="num">{lim ? `${f.num(lim.mem_peak_mib)} MiB` : '–'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <p class="muted small">A step slower than the {f.num(spec.criteria.step_p50_ms)} ms median target is marked.</p>
      </details>

      <h2>What limited it</h2>
      {#if s.attribution}
        {@const a = s.attribution}
        <p>
          <strong>{capital(a.verdicts.map((v) => VERDICT_LABEL[v]).join(', '))}</strong>, by the rules fixed in the spec,
          over the task window (release to last return, shaded below).
          {#if a.missing.length}Not recorded: {a.missing.map((k) => RULE_LABEL[k]).join(', ')}.{/if}
        </p>
        <div class="charts">
          {#each panels as p (p.key)}
            <TimeChart
              title={p.title}
              unit={p.unit}
              t={p.t}
              series={p.series.map((x) => ({ ...x, color: 'var(--series-1)' }))}
              many={p.many}
              {domain}
              window={a.window_ms}
              threshold={p.threshold ?? null}
              digits={p.unit === '' ? 2 : 1}
            />
          {/each}
          {#if stack}
            <TimeChart
              title="Host CPU by process, cores"
              unit=""
              t={[stack.t]}
              series={stack.layers.map((l, i) => ({ label: HOST_CONSUMER_LABEL[l.key], values: l.values, color: `var(--series-${i + 1})` }))}
              stack
              {domain}
              window={a.window_ms}
              ceiling={{ value: r.host.vcpus, label: `${r.host.vcpus} host vCPUs` }}
            />
          {/if}
        </div>
        {#if stack}
          <ul class="key">
            {#each HOST_CONSUMERS as k, i (k)}<li><span class="sw" style:background="var(--series-{i + 1})"></span>{HOST_CONSUMER_LABEL[k]}</li>{/each}
          </ul>
        {/if}

        <div class="table-wrap">
          <table>
            <thead><tr><th>Rule, over the task window</th><th class="num">This trial</th><th class="num">Rule</th><th>Fired</th></tr></thead>
            <tbody>
              {#each a.rules as x (x.key)}
                {@const def = rule(spec, x.key)}
                <tr>
                  <td>{RULE_LABEL[x.key]}</td>
                  <td class="num">{ruleValue(spec, x.key, x.value)}</td>
                  <td class="num">{def ? `${def.op === '>=' ? '≥' : '<'} ${def.threshold}${x.key.endsWith('_pct') ? '%' : ''}` : '–'}</td>
                  <td class={x.fired ? 'fail' : 'muted'}>{x.fired ? 'fired' : 'no'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <div class="shares">
          <ShareBar
            title="Host CPU in the task window, by process"
            parts={HOST_CONSUMERS.map((k) => ({ label: HOST_CONSUMER_LABEL[k], share: a.host_cpu_s.busy ? a.host_cpu_s[k] / HOST_CONSUMERS.reduce((t, x) => t + a.host_cpu_s[x], 0) : 0, detail: `${f.num(a.host_cpu_s[k], 1)} CPU-s` }))}
          />
          {#if a.guest_share}
            <ShareBar title="CPU inside the microVMs, by process" parts={GUEST_GROUPS.map((g) => ({ label: GUEST_GROUP_LABEL[g], share: a.guest_share![g] }))} />
          {/if}
        </div>
      {:else}
        <p>Not recorded for this trial.</p>
      {/if}

      <h2>Final screens</h2>
      {#if d.microvms.some((m) => m.img)}
        <p class="muted">What each microVM's browser showed when its task ended. Select one for the full size.</p>
        <div class="shots">
          {#each d.microvms as m (m.index)}
            <Screenshot sha={m.img ?? null} alt="The final screen of microVM {m.index}'s task" caption="microVM {m.index} · {m.product}" />
          {/each}
        </div>
      {:else}
        <p class="muted">No screenshots were recorded for this trial.</p>
      {/if}

      {#if d.filmstrip}
        <h2>Filmstrip</h2>
        <p class="muted">
          MicroVM {d.filmstrip.microvm}'s screen after each step. Untimed: taking these screenshots added time inside
          the task, which is why this trial doesn't count.
        </p>
        <div class="shots film">
          {#each d.filmstrip.frames as fr, i (fr.step)}
            <Screenshot sha={fr.img} alt="After step {i + 1}, {SUBJECT_LABEL[fr.step]}" caption="{i + 1}. {capital(SUBJECT_LABEL[fr.step])}" />
          {/each}
        </div>
      {/if}

      {#if d.settle}
        <p class="muted small">
          Before the trial the host sat idle for {d.settle.seconds} s, at {f.pct(d.settle.cpu_util_mean_pct)} CPU on average.
        </p>
      {/if}
    {:catch e}
      <Failed error={e} />
    {/await}
  {/if}
{:catch e}
  <Failed error={e} />
{/await}

<style>
  tr.selected td {
    background: var(--highlight);
  }
  td.pass,
  td.fail {
    white-space: nowrap;
  }
  .selected h3 {
    margin-top: 0;
    display: flex;
    gap: 12px;
    align-items: baseline;
  }
  .close {
    font-size: 0.85rem;
    font-weight: 400;
  }
  .sel-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr));
    gap: 16px;
  }
  .sel-grid table {
    min-width: 0;
  }
  .sel-grid th {
    font-weight: 400;
    white-space: normal;
  }
  .charts {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr));
    gap: 8px 24px;
  }
  .key {
    list-style: none;
    padding: 0;
    margin: 6px 0 12px;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
    font-size: 0.85rem;
    color: var(--ink-2);
    max-width: none;
  }
  .sw {
    display: inline-block;
    width: 12px;
    height: 10px;
    border-radius: 2px;
    margin-right: 5px;
    vertical-align: -1px;
  }
  .shares {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr));
    gap: 8px 32px;
  }
  .shots {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(9rem, 1fr));
    gap: 12px;
    margin: 12px 0;
  }
  .shots.film {
    grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
  }
  .small {
    font-size: 0.85rem;
  }
</style>
