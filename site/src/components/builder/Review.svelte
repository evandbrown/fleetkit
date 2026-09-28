<script lang="ts">
  // The review, always in view: the runs, the time, the expected and worst-case cost against the limit, the vCPUs
  // at once against the quota, what changes between the specs, the runs as a grid of hosts (a row per spec, a dot per
  // replica), and the definition to copy into Claude Code. Each figure has an ⓘ that says what it counts.
  import { LIMITS, TYPES, differing, fieldAt, getPath, usd, type Obj, type Plan } from '../../lib/campaign';
  import { REVIEW_HELP as HELP, show, type Msg } from './draft';
  import Help from './Help.svelte';

  let {
    est,
    specs,
    text,
    errors,
    others,
    onshow,
  }: {
    est: Plan | null;
    specs: [string, Obj][];
    text: string;
    errors: number;
    others: { msg: Msg; fix?: { label: string; run: () => void } }[];
    onshow: () => void;
  } = $props();

  const differs = $derived(differing(specs));
  const hosts = $derived(specs.map(([, s]) => TYPES[String(getPath(s, 'worker_host.instance_type'))]));
  const hostRows = $derived.by(() => {
    if (!differs.some((d) => d.field === 'worker_host.instance_type') || hosts.some((h) => !h)) return [];
    const rows: { label: string; values: string[] }[] = [];
    if (new Set(hosts.map((h) => h.vcpus)).size > 1) rows.push({ label: 'Host vCPUs', values: hosts.map((h) => `${h.vcpus}`) });
    if (new Set(hosts.map((h) => h.memory_gib)).size > 1) {
      rows.push({ label: 'Host memory', values: hosts.map((h) => `${h.memory_gib} GiB`) });
    }
    return rows;
  });

  /** A row whose values tell every spec apart names the columns itself; otherwise the spec names head them. */
  const idRow = $derived(differs.findIndex((d) => new Set(specs.map(([n]) => show(d.field, d.values[n]))).size === specs.length));
  const rowsShown = $derived(idRow > 0 ? [differs[idRow], ...differs.filter((_, i) => i !== idRow)] : differs);

  /** The runs as a grid: one row per spec, one dot per replica. */
  const grid = $derived.by(() => {
    if (!est) return [];
    const byName = new Map<string, { run: string; over: boolean }[]>();
    for (const r of est.runs) {
      if (!byName.has(r.spec_name)) byName.set(r.spec_name, []);
      byName.get(r.spec_name)!.push({ run: r.run, over: est.too_big_for_quota.includes(r.run) });
    }
    return [...byName.values()];
  });

  const limit = LIMITS.campaign_worst_case_usd;
  const over = $derived(!!est && est.worst_case_usd > limit);
  const peak = $derived(est ? Math.max(0, ...est.wave_vcpus) : 0);
  const pct = (a: number, b: number) => `${Math.max(0, Math.min(100, (a / b) * 100))}%`;
  const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

  let copied = $state('');
  let pre: HTMLElement | undefined = $state();
  let details: HTMLDetailsElement | undefined = $state();
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      copied = 'Copied. Paste it into Claude Code.';
    } catch {
      if (details) details.open = true;
      if (pre) {
        const range = document.createRange();
        range.selectNodeContents(pre);
        const sel = window.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(range);
      }
      copied = 'Selected below. Press ⌘C or Ctrl+C.';
    }
    setTimeout(() => (copied = ''), 5000);
  }
</script>

