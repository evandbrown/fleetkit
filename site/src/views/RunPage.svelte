<script lang="ts">
  // One run: the host it ran on, each density it tested (each trial a mark that opens it), what limited it, and its
  // full spec. Its own headline only when the campaign has other runs (otherwise Results shows the same four
  // figures). ?density=N highlights one density.
  import { loadCampaign, loadRun } from '../lib/data';
  import { href } from '../lib/router';
  import * as f from '../lib/format';
  import { campaignRefs, densityRows, type DensityRow, headline, latency, limitBars, runAttribution, runResult, runTitle, specSections } from '../lib/shape';
  import { GUEST_GROUP_LABEL, HOST_CONSUMER_LABEL, NOT_TESTED, RULE_LABEL, trialLabel, VERDICT_SHORT } from '../lib/glossary';
  import type { Range, RuleKey } from '../lib/types';
  import { HOST_CONSUMER_COLOR } from '../lib/colors';
  import Failed from '../components/Failed.svelte';
  import SyntheticBanner from '../components/SyntheticBanner.svelte';
  import Headlines from '../components/Headlines.svelte';
  import ShareBar from '../components/ShareBar.svelte';
  import Mark from '../components/Mark.svelte';
  import LatencyChart from '../components/LatencyChart.svelte';

  let { campaign, run, density }: { campaign: string; run: string; density: number | null } = $props();
  const data = $derived(Promise.all([loadCampaign(campaign), loadRun(campaign, run)]));

  /** Percentages as they are; fractions (throttled, memory free) as percentages too. */
  const asPct = (key: RuleKey) => key.endsWith('_pct') || key.endsWith('_fraction');
  const scaleOf = (key: RuleKey) => (key.endsWith('_fraction') ? 100 : 1);
  /** A rule's value, never rounded across its threshold. */
  const ruleValue = (key: RuleKey, v: number, t: number) =>
    asPct(key) ? `${f.side(v * scaleOf(key), t * scaleOf(key), key.endsWith('_fraction') ? 0 : 1)}%` : f.side(v, t, 2);
  const ruleRange = (key: RuleKey, r: Range, t: number) => f.range(r, (v) => ruleValue(key, v, t));
  const limitText = (key: RuleKey, op: '>=' | '<', t: number) =>
    `${op === '>=' ? '≥' : '<'} ${asPct(key) ? `${f.num(t * scaleOf(key))}%` : f.num(t, 2)}`;
  /** "Intel(R) Xeon(R) 6975P-C" → "Xeon 6975P-C". */
  const cpu = (s: string) =>
    s
      .replace(/\((R|TM)\)/gi, '')
      .replace(/\b(Intel|AMD|CPU|Processor)\b/gi, '')
      .replace(/@.*$/, '')
      .replace(/\s+/g, ' ')
      .trim();

  /** Neighbouring densities that weren't tested, as one row: "11–16 not tested". */
  function collapse(rows: DensityRow[]): (DensityRow & { to: number })[] {
    const out: (DensityRow & { to: number })[] = [];
    for (const d of rows) {
      const last = out.at(-1);
      if (last && last.result === 'not_tested' && d.result === 'not_tested') last.to = d.density;
      else out.push({ ...d, to: d.density });
    }
    return out;
  }

  /** Scrolls the highlighted density into view once. */
  function focusRow(node: HTMLElement, on: boolean) {
    if (on) requestAnimationFrame(() => node.scrollIntoView({ block: 'center' }));
  }
</script>

