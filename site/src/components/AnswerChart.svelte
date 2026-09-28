<script lang="ts">
  // How each spec performed: one row per spec. Its colour dot and name; a strip on one shared axis (browsers per
  // vCPU when the specs' hosts differ in vCPUs, else browsers per host) with one thin lane per replica: a dot at its
  // last pass, a × at its first failure and a faint line between them in the spec's colour, so five replicas read as
  // five strokes and the eye counts them; a tick at the spec's midpoint spans the lanes. Hovering a lane names the
  // replica and its figures. Then the cost per 1,000 tasks as one number (D102) with a bar under it scaled to the
  // dearest spec, and what ran out. The clear winner on cost is highlighted; when there is none, one muted phrase
  // says so. The replica links live in "Every trial and replica" under this chart. The axis ticks sit once above the
  // first row, with the axis title at their left; the row reads out whole to a screen reader.
  import * as f from '../lib/format';
  import { specColor } from '../lib/colors';
  import { tickText } from '../lib/bands';
  import { niceDomain } from '../lib/scale';
  import { ticks as d3ticks } from 'd3-array';
  import type { AnswerRow, AnswerRun } from '../lib/shape';
  import { browsers } from '../lib/glossary';
  import Mark from './Mark.svelte';
  import Term from './Term.svelte';

  let { rows, campaigns = false }: { rows: AnswerRow[]; campaigns?: boolean } = $props();

  const COST_TERM = 'Steady state: what a full fleet pays per 1,000 tasks, boot included. Hover a figure for its range and the one burst it was measured from.';
  /** Lanes are this many px apart; five of them sit inside the 44 px row. */
  const PITCH = 5;

  const perVcpu = $derived(new Set(rows.flatMap((r) => r.runs.map((x) => x.hostVcpus))).size > 1);
  const axisTitle = $derived(perVcpu ? 'Browsers per vCPU' : 'Browsers per host');
  const v = (d: number, vcpus: number) => (perVcpu ? d / vcpus : d);
  /** The axis's top: a round number at or above the highest count marked, in about five steps. */
  const top = $derived(
    niceDomain(
      rows.flatMap((r) => r.runs.flatMap((x) => [x.failed, x.passed].filter((d): d is number => d !== null).map((d) => v(d, x.hostVcpus)))),
      { count: 5 },
    )[1],
  );
  const pct = (x: number) => Math.max(0, Math.min(100, (x / top) * 100));
  const pos = (x: number) => `${pct(x)}%`;
  const axis = $derived(d3ticks(0, top, 5).filter((t) => t <= top + 1e-9 && (perVcpu || Number.isInteger(t))));
  const midX = (r: AnswerRow) => (r.midpoint === null ? null : perVcpu ? r.midpoint : r.midpoint * r.hostVcpus);
  /** "1.68" per vCPU; "10" or "9.5" per host. */
  const midText = (mid: number) => f.num(mid, perVcpu ? 2 : Number.isInteger(mid) ? 0 : 1);
  const replicas = $derived(rows.some((r) => r.runs.length > 1));
  const costed = $derived(rows.filter((r) => r.costValue !== null).length);
  const noWinner = $derived(costed > 1 && !rows.some((r) => r.best));

  /** One lane per replica that ran: where its marks go, in % of the strip, and its title. */
  interface Lane {
    replica: number;
    /** Its offset from the strip's middle, px. */
    dy: number;
    pass: string | null;
    /** The last pass is where the replica stopped without a failure: a dashed ring instead of a dot. */
    early: boolean;
    fail: string | null;
    /** The connector from the last pass to the first failure. */
    line: { left: string; width: string } | null;
    title: string;
  }
  function lanes(r: AnswerRow): Lane[] {
    const runs = r.runs.filter((x) => x.href);
    const n = runs.length;
    return runs.map((x, i) => {
      const early = x.failed === null && x.stoppedEarly;
      const p = x.passed === null ? null : v(x.passed, x.hostVcpus);
      const q = x.failed === null ? null : v(x.failed, x.hostVcpus);
      const parts = [
        x.passed !== null ? `${early ? 'stopped early after' : 'last pass at'} ${browsers(x.passed)}` : 'none passed',
        x.failed !== null ? `first failure at ${browsers(x.failed)}` : early ? null : 'no failure',
      ].filter(Boolean);
      return {
        replica: x.replica,
        dy: (i - (n - 1) / 2) * PITCH,
        pass: p === null ? null : pos(p),
        early,
        fail: q === null ? null : pos(q),
        line: p !== null && q !== null ? { left: pos(Math.min(p, q)), width: `${Math.abs(pct(q) - pct(p))}%` } : null,
        title: `${replicas ? `Replica ${x.replica}: ` : ''}${parts.join(', ')}`,
      };
    });
  }

  /** One entry per distinct count for the readout: the replicas that agree stack, and `n` says how many. */
  interface Stack {
    at: number;
    n: number;
  }
  const stack = (runs: AnswerRun[], of: (x: AnswerRun) => number | null): Stack[] => {
    const tally = new Map<number, number>();
    for (const run of runs) {
      const d = of(run);
      if (d !== null) tally.set(d, (tally.get(d) ?? 0) + 1);
    }
    return [...tally].sort((a, b) => a[0] - b[0]).map(([at, n]) => ({ at, n }));
  };
  /** The replicas' marks: last passes, first failures, and the last passes of replicas that stopped without a failure. */
  const marks = (r: AnswerRow) => ({
    pass: stack(r.runs, (x) => (x.href && !(x.failed === null && x.stoppedEarly) ? x.passed : null)),
    fail: stack(r.runs, (x) => (x.href ? x.failed : null)),
    early: stack(r.runs, (x) => (x.href && x.failed === null && x.stoppedEarly ? x.passed : null)),
  });
  const ran = (r: AnswerRow) => r.runs.some((x) => x.href);
  const stoppedEarly = $derived(rows.some((r) => r.runs.some((x) => x.href && x.failed === null && x.stoppedEarly)));

  /** The row read out: "c8i.xlarge: last pass at 6 in 4 replicas and 7 in 1, first failure at 7 in 4 and 8 in 1; midpoint …". */
  function readout(r: AnswerRow): string {
    if (!ran(r)) return `${r.label}: not run yet`;
    const m = marks(r);
    const list = (xs: Stack[]) => xs.map((s) => `${s.at}${replicas ? ` in ${s.n === 1 ? '1 replica' : `${s.n} replicas`}` : ''}`).join(', ');
    const parts = [
      m.pass.length ? `last pass at ${list(m.pass)}` : null,
      m.early.length ? `stopped early after ${list(m.early)}` : null,
      m.fail.length ? `first failure at ${list(m.fail)}` : 'no failure',
      !m.pass.length && !m.early.length ? 'none passed' : null,
    ].filter(Boolean);
    const mid = midX(r);
    const cost = r.cost ? `≈ ${r.cost} per 1,000 tasks` : r.burst ? `one burst ≈ ${r.burst} per 1,000 tasks, no steady-state figure` : 'no cost';
    return `${r.label}: ${parts.join(', ')}${mid !== null ? `; midpoint ${midText(mid)} ${perVcpu ? 'browsers per vCPU' : 'browsers per host'}` : ''}; ${cost}; ran out: ${r.ranOut}${r.ranOutAt ? ` ${r.ranOutAt}` : ''}${r.best ? '; the clear winner on cost' : ''}`;
  }
