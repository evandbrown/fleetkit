<script lang="ts">
  // One run: its result, each density it tested, what limited it (the resource and the process), every trial,
  // its spec with the inputs that differ from the campaign's base marked, the worker host as measured, and the
  // spec to copy to run it again.
  import { loadCampaign, loadRun } from '../lib/data';
  import { href } from '../lib/router';
  import * as f from '../lib/format';
  import { show } from '../lib/spec';
  import { densityRows, reproduce, runAttribution, specRows } from '../lib/shape';
  import { GUEST_GROUP_LABEL, HOST_CONSUMER_LABEL, RULE_LABEL, trialLabel, VERDICT_LABEL } from '../lib/glossary';
  import type { RuleKey, RunDoc, SpecDoc } from '../lib/types';
  import Failed from '../components/Failed.svelte';
  import SyntheticBanner from '../components/SyntheticBanner.svelte';
  import DensityStrip from '../components/DensityStrip.svelte';
  import Term from '../components/Term.svelte';
  import ShareBar from '../components/ShareBar.svelte';
  import CopyBlock from '../components/CopyBlock.svelte';

  let { campaign, run, density }: { campaign: string; run: string; density: number | null } = $props();
  const data = $derived(Promise.all([loadCampaign(campaign), loadRun(campaign, run)]));
  let reproduceAs: 'definition' | 'spec' = $state('definition');

  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

  function resultText(r: RunDoc): string {
    const x = r.result;
    if (!x) return `Incomplete${r.note ? `: ${r.note}` : ''}. A run that didn't finish has no result.`;
    const parts: string[] = [];
    parts.push(x.tested_successfully === null ? 'No density passed' : `Density ${x.tested_successfully} tested successfully`);
    if (x.first_failed !== null) parts.push(`density ${x.first_failed} failed`);
    if (x.not_tried) parts.push(`${f.range(x.not_tried)} not tried`);
    if (x.not_run.length) parts.push(`${x.not_run.join(', ')} not run`);
    return parts.join('; ') + '.';
  }

  /** A rule's value, never rounded across its threshold. */
  function ruleValue(spec: SpecDoc, key: RuleKey, v: number): string {
    const t = spec.rules.find((x) => x.key === key)?.threshold;
    if (key.endsWith('_fraction')) return t === undefined ? f.num(v, 2) : f.vs(v, t, 2);
    return `${t === undefined ? f.num(v, 1) : f.vs(v, t, 1)}%`;
  }
  const unitOf = (key: RuleKey) => (key.endsWith('_pct') ? '%' : '');
</script>

