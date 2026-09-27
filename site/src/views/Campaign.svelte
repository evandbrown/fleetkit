<script lang="ts">
  // A campaign (D49, D52): its question, its specs as cards with what differs between them marked, every run's
  // result side by side with replicas grouped and their spread shown, and latency against density per run.
  import { loadCampaign, loadRun } from '../lib/data';
  import { href } from '../lib/router';
  import * as f from '../lib/format';
  import { grouped, show } from '../lib/spec';
  import { capacityLine, replicaGroups, sameEverywhere, specCards } from '../lib/shape';
  import { GUEST_GROUP_LABEL, HOST_CONSUMER_LABEL, VERDICT_LABEL } from '../lib/glossary';
  import type { CampaignDoc, RunEntry } from '../lib/types';
  import Failed from '../components/Failed.svelte';
  import SyntheticBanner from '../components/SyntheticBanner.svelte';
  import SegText from '../components/SegText.svelte';
  import DensityStrip from '../components/DensityStrip.svelte';
  import Term from '../components/Term.svelte';
  import CapacityChart from '../components/CapacityChart.svelte';
  import CopyBlock from '../components/CopyBlock.svelte';

  let { id }: { id: string } = $props();
  const doc = $derived(loadCampaign(id));
  /** Every run document, for the latency chart; fetched after the campaign. */
  const runs = $derived(doc.then((c) => Promise.all(c.runs.map((r) => loadRun(c.id, r.id)))));

  const REPO = 'https://github.com/evandbrown/fleetkit/blob/';
  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
  const runHref = (c: CampaignDoc, run: string, density: number | null = null) =>
    href({ name: 'run', campaign: c.id, run, density });

  function limitText(r: RunEntry): string {
    if (!r.result) return 'no result: the run did not finish';
    const l = r.result.limit;
    if (!l) return 'nothing failed at the densities tested';
    return `${cap(VERDICT_LABEL[l.verdicts[0]])} at density ${l.density}, in ${l.trials_with_verdict} of ${l.trials} trials`;
  }
</script>