{#await data}
  <p class="status">Loading…</p>
{:then [c, r]}
  {@const spec = c.specs.find((s) => s.name === r.spec)!}
  {@const rows = densityRows(r)}
  {@const attr = r.has.attribution ? runAttribution(r) : null}
  {@const bars = limitBars(r, c.rules)}
  {@const res = r.result}
  {@const rr = c.runs.length > 1 ? runResult(c, r.id) : null}
  {@const title = runTitle(c, r.id)}
  {@const head = bars?.fired[0] ?? null}
  {@const failedAt = res.first_failed}
  {@const rest = bars
    ? [
        ...bars.fired.slice(1).map((b) => ({ key: b.key, op: b.op, threshold: b.threshold, values: b.at.find((a) => a.density === failedAt)?.values ?? null, fired: true })),
        ...bars.others.map((o) => ({ ...o, fired: false })),
      ]
    : []}
  <p class="crumbs">
    <a href={href({ name: 'results', campaign: null })}>Results</a> ›
    <a href={href({ name: 'results', campaign: c.id })}>{c.title}</a> › {title}
  </p>
  {#if r.synthetic}<SyntheticBanner />{/if}

  <h1>{title}</h1>
  <p class="facts">
    <strong>{r.host.instance_type}</strong> {r.host.host_kind} · {cpu(r.host.cpu_model)} · {f.duration(r.duration_s)} run
  </p>

  {#if rr}<Headlines h={headline(rr)} />{/if}

  {@const lat = latency(campaignRefs(c).filter((x) => x.spec.name === r.spec), new Map([[c.id, c]]), new Map([[`${c.id}/${r.id}`, r]]))}
  {#if lat.series.some((x) => x.points.length)}
    <h2>Latency by density</h2>
    <LatencyChart data={lat} />
  {/if}

  <h2>Densities</h2>
  <div class="table-wrap">
    <table class="densities">
      <thead>
        <tr>
          <th class="num">Density</th>
          <th>Result</th>
          <th>Trials</th>
          <th class="wide">Missed</th>
          <th class="num">CPU pressure</th>
          <th class="num wide">Memory used (GiB)</th>
          <th class="num wide">$ / 1k tasks</th>
        </tr>
      </thead>
      <tbody>
        {#each collapse(rows) as d (d.density)}
          {@const here = density !== null && density >= d.density && density <= d.to}
          <tr class:current={here} use:focusRow={here}>
            <td class="num"><strong>{d.to > d.density ? `${d.density}–${d.to}` : d.density}</strong></td>
            {#if d.result === 'not_tested'}
              <td class="label" colspan="6">{NOT_TESTED}</td>
            {:else}
              <td class="result">
                <span class={d.result === 'passed' ? 'pass' : 'fail'}>{d.result === 'passed' ? 'passed' : 'failed'}</span>
                {#each d.misses as m (m.text)}<span class="miss phone">{m.text} <span class="muted">{m.missed}/{m.of}</span></span>{/each}
              </td>
              <td class="marks">
                {#each d.trials as t (t.id)}
                  <a href={href({ name: 'trial', campaign: c.id, run: r.id, trial: t.id, microvm: null })} aria-label="{trialLabel(t)}, {t.passed ? 'passed' : 'failed'}"
                    ><Mark kind={t.passed ? 'pass' : 'fail'} size={13} /></a
                  >
                {/each}
              </td>
              <td class="wide misses">
                {#each d.misses as m (m.text)}
                  <div>{m.text}{#if m.value}{' '}<span class="fail">{m.value}</span>{/if} <span class="muted">{m.missed}/{m.of} trials</span></div>
                {:else}
                  <span class="muted">–</span>
                {/each}
              </td>
              <td class="num">{d.cpuPressure ? f.range(d.cpuPressure, (v) => f.pct(v, 0)) : '–'}</td>
              <td class="num wide">{d.memUsed ? f.range(d.memUsed, (v) => f.num(v, 0)) : '–'} <span class="muted">of {f.num(d.memAllocated, 0)} given</span></td>
              <td class="num wide">{d.costExecution ? f.usdRange(d.costExecution) : '–'}</td>
            {/if}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>


  <h2 id="limit">What limited it</h2>
  {#if res.limit}
    {@const l = res.limit}
    <p class="verdict">
      <strong>{VERDICT_SHORT[l.verdicts[0]]}</strong>
      <span class="muted">at density {l.density} · limit reached in {l.trials_with_verdict} of {l.trials} trials</span>
    </p>
    {#if head}
      {@const top = Math.max(head.threshold * 1.25, ...head.at.map((a) => a.values[1] * 1.05))}
      {@const scale = (v: number) => `${Math.min(100, (v / (asPct(head.key) && !head.key.endsWith('_fraction') ? Math.min(100, top) : top)) * 100)}%`}
      <figure class="bullet">
        <figcaption>{RULE_LABEL[head.key]} <span class="muted">limit {limitText(head.key, head.op, head.threshold)}</span></figcaption>
        {#each head.at as a (a.density)}
          {@const over = head.op === '>=' ? a.values[1] >= head.threshold : a.values[0] < head.threshold}
          <div class="brow">
            <span class="bd">at {a.density}</span>
            <span class="track" role="img" aria-label="{RULE_LABEL[head.key]} at density {a.density}: {ruleRange(head.key, a.values, head.threshold)}">
              {#if head.op === '>='}
                <span class="fill" style:width={scale(over ? Math.min(a.values[1], head.threshold) : a.values[1])}></span>
                {#if over}<span class="fill over" style:left={scale(head.threshold)} style:width="calc({scale(a.values[1])} - {scale(head.threshold)})"></span>{/if}
              {:else}
                <span class="fill" class:over style:width={scale(a.values[1])}></span>
              {/if}
              <span class="tick" style:left={scale(head.threshold)}></span>
            </span>
            <span class="bv" class:fail={over}>{ruleRange(head.key, a.values, head.threshold)}</span>
          </div>
        {/each}
      </figure>
      {#if rest.length}
        <details class="others">
          <summary>{rest.length} other limits at {failedAt}</summary>
          <ul>
            {#each rest as o (o.key)}
              <li>
                {RULE_LABEL[o.key]}
                <span class="muted">{o.values ? ruleRange(o.key, o.values, o.threshold) : 'not recorded'} · limit {limitText(o.key, o.op, o.threshold)}{o.fired ? ' · reached' : ''}</span>
              </li>
            {/each}
          </ul>
        </details>
      {/if}
    {/if}
  {:else if res.first_failed !== null}
    <p class="verdict"><strong>{VERDICT_SHORT.unknown}</strong> <span class="muted">at density {res.first_failed}</span></p>
  {:else}
    <p class="verdict">
      <strong>{res.tested_successfully === null ? NOT_TESTED : 'Nothing failed'}</strong>
      {#if res.tested_successfully !== null}<span class="muted">up to density {res.tested_successfully}</span>{/if}
    </p>
  {/if}
  {#if attr}
    <div class="shares">
      <ShareBar
        title="Host CPU by process at {attr.density}"
        parts={attr.host.map((h) => ({ label: HOST_CONSUMER_LABEL[h.key], color: HOST_CONSUMER_COLOR[h.key], share: h.share, detail: `${f.num(h.seconds, 1)} CPU-s` }))}
      />
      {#if attr.guest}
        <ShareBar title="Guest CPU by process at {attr.density}" parts={attr.guest.map((g) => ({ label: GUEST_GROUP_LABEL[g.key], share: g.share }))} />
      {/if}
    </div>
  {/if}

  <details class="spec">
    <summary>Full spec</summary>
    <div class="sections">
      {#each specSections(spec.spec, spec.changes) as g (g.group)}
        <section>
          <h3>{g.group}</h3>
          {#if g.rows.length === 1 && g.rows[0].label === g.group}
            {@const row = g.rows[0]}
            <p class="solo" class:changed={row.base !== undefined}>{row.value}{#if row.base !== undefined}<span class="was">base {row.base}</span>{/if}</p>
          {:else}
            <dl>
              {#each g.rows as row (row.path)}
                <div class:changed={row.base !== undefined}>
                  <dt>{row.label}</dt>
                  <dd>{row.value}{#if row.base !== undefined}<span class="was">base {row.base}</span>{/if}</dd>
                </div>
              {/each}
            </dl>
          {/if}
        </section>
      {/each}
    </div>
  </details>

  <p class="next"><a class="quiet" href={href({ name: 'builder', from: `run:${c.id}/${r.id}` })}>New campaign from this</a></p>
{:catch e}
  <Failed error={e} />
{/await}

<style>
  .facts {
    margin: 0 0 20px;
    color: var(--ink-2);
  }
  .facts strong {
    color: var(--ink);
    font-weight: 600;
  }
  .next {
    margin: 40px 0 0;
    padding-top: 16px;
    border-top: 1px solid var(--rule);
    max-width: none;
  }
  .solo {
    margin: 0;
    padding: 3px 4px;
    font-size: 0.9rem;
    border-bottom: 1px solid var(--rule);
  }
  .solo.changed {
    background: var(--highlight);
  }
  .label {
    color: var(--muted);
  }
  tr.current td {
    background: var(--highlight);
  }
  .result span.pass,
  .result span.fail {
    font-weight: 600;
    white-space: nowrap;
  }
  .miss {
    display: block;
    font-size: 0.82rem;
    color: var(--ink-2);
  }
  .marks {
    white-space: nowrap;
  }
  .marks a {
    display: inline-block;
    padding: 2px;
    border-radius: 4px;
  }
  .marks a:hover {
    background: var(--surface-2);
  }
  .misses {
    font-size: 0.88rem;
  }
  .phone {
    display: none;
  }
  @media (max-width: 600px) {
    .wide {
      display: none;
    }
    .phone {
      display: block;
    }
  }
  .verdict {
    font-size: 1.15rem;
    margin: 0 0 12px;
  }
  .verdict .muted {
    font-size: 0.9rem;
    margin-left: 6px;
  }
  .bullet {
    margin: 0;
    max-width: 36rem;
  }
  .bullet figcaption {
    font-weight: 600;
    font-size: 0.9rem;
    margin-bottom: 6px;
  }
  .bullet figcaption .muted {
    font-weight: 400;
    margin-left: 4px;
  }
  .brow {
    display: grid;
    grid-template-columns: 3.4rem minmax(0, 1fr) 7.5rem;
    align-items: center;
    gap: 10px;
    margin: 4px 0;
    font-size: 0.85rem;
  }
  .bd {
    color: var(--ink-2);
  }
  .track {
    position: relative;
    height: 14px;
    border-radius: 3px;
    background: var(--surface);
  }
  .fill {
    position: absolute;
    top: 0;
    bottom: 0;
    left: 0;
    border-radius: 3px 0 0 3px;
    background: var(--ordinal-2);
  }
  .fill.over {
    background: var(--critical);
    border-radius: 0 3px 3px 0;
  }
  .tick {
    position: absolute;
    top: -4px;
    bottom: -4px;
    width: 2px;
    margin-left: -1px;
    background: var(--ink);
  }
  .bv {
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .others {
    margin-top: 10px;
    font-size: 0.88rem;
  }
  .others summary {
    color: var(--muted);
  }
  .others ul {
    margin: 6px 0 0;
    padding-left: 18px;
  }
  .shares {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr));
    gap: 8px 32px;
    margin-top: 16px;
  }
  details.spec {
    margin-top: 40px;
  }
  .sections {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 16rem), 1fr));
    gap: 0 24px;
  }
  .sections h3 {
    margin: 12px 0 4px;
    font-size: 0.95rem;
  }
  dl {
    margin: 0;
    font-size: 0.9rem;
  }
  dl div {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    border-bottom: 1px solid var(--rule);
    padding: 3px 4px;
  }
  dl div.changed {
    background: var(--highlight);
  }
  dt {
    color: var(--ink-2);
  }
  dd {
    margin: 0;
    text-align: right;
  }
  .was {
    display: block;
    font-size: 0.8rem;
    color: var(--ink-2);
  }
</style>