<section class="review" aria-labelledby="review-h">
  <h2 id="review-h">Review</h2>

  {#if est}
    <dl class="stats">
      <div class="stat">
        <dt>Runs<Help id="r-runs-about" label="Runs" text={HELP.runs} /></dt>
        <dd class="v">{est.runs.length}</dd>
      </div>
      <div class="stat">
        <dt>Time<Help id="r-time-about" label="Time" text={HELP.time} /></dt>
        <dd class="v">≈{est.minutes_in_waves} min</dd>
        <dd class="s">
          {est.waves.length === 1 ? 'all at once' : est.waves.length ? `${est.waves.length} waves` : 'over quota'}
        </dd>
      </div>
      <div class="stat">
        <dt>Expected<Help id="r-expected-about" label="Expected" text={HELP.expected} /></dt>
        <dd class="v">{usd(est.expected_usd)}</dd>
      </div>
      <div class="stat" class:over>
        <dt>Worst case<Help id="r-worst-about" label="Worst case" text={HELP.worst} /></dt>
        <dd class="v">{usd(est.worst_case_usd)}</dd>
        <dd class="s">{est.shutdown_after_minutes} min per host</dd>
      </div>
    </dl>

    <div class="meters">
      <div class="meter-row">
        <span class="mk">vCPUs<Help id="r-vcpus-about" label="vCPUs" text={HELP.vcpus} /></span>
        <span class="meter" role="img" aria-label="{peak} of {est.vcpu_quota} vCPUs at once">
          <span style:width={pct(peak, est.vcpu_quota)}></span>
        </span>
        <span class="mv">{peak} / {est.vcpu_quota}</span>
      </div>
      <div class="meter-row" class:over>
        <span class="mk">Cost<Help id="r-cost-about" label="Cost" text={HELP.cost} /></span>
        <span class="meter" role="img" aria-label="Worst case {usd(est.worst_case_usd)} of the {usd(limit)} limit">
          <span style:width={pct(est.worst_case_usd, limit)}></span>
        </span>
        <span class="mv">{usd(est.worst_case_usd).replace(/\.00$/, '')} / {usd(limit).replace(/\.00$/, '')}</span>
      </div>
    </div>
  {:else}
    <p class="muted small">Fix the errors to see runs and cost.</p>
  {/if}

  {#if specs.length > 1}
    <div class="h3"><h3>What changes</h3><Help id="r-changes-about" label="What changes" text={HELP.changes} /></div>
    {#if !differs.length && !hostRows.length}
      <p class="muted small">Nothing yet.</p>
    {:else}
      <div class="table-wrap">
        <table>
          {#if idRow < 0}
            <thead>
              <tr><th></th>{#each specs as [n] (n)}<th>{n}</th>{/each}</tr>
            </thead>
          {/if}
          <tbody>
            {#each rowsShown as d, i (d.field)}
              <tr class:id={idRow >= 0 && i === 0}>
                <th scope="row">{fieldAt(d.field)?.label ?? d.field}</th>
                {#each specs as [n] (n)}<td>{show(d.field, d.values[n])}</td>{/each}
              </tr>
            {/each}
            {#each hostRows as r (r.label)}
              <tr class="follows">
                <th scope="row">{r.label}</th>
                {#each r.values as v, i (i)}<td>{v}</td>{/each}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  {/if}

  {#if est?.runs.length}
    <div class="h3"><h3>Hosts</h3><Help id="r-hosts-about" label="Hosts" text={HELP.hosts} /></div>
    <div class="hosts">
      <span class="grid" role="img" aria-label="{plural(est.runs.length, 'worker host')}">
        {#each grid as row, i (i)}
          <span class="grow">{#each row as h (h.run)}<i class:over={h.over}></i>{/each}</span>
        {/each}
      </span>
      <span class="small">
        {plural(est.runs.length, 'worker host')}, one per run
        {#if est.too_big_for_quota.length}<span class="warn"> · {est.too_big_for_quota.length} over quota</span>{/if}
      </span>
    </div>
  {/if}

  {#if others.length}
    <h3>Fix</h3>
    <ul class="others">
      {#each others as o, i (i)}
        <li class:error={o.msg.error} class="problem">
          <code>{o.msg.path}</code>: {o.msg.text}
          {#if o.fix}<button type="button" class="link" onclick={o.fix.run}>{o.fix.label}</button>{/if}
        </li>
      {/each}
    </ul>
  {/if}

  <div class="copy">
    <button type="button" class="primary big" disabled={errors > 0} onclick={copy}>Copy definition</button>
    {#if errors}
      <p class="small err">
        {plural(errors, 'problem')} to fix <button type="button" class="link" onclick={onshow}>Show</button>
      </p>
    {:else if copied}
      <p class="small" role="status">{copied}</p>
    {:else}
      <p class="small muted">Paste it into Claude Code to run.</p>
    {/if}
  </div>

  <details bind:this={details}>
    <summary>JSON</summary>
    <!-- svelte-ignore a11y_no_noninteractive_tabindex -- focusable so a keyboard reader can scroll it -->
    <pre bind:this={pre} tabindex="0" aria-label="Campaign definition"><code>{text}</code></pre>
  </details>
</section>

<style>
  .review {
    background: var(--bg);
    border: 1px solid var(--rule);
    border-radius: 10px;
    padding: 16px 18px 18px;
  }
  h2 {
    margin: 0 0 12px;
  }
  .h3 {
    display: flex;
    align-items: center;
    margin: 20px 0 8px;
  }
  h3 {
    font-size: 0.92rem;
    margin: 20px 0 8px;
  }
  .h3 h3 {
    margin: 0;
  }
  .small {
    font-size: 0.86rem;
    margin: 6px 0 0;
  }
  .stats {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px 16px;
    margin: 0;
  }
  .stat {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .stat dt {
    order: 2;
    font-size: 0.8rem;
    color: var(--ink-2);
  }
  .stat dd {
    margin: 0;
  }
  .stat .v {
    order: 1;
    font-size: 1.5rem;
    font-weight: 650;
    line-height: 1.15;
    letter-spacing: -0.01em;
    font-variant-numeric: tabular-nums;
  }
  .stat .s {
    order: 3;
    font-size: 0.75rem;
    color: var(--muted);
  }
  .stat.over .v {
    color: var(--critical);
  }
  .meters {
    display: grid;
    gap: 6px;
    margin-top: 16px;
  }
  .meter-row {
    display: grid;
    grid-template-columns: 4.2rem 1fr auto;
    align-items: center;
    gap: 10px;
    font-size: 0.78rem;
  }
  .mk {
    display: inline-flex;
    align-items: center;
    color: var(--ink-2);
  }
  .mv {
    color: var(--muted);
    font-variant-numeric: tabular-nums;
    text-align: right;
    min-width: 4.5rem;
  }
  .meter {
    height: 6px;
    border-radius: 3px;
    background: var(--surface-2);
    overflow: hidden;
  }
  .meter span {
    display: block;
    height: 100%;
    min-width: 3px;
    background: var(--accent);
    border-radius: 3px;
  }
  .over .meter span {
    background: var(--critical);
  }
  .over .mv {
    color: var(--critical);
  }
  table {
    font-size: 0.84rem;
  }
  th,
  td {
    padding: 4px 10px 4px 0;
  }
  thead th {
    font-weight: 600;
  }
  tbody th {
    font-weight: 400;
    color: var(--ink-2);
    white-space: nowrap;
  }
  .follows th,
  .follows td {
    color: var(--muted);
  }
  .table-wrap {
    margin: 0;
  }
  tr.id td {
    font-weight: 650;
    color: var(--ink);
  }
  .hosts {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .hosts .small {
    margin: 0;
  }
  .grid {
    display: inline-flex;
    flex-direction: column;
    gap: 4px;
  }
  .grow {
    display: flex;
    gap: 4px;
  }
  .grow i {
    width: 12px;
    height: 15px;
    border-radius: 3px;
    border: 1.5px solid var(--accent);
    background: var(--highlight);
  }
  .grow i.over {
    border-color: var(--synthetic-ink);
    background: var(--synthetic-bg);
  }
  .warn {
    color: var(--synthetic-ink);
  }
  .others {
    padding-left: 18px;
    margin: 4px 0;
    font-size: 0.84rem;
  }
  .others .error {
    color: var(--critical);
  }
  .copy {
    margin-top: 20px;
  }
  .err {
    color: var(--critical);
  }
  details {
    margin-top: 12px;
    font-size: 0.86rem;
  }
  pre {
    margin: 8px 0 0;
    max-height: 22rem;
    overflow: auto;
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    padding: 10px;
    font-size: 0.74rem;
    line-height: 1.45;
  }
</style>
