<script lang="ts">
  // Latency by browsers per host: per spec, a line through the median of its trials at each count, with every trial
  // as a small mark behind it. The main panel is each trial's slowest step p50 against its SLO; under it, a small
  // multiple of the whole task's p95 against its own. The x axis is linear (browsers per host vCPU across host sizes,
  // browsers per host on one size), so a slope means what it looks like; where the campaign listed counts above the
  // highest tested, the region beyond it is shaded not tested. Ticks every 0.25 (per vCPU) or at round steps, labelled
  // every 0.5, plus a label where each spec first failed when it has room. The SLO is a dashed line with a thin
  // tinted band over it, labelled on the chart; when the specs' limits differ, each names whose it is (D74). Failed
  // trials are crosses. Pointing at (or tabbing to) a trial reads it out, replica included; each mark opens its trial.
  import * as f from '../lib/format';
  import { SUBJECT_LABEL } from '../lib/glossary';
  import { specColor } from '../lib/colors';
  import { CHART_RIGHT, chartLeft, latencyTicks, type Band } from '../lib/bands';
  import { linear, linePath, niceDomain } from '../lib/scale';
  import type { Latency, LatencyMedian, LatencyPoint } from '../lib/shape';
  import MarkShape from './MarkShape.svelte';

  let { data, bands: given = null }: { data: Latency; bands?: Band[] | null } = $props();

  let wrap = $state(0);
  let hover: LatencyPoint | null = $state(null);
  const phone = $derived(wrap > 0 && wrap < 560);
  const touch = typeof matchMedia !== 'undefined' && matchMedia('(pointer: coarse)').matches;

  const points = $derived(data.series.flatMap((s) => s.points));
  const multi = $derived(data.series.length > 1);
  /** Where each spec first failed: a labelled tick each, when there is room. */
  const firstFails = $derived(
    data.series.flatMap((s) => {
      const failed = s.points.filter((p) => !p.passed);
      return failed.length ? [Math.min(...failed.map((p) => p.x))] : [];
    }),
  );
  const xs = $derived([...new Set(points.map((p) => p.x))].sort((a, b) => a - b));
  const maxX = $derived(xs.at(-1) ?? 1);
  /** The campaign listed counts above the highest tested (the every-trial bands say so): shade beyond it. */
  const beyond = $derived(!!given?.some((b) => b.untested));
  /** 0 to a little past the highest count tested (or past 2 browsers per vCPU), further when a region is shaded. */
  const end = $derived(Math.max(data.perVcpu ? 2 * 1.05 : 0, maxX * (beyond ? 1.2 : 1.05)));
  const left = $derived(chartLeft(phone));
  const x = $derived(linear([0, end], [left, wrap - CHART_RIGHT]));
  const axisTicks = $derived(latencyTicks(end, x, data.perVcpu, firstFails, phone ? 4 : 6));
  /** How wide a count's cluster of marks may be: 16 px, or less where counts sit closer than that. */
  const cluster = $derived(Math.min(16, 0.6 * Math.min(Infinity, ...xs.slice(1).map((v, i) => x(v) - x(xs[i])))));

  /** Each trial's place among its spec's trials at its count (replicas pooled), for a narrow cluster on the line. */
  const places = $derived.by(() => {
    const groups = new Map<string, LatencyPoint[]>();
    for (const p of points) {
      const k = `${p.series}|${p.x}`;
      groups.set(k, [...(groups.get(k) ?? []), p]);
    }
    const out = new Map<string, { k: number; n: number }>();
    for (const g of groups.values()) g.forEach((p, k) => out.set(p.key, { k, n: g.length }));
    return out;
  });

  /** A passed mark's radius (4 px across) and a failed one's size. */
  const R = 2;
  const FAIL = 7;
  const TOP = 12;
  const BOTTOM = 34;

  const panels = $derived([
    {
      key: 'step',
      title: 'Slowest step p50 (ms)',
      value: (p: LatencyPoint) => p.step?.ms ?? null,
      median: (m: LatencyMedian) => m.step,
      targets: data.stepTargets,
      who: data.stepTargetWho,
      H: (phone ? 210 : 260) + TOP + BOTTOM,
      cap: null as number | null,
      yTicks: 4,
    },
    {
      key: 'task',
      title: 'Task p95 (ms)',
      value: (p: LatencyPoint) => p.task?.ms ?? null,
      median: (m: LatencyMedian) => m.task,
      targets: data.taskTargets,
      who: data.taskTargetWho,
      H: 90 + TOP + BOTTOM,
      cap: 1.2 as number | null,
      yTicks: 2,
    },
  ]);

  function place(p: LatencyPoint) {
    const pl = places.get(p.key) ?? { k: 0, n: 1 };
    const step = pl.n > 1 ? Math.min(3.5, cluster / (pl.n - 1)) : 0;
    return x(p.x) + (pl.k - (pl.n - 1) / 2) * step;
  }

  function scaleY(values: number[], targets: number[], H: number, cap: number | null) {
    const want = cap ? [...targets.map((t) => t * cap), ...values.map((v) => v * 1.04)] : [...values.map((v) => v * 1.04), ...targets.map((t) => t * 1.25)];
    return linear(niceDomain(want, { count: 4 }), [H - BOTTOM, TOP]);
  }

  /** Where each limit's label goes: above its band, and above the label below when two limits sit close. */
  function labelYs(targets: number[], y: (v: number) => number): number[] {
    const out: number[] = [];
    for (const t of targets) {
      const want = y(t) - 10;
      const below = out.at(-1);
      out.push(below === undefined ? want : Math.min(want, below - 13));
    }
    return out;
  }

  const readout = (p: LatencyPoint) =>
    [
      p.step ? `slowest step ${SUBJECT_LABEL[p.step.name]} p50 ${f.vs(p.step.ms, p.step.target)} ms` : null,
      p.task ? `task p95 ${f.vs(p.task.ms, p.task.target)} ms` : null,
    ]
      .filter(Boolean)
      .join(' · ');