</script>

<div class="answer">
  <div class="head">
    <span>Spec</span>
    <!-- The axis title, on a phone only: wider, it sits on the tick row. -->
    <span class="chartcol phone-title">{axisTitle}</span>
    <span class="num"><Term text={COST_TERM}>$ / 1k tasks</Term></span>
    <span>Ran out</span>
  </div>
  <div class="axis" aria-hidden="true">
    <span class="axis-title">{axisTitle}</span>
    <span class="chartcol ticks">
      {#each axis as t, i (t)}<span class="t" class:first={i === 0} class:last={i === axis.length - 1} style:left={pos(t)}>{tickText(t, perVcpu)}</span>{/each}
    </span>
  </div>
  <ol aria-label="How each spec performed">
    {#each rows as r, i (r.key)}
      {#if campaigns && (i === 0 || rows[i - 1].campaign !== r.campaign)}
        <li class="group" aria-hidden="true">{r.campaignTitle}</li>
      {/if}
      {@const mid = midX(r)}
      {@const m = marks(r)}
      <li class="spec" class:best={r.best} style:--c={specColor(r.series)} aria-label={readout(r)}>
        <p class="name" aria-hidden="true"><i class="dot"></i><span>{r.label}</span></p>
        <div class="chartcol strip" aria-hidden="true">
          {#each axis as t (t)}{#if t > 0}<i class="grid" style:left={pos(t)}></i>{/if}{/each}
          {#if !ran(r)}
            <span class="none">not run yet</span>
          {:else}
            {#each lanes(r) as l (l.replica)}
              <span class="lane" style:top="calc(50% + {l.dy}px)" title={l.title}>
                {#if l.line}<i class="link" style:left={l.line.left} style:width={l.line.width}></i>{/if}
                {#if l.pass !== null}
                  <span class="p" style:left={l.pass}>{#if l.early}<Mark kind="untested" size={7} />{:else}<i class="dot"></i>{/if}</span>
                {/if}
                {#if l.fail !== null}<span class="x" style:left={l.fail}><Mark kind="fail" size={7} /></span>{/if}
              </span>
            {/each}
            {#if mid !== null}
              <i class="mid" style:left={pos(mid)} title="Midpoint {midText(mid)}{r.partial ? ` (${r.partial})` : ''}"></i>
            {/if}
            {#if !m.pass.length && !m.early.length}<span class="none">none passed</span>{/if}
          {/if}
        </div>
        <div class="cost" aria-hidden="true" title={r.costNote}>
          {#if r.cost}
            <span class="v"><span class="approx">≈</span>{' '}{r.cost}</span>
            {#if r.share !== null}<span class="bar"><i style:width="{r.share * 100}%"></i></span>{/if}
          {:else if r.burst}
            <span class="v burst"><Term text={r.costNote ?? ''}><span class="approx">≈</span>{' '}{r.burst}</Term></span>
          {:else}
            <span class="v none">—</span>
          {/if}
        </div>
        <div class="out" aria-hidden="true">{r.ranOut}{#if r.ranOutAt}<span class="at">{r.ranOutAt}</span>{/if}</div>
      </li>
    {/each}
  </ol>
  <p class="key" aria-hidden="true">
    <span><i class="dot"></i>last pass</span>
    <span><Mark kind="fail" size={7} />first failure</span>
    <span><i class="mid-sw"></i>midpoint</span>
    {#if stoppedEarly}<span><Mark kind="untested" size={7} />stopped early</span>{/if}
    {#if noWinner}<span class="nowin">no clear winner on cost</span>{/if}
  </p>
</div>

<style>
  .answer {
    margin: 4px 0 0;
    font-size: 0.92rem;
  }
  .head,
  .spec,
  .axis {
    display: grid;
    grid-template-columns: 11.5rem minmax(0, 1fr) 8rem 7.5rem;
    column-gap: 20px;
    align-items: center;
  }
  .head {
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--muted);
    padding-bottom: 4px;
    border-bottom: 1px solid var(--rule);
  }
  .num {
    text-align: right;
  }
  /* The axis ticks, once, above the first row, its title at their left in the name column. */
  .axis {
    height: 22px;
  }
  .axis-title,
  .t {
    font-size: 11px;
    color: var(--muted);
  }
  .axis-title {
    text-align: right;
    padding-top: 2px;
    line-height: 1.3;
  }
  .phone-title {
    display: none;
  }
  .ticks {
    position: relative;
    height: 100%;
  }
  .t {
    position: absolute;
    top: 4px;
    transform: translateX(-50%);
    font-variant-numeric: tabular-nums;
  }
  ol {
    list-style: none;
    margin: 0;
    padding: 0;
    max-width: none;
  }
  .group {
    padding: 12px 0 2px;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--muted);
  }
  .spec {
    min-height: 44px;
    padding: 0;
    border-bottom: 1px solid var(--rule);
    border-radius: 6px;
  }
  .spec.best {
    background: var(--highlight);
  }
  .name {
    margin: 0;
    padding-left: 8px;
    font-weight: 600;
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
    line-height: 1.3;
  }
  .name .dot {
    transform: translateY(1px);
  }
  .dot {
    flex: none;
    display: inline-block;
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--c, var(--ink-2));
  }
  /* The strip: a lane per replica, PITCH px apart and centred on the row; its marks overflow the lane's own height. */
  .strip {
    position: relative;
    height: 44px;
    min-width: 0;
  }
  .grid {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 1px;
    background: var(--grid);
  }
  .lane {
    position: absolute;
    left: 0;
    right: 0;
    height: 5px;
    margin-top: -2.5px;
  }
  .link {
    position: absolute;
    top: 50%;
    height: 1px;
    margin-top: -0.5px;
    background: var(--c, var(--ink-2));
    opacity: 0.35;
  }
  .p,
  .x {
    position: absolute;
    top: 50%;
    display: inline-flex;
    line-height: 0;
    transform: translate(-50%, -50%);
  }
  .p :global(svg),
  .x :global(svg) {
    display: block;
  }
  .p .dot {
    width: 6px;
    height: 6px;
    box-shadow: 0 0 0 1px var(--bg);
  }
  .mid {
    position: absolute;
    top: 6px;
    bottom: 6px;
    width: 0;
    border-left: 1.5px solid var(--ink);
  }
  .none {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    font-size: 0.8rem;
    color: var(--muted);
  }
  /* One number per spec, with a bar under it for its share of the dearest spec's cost: the winner's in the accent,
     the others in the axis grey, so the dearest bar is never the heaviest mark. */
  .cost {
    text-align: right;
    padding-right: 4px;
    min-width: 0;
  }
  .v {
    display: block;
    font-size: 1.35rem;
    font-weight: 650;
    line-height: 1.2;
    letter-spacing: -0.01em;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .best .v {
    color: var(--accent-ink);
  }
  .v.burst {
    color: var(--muted);
    font-weight: 600;
  }
  .v.none {
    color: var(--muted);
    font-weight: 500;
  }
  .bar {
    display: block;
    position: relative;
    height: 4px;
    margin-top: 4px;
    border-radius: 2px;
    background: var(--surface-2);
    overflow: hidden;
  }
  .bar i {
    position: absolute;
    right: 0;
    top: 0;
    bottom: 0;
    background: var(--axis);
  }
  .best .bar i {
    background: var(--accent);
  }
  .out {
    font-size: 0.9rem;
    line-height: 1.25;
    display: flex;
    flex-direction: column;
  }
  .at {
    font-size: 0.8rem;
    color: var(--ink-2);
  }
  .key {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
    margin: 8px 0 0;
    font-size: 0.8rem;
    color: var(--muted);
    max-width: none;
  }
  .key span {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .key .dot {
    width: 6px;
    height: 6px;
    background: var(--ink-2);
  }
  .mid-sw {
    display: inline-block;
    width: 1.5px;
    height: 12px;
    background: var(--ink);
  }
  .nowin {
    margin-left: auto;
    font-style: italic;
  }

  /* Narrower: the name over the strip at the row's full width, the cost and what ran out on a line under it. */
  @media (max-width: 760px) {
    .head,
    .axis {
      grid-template-columns: minmax(0, 1fr);
    }
    .head > :not(.chartcol),
    .axis-title {
      display: none;
    }
    .phone-title {
      display: block;
    }
    /* The strip spans the page, so the end labels keep inside it. */
    .t.first {
      transform: none;
    }
    .t.last {
      transform: translateX(-100%);
    }
    .spec {
      grid-template-columns: minmax(0, 1fr) auto;
      column-gap: 24px;
      row-gap: 2px;
      padding: 8px 0;
    }
    .name,
    .strip {
      grid-column: 1 / -1;
    }
    .name {
      padding-left: 0;
    }
    .cost,
    .out {
      display: flex;
      flex-direction: column;
      gap: 1px;
    }
    .cost::before,
    .out::before {
      font-size: 0.72rem;
      font-weight: 600;
      color: var(--muted);
    }
    .cost::before {
      content: '$ / 1k tasks';
    }
    .out::before {
      content: 'Ran out';
    }
    .cost {
      text-align: left;
      padding: 0;
    }
    .bar {
      max-width: 8rem;
    }
    .out {
      text-align: right;
    }
  }
</style>