{#await doc}
  <p class="status">Loading…</p>
{:then c}
  {@const cards = specCards(c)}
  {@const groups = replicaGroups(c)}
  {@const same = sameEverywhere(c)}
  {@const labels = Object.fromEntries(c.specs.map((s) => [s.name, s.label]))}
  <p class="crumbs"><a href={href({ name: 'campaigns' })}>Campaigns</a> › {c.title}</p>
  {#if c.synthetic}<SyntheticBanner />{/if}

  <h1>{c.title}</h1>
  <p class="lead">{c.question}</p>
  <p class="meta">
    {plural(c.specs.length, 'spec')} × {c.definition.replicas}
    <Term k="replica" text={c.definition.replicas === 1 ? 'replica' : 'replicas'} /> = {plural(c.runs.length, 'run')}, each on
    its own <Term k="worker_host" />. Started {f.when(c.started)}, ended {f.when(c.ended)}.
    {#if c.status === 'partial'}<strong>Partial: at least one run did not finish.</strong>{/if}
  </p>
  {#if c.before_campaigns}
    <p class="meta">
      A single run published before campaigns existed, shown as a campaign of one.
      {#if c.provenance === 'reconstructed'}Its spec was reconstructed from the inputs the run recorded.{/if}
    </p>
  {/if}
  {#if c.preregistration}
    <p class="meta">
      The criteria were fixed before the first run in
      <a href="{REPO}{c.preregistration.commit}/{c.preregistration.path}" rel="noopener">{c.preregistration.path}</a>
      (commit <code>{c.preregistration.commit.slice(0, 7)}</code>).
    </p>
  {/if}

  {#if c.answer}<p class="answer"><SegText segs={c.answer} /></p>{/if}
  {#if c.reading}<blockquote><strong>Evan's reading:</strong> {c.reading}</blockquote>{/if}

  <h2>{c.specs.length > 1 ? 'What differs between the specs' : 'The spec'}</h2>
  <p class="muted">
    {#if c.specs.length > 1}
      Each <Term k="spec" /> is the base with a few inputs changed. Changed inputs are marked, with the base's value
      beside them.
    {:else}
      The <Term k="spec" /> fixes everything about a run. This campaign has one, so nothing differs.
    {/if}
  </p>
  <div class="cards">
    {#each cards as card (card.name)}
      <section class="card" aria-labelledby="spec-{card.name}">
        <h3 id="spec-{card.name}">
          {card.label}
          {#if c.specs.length > 1}
            <span class="tag">{card.isBase ? 'the base' : `${card.rows.filter((r) => r.changed).length} changed`}</span>
          {/if}
        </h3>
        <dl>
          {#each card.rows as row (row.key)}
            <div class="row" class:changed={row.changed}>
              <dt>{row.label}{#if row.changed}<span class="vh"> (changed from the base)</span>{/if}</dt>
              <dd>
                <span class:assumed={row.cls === 'assumed'}>{row.value}</span>
                {#if row.note}<span class="note">{row.note}</span>{/if}
                {#if row.base}<span class="was">base: {row.base}</span>{/if}
              </dd>
            </div>
          {/each}
        </dl>
        <p class="card-runs">
          {#each card.runs as r, i (r.id)}{i ? ' · ' : ''}<a href={runHref(c, r.id)}>replica {r.replica}</a>:
            {r.result ? (r.result.tested_successfully ?? 'none passed') : 'incomplete'}{/each}
          <span class="muted">(<Term k="tested_successfully" />)</span>
        </p>
      </section>
    {/each}
  </div>
  <details>
    <summary>
      {c.specs.length > 1 ? `The ${same.length} inputs that are the same in every spec` : `Every input (${same.length})`}
    </summary>
    {#each grouped(c, c.definition.base) as g (g.group)}
      {@const rows = g.rows.filter((r) => same.includes(r.field.path))}
      {#if rows.length}
        <h4>{g.group}</h4>
        <div class="table-wrap">
          <table>
            <tbody>
              {#each rows as row (row.field.path)}
                <tr>
                  <td>{row.field.label}</td>
                  <td>{show(row.value, row.field.unit)}</td>
                  <td class="muted"><code>{row.field.path}</code></td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {/each}
  </details>

  <h2>Results, run by run</h2>
  <p class="muted">
    Runs are grouped by spec. A spec's row gives the spread across its replicas; a difference between specs means
    something only when it is larger than that spread.
  </p>
  <div class="table-wrap">
    <table class="results">
      <thead>
        <tr>
          <th>Run and its <Term k="density" text="densities" /></th>
          <th class="num"><Term k="tested_successfully" text="Tested successfully" /></th>
          <th class="num">Per host vCPU</th>
          <th class="num">Cost per 1,000 tasks</th>
          <th>What limited it</th>
        </tr>
      </thead>
      {#each groups as g (g.spec.name)}
        <tbody>
          <tr class="group">
            <th scope="rowgroup">
              {g.spec.label}
              <div class="sub">{plural(g.runs.length, 'replica')}{g.runs.length > 1 ? ': spread' : ''}</div>
            </th>
            <td class="num strong" data-label="Tested successfully">{g.tested ? f.range(g.tested) : 'none'}</td>
            <td class="num strong" data-label="Per host vCPU">{g.perHostVcpu ? f.range(g.perHostVcpu, f.ratio) : '–'}</td>
            <td class="num strong" data-label="Cost per 1,000 tasks">
              {#if g.costExecution}
                ≈{f.usdRange(g.costExecution)}
                <div class="sub">observed ≈{f.usdRange(g.costObserved!)}</div>
              {:else}–{/if}
            </td>
            <td data-label="What limited it">
              {#each g.limits as l, i (l.verdict)}{i ? '; ' : ''}{cap(VERDICT_LABEL[l.verdict])} ({l.runs} of {g.runs.length}){/each}
            </td>
          </tr>
          {#each g.runs as r (r.id)}
            <tr class="replica">
              <td class="run-cell">
                <a href={runHref(c, r.id)}>{r.id}</a>
                <span class="sub">{r.host.instance_type}, {r.host.vcpus} vCPUs{r.status === 'incomplete' ? ', incomplete' : ''}</span>
                <div class="strip"><DensityStrip briefs={r.by_density} href={(d) => runHref(c, r.id, d)} /></div>
              </td>
              <td class="num" data-label="Tested successfully">{r.result ? (r.result.tested_successfully ?? 'none') : '–'}</td>
              <td class="num" data-label="Per host vCPU">{r.result?.per_host_vcpu != null ? f.ratio(r.result.per_host_vcpu) : '–'}</td>
              <td class="num" data-label="Cost per 1,000 tasks">
                {#if r.result?.cost_per_1000_tasks}
                  ≈{f.usdRange(r.result.cost_per_1000_tasks.execution)}
                  <div class="sub">observed ≈{f.usdRange(r.result.cost_per_1000_tasks.observed)}</div>
                {:else}–{/if}
              </td>
              <td data-label="What limited it">
                {limitText(r)}
                {#if r.result?.limit}
                  {@const l = r.result.limit}
                  <div class="sub">
                    Most host CPU: {HOST_CONSUMER_LABEL[l.host_consumer]}. Inside the microVMs:
                    {GUEST_GROUP_LABEL[l.guest_group]}, {f.range(l.guest_share, (v) => f.pct(v * 100, 0))}.
                  </div>
                {/if}
                {#if r.note}<div class="sub">{r.note}</div>{/if}
              </td>
            </tr>
          {/each}
        </tbody>
      {/each}
    </table>
  </div>
  <p class="muted small">
    Per host vCPU is the density tested successfully divided by the worker host's vCPUs. Cost is modelled from
    measured times at an <em>assumed</em> on-demand price: execution counts only the time tasks ran; observed counts
    the whole trial, from requesting the microVMs to a clean host. Both are at the density tested successfully.
  </p>

  <h2>Latency against density</h2>
  <p class="muted">
    One line per run. Each point is the slowest <Term k="trial" /> at that <Term k="density" />, because a density
    passes only if every trial at it does; the faint dots are the other trials. The dashed lines are the spec's
    targets.
  </p>
  {#await runs}
    <p class="status">Loading the runs…</p>
  {:then docs}
    <CapacityChart
      campaign={c.id}
      {labels}
      lines={docs.map((r) => capacityLine(r, c.specs.find((s) => s.name === r.spec)!, c.specs.findIndex((s) => s.name === r.spec)))}
    />
  {:catch e}
    <Failed error={e} />
  {/await}

  {#if c.does_not_show?.length}
    <h2>What this result does not show</h2>
    <ul>
      {#each c.does_not_show as item, i (i)}<li><SegText segs={item} /></li>{/each}
    </ul>
  {/if}

  {#if c.next?.length}
    <h2>Next</h2>
    <ul>
      {#each c.next as n, i (i)}
        <li>
          {n.text}
          <span class="muted">Why: {n.motivated_by}.</span>
          {#each Object.entries(n.changes) as [p, v] (p)}<code class="change">{p} = {show(v)}</code>{/each}
        </li>
      {/each}
    </ul>
  {/if}

  <h2>The campaign definition</h2>
  <p class="muted">
    What the experiment builder produces and what was launched: the question, the base spec, each spec as its changes
    from the base, and the number of replicas.
    {#if c.provenance === 'reconstructed'}This one was reconstructed after the run.{/if}
  </p>
  <details>
    <summary>Show the definition</summary>
    <CopyBlock label="campaign definition" text={JSON.stringify(c.definition, null, 2)} />
  </details>
{:catch e}
  <Failed error={e} />
{/await}

<style>
  .answer {
    font-size: 1.05rem;
  }
  blockquote {
    margin: 12px 0;
    padding-left: 12px;
    border-left: 3px solid var(--rule);
    color: var(--ink-2);
  }
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr));
    gap: 16px;
    margin: 12px 0;
  }
  .card {
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    padding: 14px 16px 10px;
    min-width: 0;
  }
  .card h3 {
    margin: 0 0 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: baseline;
  }
  .tag {
    font-size: 0.75rem;
    font-weight: 500;
    color: var(--ink-2);
    border: 1px solid var(--rule);
    border-radius: 4px;
    padding: 0 6px;
  }
  dl {
    margin: 0;
  }
  .row {
    display: grid;
    grid-template-columns: minmax(7rem, 38%) 1fr;
    gap: 8px;
    padding: 5px 6px;
    border-left: 3px solid transparent;
    border-radius: 2px;
  }
  .row + .row {
    border-top: 1px solid var(--rule);
  }
  .row.changed {
    background: var(--highlight);
    border-left-color: var(--accent);
  }
  dt {
    color: var(--ink-2);
    font-size: 0.88rem;
  }
  .row.changed dt {
    color: var(--ink);
    font-weight: 600;
  }
  dd {
    margin: 0;
    min-width: 0;
  }
  .note,
  .was {
    display: block;
    font-size: 0.8rem;
    color: var(--muted);
  }
  .was {
    color: var(--ink-2);
  }
  .assumed {
    font-style: italic;
  }
  .card-runs {
    font-size: 0.88rem;
    margin: 10px 0 0;
  }
  h4 {
    margin: 12px 0 0;
    font-size: 0.95rem;
  }
  .results tr.group th,
  .results tr.group td {
    background: var(--surface);
    border-top: 2px solid var(--rule);
    padding-top: 8px;
  }
  .results tr.group th {
    color: var(--ink);
    white-space: normal;
    padding-left: 8px;
  }
  .results tr.replica td:first-child {
    padding-left: 18px;
  }
  .strong {
    font-weight: 600;
  }
  .sub {
    font-size: 0.8rem;
    color: var(--muted);
    white-space: normal;
    font-weight: 400;
  }
  .small {
    font-size: 0.85rem;
  }
  .run-cell {
    min-width: 13rem;
  }
  .run-cell .sub {
    display: block;
  }
  .strip {
    margin-top: 4px;
  }
  td.num .sub {
    white-space: nowrap;
  }
  /* On a phone, each run becomes a block of labelled values instead of a wide row. */
  @media (max-width: 640px) {
    .results,
    .results tbody,
    .results tr,
    .results th,
    .results td {
      display: block;
      min-width: 0;
    }
    .results thead {
      display: none;
    }
    .results tr.group th,
    .results tr.group td,
    .results tr.replica td {
      border: 0;
      padding: 2px 8px;
      text-align: left;
    }
    .results tr.group {
      background: var(--surface);
      border-top: 2px solid var(--rule);
      padding: 6px 0;
      margin-top: 12px;
    }
    .results tr.group th {
      border-top: 0;
    }
    .results tr.replica {
      border-top: 1px solid var(--rule);
      padding: 8px 0 8px 10px;
    }
    .results tr.replica td:first-child {
      padding-left: 8px;
    }
    .results td[data-label]::before {
      content: attr(data-label) ': ';
      color: var(--ink-2);
      font-weight: 400;
    }
    .results td[data-label] .sub {
      display: inline;
      margin-left: 4px;
      white-space: normal;
    }
    .results td[data-label='What limited it'] .sub {
      display: block;
      margin-left: 0;
    }
  }
  .change {
    margin-left: 8px;
  }
  .vh {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
</style>