</script>

<div class="lat" bind:clientWidth={wrap}>
  <p class="read" aria-hidden="true">
    {#if hover}
      <strong>{hover.title}</strong>
      <span class:bad={!hover.passed}>{hover.passed ? 'passed' : 'failed'}</span>
      <span>{readout(hover)}</span>
    {:else}
      <span class="hint">{touch ? 'Tap' : 'Click'} a mark to open its trial</span>
    {/if}
  </p>
  {#each panels as P (P.key)}
    {@const vals = points.map(P.value).filter((v): v is number => v !== null)}
    <figure class="pn pn-{P.key}">
      <figcaption>
        {P.title}
        {#if P.key === 'step'}<span class="sub" class:block={phone}>line: the median at each count · one mark per trial</span>{/if}
      </figcaption>
      {#if wrap > 0 && vals.length}
        {@const y = scaleY(vals, P.targets, P.H, P.cap)}
        {@const top = y.domain[1]}
        {@const bottom = P.H - BOTTOM}
        {@const right = wrap - CHART_RIGHT}
        {@const shade = beyond ? x(maxX) + cluster / 2 + 6 : null}
        <svg width={wrap} height={P.H} role="group" aria-label="{P.title} by browsers per host: per spec, a line through the median at each count, and a mark per trial">
          {#if shade !== null}
            <rect class="untested" x={shade} y={TOP} width={Math.max(0, right - shade)} height={bottom - TOP} rx="4" />
            {#if P.key === 'step' && right - shade >= 70}<text class="untested-label" x={(shade + right) / 2} y={TOP + 14} text-anchor="middle">not tested</text>{/if}
          {/if}
          {#each y.ticks(P.yTicks) as v (v)}
            <line class="grid" x1={left} x2={right} y1={y(v)} y2={y(v)} />
            <text class="tick" x={left - 6} y={y(v)} dy="0.32em" text-anchor="end">{f.num(v)}</text>
          {/each}
          <line class="axis" x1={left} x2={right} y1={bottom} y2={bottom} />
          {#each axisTicks as t (t.v)}
            <line class="axis" x1={x(t.v)} x2={x(t.v)} y1={bottom} y2={bottom + (t.label === null ? 3 : 5)} />
            {#if t.label !== null}<text class="tick x" class:fail={t.failure} x={x(t.v)} y={bottom + 17} text-anchor="middle">{t.label}</text>{/if}
          {/each}
          <text class="axis-title" x={left} y={P.H - 3}>{data.perVcpu ? 'Browsers per vCPU' : 'Browsers per host'}</text>

          {#each P.targets as t (t)}
            <rect class="over" x={left} y={y(t) - 6} width={right - left} height="6" />
            <line class="slo" x1={left} x2={right} y1={y(t)} y2={y(t)} />
          {/each}

          {#each data.series as s, i (s.key)}
            {@const line = s.medians.map((m) => {
              const v = P.median(m);
              return { x: x(m.x), y: v === null ? null : y(Math.min(v, top)) };
            })}
            <!-- A line needs two medians; a single count is its marks alone. -->
            {#if line.filter((q) => q.y !== null).length > 1}
              <path class="line" class:lit={hover?.series === i} d={linePath(line)} style:--c={specColor(i)} />
            {/if}
          {/each}

          {#each labelYs(P.targets, y) as ly, ti (P.targets[ti])}
            <text class="slo-label" x={left + 6} y={ly}>SLO ≤ {f.num(P.targets[ti])} ms{P.who[ti] ? ` · ${P.who[ti]}` : ''}</text>
          {/each}

          {#each points as p (p.key)}
            {@const v = P.value(p)}
            {#if v !== null}
              {@const cx = place(p)}
              {@const cy = y(Math.min(v, top))}
              <a
                class="mark"
                href={p.href}
                aria-label="{p.title}, {p.passed ? 'passed' : 'failed'}: {P.key === 'step' ? `slowest step p50 ${f.num(v)} ms` : `task p95 ${f.num(v)} ms`}"
                onpointerenter={() => (hover = p)}
                onpointerleave={() => (hover = null)}
                onfocus={() => (hover = p)}
                onblur={() => (hover = null)}
                style:--c={specColor(p.series)}
              >
                <circle class="hit" {cx} {cy} r="5" />
                {#if hover?.key === p.key}<circle class="halo" {cx} {cy} r={(p.passed ? R : FAIL / 2) + 4} />{/if}
                {#if p.passed}
                  <circle class="dot" {cx} {cy} r={R} />
                {:else}
                  <g class="fail"><MarkShape kind="fail" {cx} {cy} size={FAIL} /></g>
                {/if}
              </a>
            {/if}
          {/each}
        </svg>
      {/if}
    </figure>
  {/each}
  <!-- The key names the specs; the SLO and the failures are labelled on the chart itself. -->
  {#if multi || beyond}
    <ul class="key" aria-label="Key">
      {#if multi}
        {#each data.series as s, i (s.key)}
          <li>
            <svg width="20" height="12" aria-hidden="true" style:--c={specColor(i)}>
              <line class="line" x1="1" x2="19" y1="6" y2="6" />
              <circle class="dot" cx="10" cy="6" r={R} />
            </svg>{s.label}
          </li>
        {/each}
      {/if}
      {#if beyond}<li><i class="sw untested-sw"></i>not tested</li>{/if}
    </ul>
  {/if}
</div>

<style>
  .lat {
    margin: 4px 0 0;
    min-width: 0;
  }
  /* One line, always: a readout that wrapped would move the marks under the pointer. */
  .read {
    margin: 0 0 2px;
    height: 1.5em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 0.85rem;
    color: var(--ink-2);
    font-variant-numeric: tabular-nums;
    max-width: none;
  }
  .read strong {
    color: var(--ink);
    font-weight: 650;
  }
  .read > span + span,
  .read > strong + span {
    margin-left: 10px;
  }
  .read .bad {
    color: var(--critical);
    font-weight: 600;
  }
  .hint {
    color: var(--muted);
  }
  .pn {
    margin: 0;
    min-width: 0;
  }
  .pn-task {
    margin-top: 8px;
  }
  figcaption {
    font-weight: 600;
    font-size: 0.9rem;
    margin: 4px 0 0;
  }
  .sub {
    margin-left: 8px;
    font-weight: 400;
    font-size: 0.8rem;
    color: var(--muted);
  }
  .sub.block {
    display: block;
    margin: 0;
  }
  .pn-task figcaption {
    font-size: 0.84rem;
    color: var(--muted);
  }
  svg {
    display: block;
    overflow: visible;
  }
  .over {
    fill: var(--critical-bg);
  }
  .slo {
    stroke: var(--critical);
    stroke-width: 1.25;
    stroke-dasharray: 5 4;
  }
  .slo-label {
    fill: var(--critical);
    font-size: 11px;
    font-weight: 600;
    paint-order: stroke;
    stroke: var(--bg);
    stroke-width: 3px;
  }
  .grid {
    stroke: var(--grid);
  }
  .untested {
    fill: var(--surface);
    stroke: var(--rule);
    stroke-dasharray: 3 3;
  }
  .untested-label {
    fill: var(--muted);
    font-size: 11px;
  }
  .axis {
    stroke: var(--axis);
  }
  .tick {
    fill: var(--ink-2);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  .tick.fail {
    fill: var(--ink);
    font-weight: 600;
  }
  .axis-title {
    fill: var(--muted);
    font-size: 12px;
  }
  .line {
    fill: none;
    stroke: var(--c);
    stroke-width: 1.5;
    stroke-linejoin: round;
    stroke-linecap: round;
  }
  .line.lit {
    stroke-width: 2.5;
  }
  .mark {
    cursor: pointer;
  }
  .hit {
    fill: transparent;
  }
  .dot {
    fill: var(--c);
    opacity: 0.55;
  }
  .fail {
    opacity: 0.85;
  }
  .halo {
    fill: var(--highlight);
    stroke: var(--ink);
    stroke-width: 1.5;
  }
  .mark:focus-visible {
    outline: none;
  }
  .mark:focus-visible .dot {
    opacity: 1;
    stroke: var(--ink);
    stroke-width: 1.5;
  }
  .key {
    list-style: none;
    padding: 0;
    margin: 6px 0 0;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
    font-size: 0.82rem;
    color: var(--ink-2);
    max-width: none;
  }
  .key li {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .key svg {
    display: inline-block;
    overflow: visible;
  }
  .sw {
    display: inline-block;
    width: 14px;
    height: 10px;
    border-radius: 2px;
  }
  .untested-sw {
    background: var(--surface);
    border: 1px dashed var(--axis);
  }
</style>