{#await data}
  <p class="status">Loading…</p>
{:then [c, r]}
  {@const spec = c.specs.find((s) => s.name === r.spec)!}
  {@const shown = density === null ? r.trials : r.trials.filter((t) => t.density === density)}
  {@const rows = densityRows(r)}
  {@const attr = r.has.attribution ? runAttribution(r) : null}
  {@const again = reproduce(c, spec)}
  <p class="crumbs">
    <a href={href({ name: 'campaigns' })}>Campaigns</a> ›
    <a href={href({ name: 'campaign', campaign: c.id })}>{c.title}</a> › {r.id}
  </p>
  {#if r.synthetic}<SyntheticBanner />{/if}

  <h1>Run {r.id}</h1>
  <p class="lead">
    {spec.label}, <Term k="replica" /> {r.replica} of {c.definition.replicas}, in
    <a href={href({ name: 'campaign', campaign: c.id })}>{c.title}</a>.
  </p>
  <p class="meta">
    Started {f.when(r.started)} and ran for {f.duration(r.duration_s)}, from commit
    <code>{r.code.commit.slice(0, 7)}</code>{r.code.dirty ? ' with uncommitted changes' : ''}.
  </p>
  <p class="result">{resultText(r)}</p>

  {#if r.result}
    <div class="facts">
      <div>
        <span class="k">Density per host vCPU</span>
        <span class="v">{r.result.per_host_vcpu !== null ? f.ratio(r.result.per_host_vcpu) : '–'}</span>
        <span class="sub">density tested successfully ÷ {r.host.vcpus} host vCPUs</span>
      </div>
      <div>
        <span class="k">vCPUs <Term k="allocated" /> per host vCPU</span>
        <span class="v">{r.result.vcpus_allocated_per_host_vcpu !== null ? f.ratio(r.result.vcpus_allocated_per_host_vcpu) : '–'}</span>
        <span class="sub">each microVM has {spec.microvm.vcpus} vCPUs</span>
      </div>
      {#if r.result.cost_per_1000_tasks}
        <div>
          <span class="k">Cost per 1,000 tasks</span>
          <span class="v">≈{f.usdRange(r.result.cost_per_1000_tasks.execution)}</span>
          <span class="sub">
            while tasks ran; ≈{f.usdRange(r.result.cost_per_1000_tasks.observed)} over whole trials; at an
            <em>assumed</em> ${f.num(spec.price_usd_per_hour, 5)} an hour
          </span>
        </div>
      {/if}
    </div>
  {/if}

  <h2>Densities</h2>
  <p class="muted">
    The run tested the spec's densities in order and stopped at the first that failed. A
    <Term k="density" /> passes only if every trial at it passed; extra trials at the boundary show how much the same
    test varies on this host.
  </p>
  <DensityStrip briefs={r.by_density} href={(d) => href({ name: 'run', campaign: c.id, run: r.id, density: d })} />
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th class="num">Density</th>
          <th>Result</th>
          <th>Trials</th>
          <th>Which criterion failed</th>
          <th class="num"><Term k="cpu_pressure" text="CPU pressure" /></th>
          <th class="num">Busy cores</th>
          <th class="num">Memory used / <Term k="allocated" /></th>
          <th class="num">Cost per 1,000 tasks</th>
        </tr>
      </thead>
      <tbody>
        {#each rows as d (d.density)}
          <tr class:current={density === d.density}>
            <td class="num"><strong>{d.density}</strong></td>
            {#if d.result === 'not_run'}
              <td class="muted" colspan="7">not run: the run stopped at the first density that failed</td>
            {:else}
              <td class={d.result === 'passed' ? 'pass' : 'fail'}>
                {d.result === 'passed' ? '✓ passed' : '× failed'}
                <div class="sub">{d.passed} of {d.trials.length} trials passed</div>
              </td>
              <td class="nowrap">
                {#each d.trials as t (t.id)}
                  <a
                    class="trial {t.passed ? 'pass' : 'fail'}"
                    href={href({ name: 'trial', campaign: c.id, run: r.id, trial: t.id, microvm: null })}
                    aria-label="{trialLabel(t)}, {t.passed ? 'passed' : 'failed'}">{t.number}{t.passed ? '✓' : '×'}</a
                  >
                {/each}
              </td>
              <td>
                {#if d.misses.length === 0}<span class="muted">none</span>{/if}
                {#each d.misses as m (m.text)}
                  <div>{m.text} <span class="muted">(missed in {m.missed} of {m.of})</span></div>
                {/each}
              </td>
              <td class="num">{d.cpuPressure ? f.range(d.cpuPressure, (v) => f.pct(v)) : 'not recorded'}</td>
              <td class="num">{d.busyCores ? f.range(d.busyCores, (v) => f.num(v, 1)) : '–'}</td>
              <td class="num">{d.memUsed ? f.range(d.memUsed, (v) => f.num(v, 1)) : '–'} / {f.num(d.memAllocated, 1)} GiB</td>
              <td class="num">{d.costExecution ? `≈${f.usdRange(d.costExecution)}` : '–'}</td>
            {/if}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <h2>What limited it</h2>
  <div class="panel limit">
    {#if !r.result}
      <p>The run did not finish, so there is no limit to report.</p>
    {:else if !r.has.attribution}
      <p>This run's format did not record what used the CPU, so the limit is not recorded.</p>
    {:else if !attr}
      <p>No trial at the deciding density recorded attribution.</p>
    {:else}
      {@const l = r.result.limit}
      {#if l}
        <p class="limit-lead">
          <strong>{cap(VERDICT_LABEL[l.verdicts[0]])}</strong> at density {l.density}, in {l.trials_with_verdict} of
          {l.trials} trials{l.verdicts.length > 1 ? `; also ${l.verdicts.slice(1).map((v) => VERDICT_LABEL[v]).join(', ')}` : ''}.
          The verdict comes from rules fixed in the spec before the run.
        </p>
        {#if l.separated_by}
          {@const sb = l.separated_by}
          <p>
            {RULE_LABEL[sb.rule]} separated the densities: {f.range(sb.passing, (v) => f.num(v, 1))}{unitOf(sb.rule)} at
            {r.result.tested_successfully}, {f.range(sb.failing, (v) => f.num(v, 1))}{unitOf(sb.rule)} at {l.density},
            against a rule of {f.num(sb.threshold)}{unitOf(sb.rule)}.
          </p>
        {/if}
      {:else}
        <p class="limit-lead">
          Nothing failed at the densities tested, so nothing limited this run. Below is what used the CPU at the
          highest, density {attr.density}.
        </p>
      {/if}

      <h3>By resource</h3>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Rule</th><th class="num">Over {attr.trials === 1 ? 'the trial' : `the ${attr.trials} trials`} at {attr.density}</th><th class="num">Rule</th><th>Fired</th></tr>
          </thead>
          <tbody>
            {#each attr.rules as x (x.key)}
              {@const def = spec.rules.find((d) => d.key === x.key)}
              <tr>
                <td>{RULE_LABEL[x.key]}</td>
                <td class="num">{x.values ? f.range(x.values, (v) => ruleValue(spec, x.key, v)) : 'not recorded'}</td>
                <td class="num">{def ? `${def.op === '>=' ? '≥' : '<'} ${f.num(def.threshold, def.threshold % 1 ? 2 : 0)}${unitOf(x.key)}` : '–'}</td>
                <td class={x.fired ? 'fail' : 'muted'}>{x.fired ? `in ${x.fired} of ${attr.trials}` : 'no'}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>

      <h3>By process</h3>
      <div class="shares">
        <ShareBar
          title="Host CPU at density {attr.density}, by process"
          parts={attr.host.map((h) => ({ label: HOST_CONSUMER_LABEL[h.key], share: h.share, detail: `${f.num(h.seconds, 1)} CPU-s` }))}
        />
        {#if attr.guest}
          <ShareBar
            title="CPU inside the microVMs, by process"
            parts={attr.guest.map((g) => ({ label: GUEST_GROUP_LABEL[g.key], share: g.share }))}
          />
        {:else}
          <p class="muted">CPU inside the microVMs was not recorded.</p>
        {/if}
      </div>
      <p class="muted small">
        Summed over the task windows (release to last return) of {attr.trials === 1 ? 'the trial' : `the ${attr.trials} trials`} at density {attr.density}.
        Inside the microVMs, shares are of busy CPU (total minus idle), averaged over the trials.
      </p>
    {/if}
  </div>

  <h2>Trials{density !== null ? ` at density ${density}` : ''}</h2>
  <nav class="filters" aria-label="Filter trials by density">
    <a href={href({ name: 'run', campaign: c.id, run: r.id, density: null })} aria-current={density === null ? 'true' : undefined}>All</a>
    {#each r.by_density.filter((d) => d.result !== 'not_run') as d (d.density)}
      <a href={href({ name: 'run', campaign: c.id, run: r.id, density: d.density })} aria-current={density === d.density ? 'true' : undefined}
        >{d.density}</a
      >
    {/each}
  </nav>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>Trial</th><th>Result</th><th class="num">All ready</th><th class="num">Tasks ok</th><th>Limit</th></tr>
      </thead>
      <tbody>
        {#each shown as t (t.id)}
          <tr class={t.counts ? '' : 'excluded'}>
            <td class="nowrap"><a href={href({ name: 'trial', campaign: c.id, run: r.id, trial: t.id, microvm: null })}>{trialLabel(t)}</a></td>
            <td>
              {#if !t.counts}
                <span class="muted">doesn't count: {t.excluded_because}</span>
              {:else if t.passed}
                <span class="pass">✓ passed{t.at_limit ? ', while a limit rule fired' : ''}</span>
              {:else}
                <span class="fail">× {t.failed.join('; ')}</span>
              {/if}
            </td>
            <td class="num">{t.ready.all_ready_ms !== null ? f.seconds(t.ready.all_ready_ms) : 'never'}</td>
            <td class="num">{t.tasks.ok} of {t.tasks.of}</td>
            <td>{t.attribution ? t.attribution.verdicts.map((v) => VERDICT_LABEL[v]).join(', ') : '–'}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <h2>Its spec</h2>
  <p class="muted">
    {#if spec.changes.length}
      {spec.label} changes {spec.changes.length} {spec.changes.length === 1 ? 'input' : 'inputs'} from the campaign's base;
      they're marked.
    {:else if c.specs.length > 1}
      This is the campaign's base spec; the other specs change it.
    {:else}
      The campaign's only spec.
    {/if}
    Host kind and price aren't inputs: both follow from the instance type.
  </p>
  <div class="spec">
    {#each specRows(c, spec) as g (g.group)}
      <section>
        <h3>{g.group}</h3>
        <table>
          <tbody>
            {#each g.rows as row (row.path)}
              <tr class:changed={row.changed}>
                <th scope="row">{row.label}</th>
                <td>
                  {show(row.value, row.unit)}
                  {#if row.changed}<span class="was">changed; base: {show(row.base, row.unit)}</span>{/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </section>
    {/each}
    <section>
      <h3>Derived, not inputs</h3>
      <table>
        <tbody>
          <tr><th scope="row"><Term k="host_kind" text="Host kind" /></th><td>{spec.host_kind}</td></tr>
          <tr><th scope="row">Price</th><td><em>assumed</em> ${f.num(spec.price_usd_per_hour, 5)} an hour</td></tr>
        </tbody>
      </table>
    </section>
  </div>

  <h2>Worker host, as measured</h2>
  <div class="table-wrap">
    <table>
      <tbody>
        <tr><th scope="row">Instance type</th><td>{r.host.instance_type} ({r.host.host_kind})</td></tr>
        <tr><th scope="row">vCPUs</th><td>{r.host.vcpus} = {r.host.sockets} {r.host.sockets === 1 ? 'socket' : 'sockets'} × {r.host.cores / r.host.sockets} cores × {r.host.threads_per_core} threads</td></tr>
        <tr><th scope="row">CPU</th><td>{r.host.cpu_model}</td></tr>
        <tr><th scope="row">Memory</th><td>{f.num(r.host.mem_gib, 1)} GiB</td></tr>
        <tr><th scope="row">Kernel</th><td>{r.host.kernel_release}</td></tr>
        <tr><th scope="row"><Term k="hypervisor" text="Hypervisor" /></th><td>{r.host.hypervisor_version}</td></tr>
        <tr><th scope="row">Chromium</th><td>{r.host.chromium_version}</td></tr>
      </tbody>
    </table>
  </div>

  <h2>Run it again</h2>
  <div class="panel">
    <p>
      Paste this into chat to launch it. The campaign definition asks for one more run of this spec on a new worker
      host; edit its question or replicas first if you like.
    </p>
    <fieldset class="as">
      <legend>Copy as</legend>
      <label><input type="radio" bind:group={reproduceAs} value="definition" /> a campaign definition</label>
      <label><input type="radio" bind:group={reproduceAs} value="spec" /> the spec alone</label>
    </fieldset>
    {#if reproduceAs === 'definition'}
      <CopyBlock label="campaign definition" text={again.definition} />
    {:else}
      <CopyBlock label="spec" text={again.spec} />
    {/if}
  </div>
{:catch e}
  <Failed error={e} />
{/await}

<style>
  .result {
    font-size: 1.15rem;
    font-weight: 600;
  }
  .facts {
    display: flex;
    flex-wrap: wrap;
    gap: 16px 32px;
    margin: 16px 0;
  }
  .facts > div {
    display: flex;
    flex-direction: column;
    min-width: 12rem;
    flex: 1 1 12rem;
  }
  .k {
    font-size: 0.85rem;
    color: var(--ink-2);
  }
  .v {
    font-size: 1.6rem;
    font-weight: 600;
  }
  .sub {
    font-size: 0.8rem;
    color: var(--muted);
  }
  .small {
    font-size: 0.85rem;
  }
  .nowrap {
    white-space: nowrap;
  }
  tr.current td {
    background: var(--highlight);
  }
  a.trial {
    display: inline-block;
    min-width: 2.2em;
    padding: 0 4px;
    margin-right: 2px;
    border: 1px solid var(--rule);
    border-radius: 4px;
    text-decoration: none;
    text-align: center;
    font-variant-numeric: tabular-nums;
  }
  .limit h3 {
    margin-top: 16px;
  }
  .limit-lead {
    font-size: 1.05rem;
  }
  .shares {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr));
    gap: 8px 32px;
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 8px 0;
  }
  .filters a {
    border: 1px solid var(--rule);
    border-radius: 4px;
    padding: 0 8px;
    text-decoration: none;
    color: var(--ink-2);
  }
  .filters a[aria-current='true'] {
    border-color: var(--accent);
    background: var(--highlight);
    color: var(--ink);
  }
  tr.excluded td {
    color: var(--muted);
  }
  .spec {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr));
    gap: 0 32px;
  }
  .spec h3 {
    margin: 12px 0 4px;
  }
  .spec table {
    width: 100%;
    min-width: 0;
  }
  .spec th {
    font-weight: 400;
    white-space: normal;
    width: 55%;
  }
  .spec tr.changed th,
  .spec tr.changed td {
    background: var(--highlight);
  }
  .spec tr.changed th {
    font-weight: 600;
    color: var(--ink);
    border-left: 3px solid var(--accent);
    padding-left: 6px;
  }
  .was {
    display: block;
    font-size: 0.8rem;
    color: var(--ink-2);
  }
  fieldset.as {
    border: 0;
    padding: 0;
    margin: 8px 0;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
  }
  fieldset.as legend {
    float: left;
    color: var(--ink-2);
    margin-right: 4px;
  }
</style>
