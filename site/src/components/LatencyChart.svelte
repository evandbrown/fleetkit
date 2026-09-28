<script lang="ts">
  // Latency by density: each trial's slowest step p50, full width, against its SLO; under it, smaller, each trial's
  // whole-task p95 against its own. One band per density tested (bands.ts), shared with the every-trial grid below, so
  // the densities near the limit get as much room as the rest. In a band, each spec and replica has its own slot and
  // each trial its own place: a dot passed, a cross failed, in the spec's colour, hollow for every replica after the
  // first. The area
  // over a limit is tinted; when the specs' limits differ, each limit names whose it is (D74). Pointing at a trial
  // reads it out; each mark opens its trial.
  import * as f from '../lib/format';
  import { SUBJECT_LABEL } from '../lib/glossary';
  import { specColor } from '../lib/colors';
  import { bandIndex, bandLayout, makeBands, tickShown, type Band } from '../lib/bands';
  import { linear, niceDomain } from '../lib/scale';
  import type { Latency, LatencyPoint } from '../lib/shape';

  let { data, bands: given = null }: { data: Latency; bands?: Band[] | null } = $props();

  let wrap = $state(0);
  let hover: LatencyPoint | null = $state(null);
  const phone = $derived(wrap > 0 && wrap < 560);
  const touch = typeof matchMedia !== 'undefined' && matchMedia('(pointer: coarse)').matches;

  const points = $derived(data.series.flatMap((s) => s.points));
  const multi = $derived(data.series.length > 1);
  const bands = $derived(
    given && points.every((p) => bandIndex(given, p.x) >= 0) ? given : makeBands(points.map((p) => p.x), [], data.perVcpu),
  );

  const apart = $derived(data.replicas > 1);
  const slots = $derived(data.series.length * (apart ? data.replicas : 1));
  const slotOf = (p: LatencyPoint) => p.series * (apart ? data.replicas : 1) + (apart ? p.replica - 1 : 0);
  /** Each trial's place among the trials of its slot at its density, and how many there are. */
  const places = $derived.by(() => {
    const groups = new Map<string, LatencyPoint[]>();
    for (const p of points) {
      const k = `${slotOf(p)}|${p.x}`;
      groups.set(k, [...(groups.get(k) ?? []), p]);
    }
    const out = new Map<string, { k: number; n: number }>();
    for (const g of groups.values()) g.forEach((p, k) => out.set(p.key, { k, n: g.length }));
    return out;
  });

  const R = $derived(phone ? 4.5 : 5.5);

  const panels = $derived([
    { key: 'step', title: 'Slowest step p50 (ms)', value: (p: LatencyPoint) => p.step?.ms ?? null, targets: data.stepTargets, who: data.stepTargetWho, H: phone ? 230 : 280, cap: null },
    { key: 'task', title: 'Task p95 (ms)', value: (p: LatencyPoint) => p.task?.ms ?? null, targets: data.taskTargets, who: data.taskTargetWho, H: phone ? 130 : 150, cap: 1.2 },
  ]);
  const TOP = 14;
  const BOTTOM = 32;

  function place(p: LatencyPoint, L: ReturnType<typeof bandLayout>) {
    const i = bandIndex(bands, p.x);
    const inner = L.bw * 0.84;
    const sw = Math.min(26, inner / Math.max(1, slots));
    const pl = places.get(p.key) ?? { k: 0, n: 1 };
    const spread = Math.min(R * 1.1, (sw * 0.8) / Math.max(1, pl.n));
    return L.center(i) + (slotOf(p) - (slots - 1) / 2) * sw + (pl.k - (pl.n - 1) / 2) * spread;
  }
  const hitR = $derived.by(() => {
    const L = bandLayout(bands.length, wrap, phone);
    return Math.max(R + 1, Math.min(10, (L.bw * 0.84) / Math.max(1, slots) / 2 + 1));
  });

  function scaleY(values: number[], targets: number[], H: number, cap: number | null) {
    const want = cap ? [...targets.map((t) => t * cap), ...values.map((v) => v * 1.04)] : [...values.map((v) => v * 1.04), ...targets.map((t) => t * 1.25)];
    return linear(niceDomain(want, { count: 4 }), [H - BOTTOM, TOP]);
  }

  const readout = (p: LatencyPoint) =>
    [
      p.step ? `slowest step ${SUBJECT_LABEL[p.step.name]} p50 ${f.vs(p.step.ms, p.step.target)} ms` : null,
      p.task ? `task p95 ${f.vs(p.task.ms, p.task.target)} ms` : null,
    ]
      .filter(Boolean)
      .join(' · ');
  const X = (cx: number, cy: number, r: number) => `M${cx - r},${cy - r}L${cx + r},${cy + r}M${cx + r},${cy - r}L${cx - r},${cy + r}`;
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
      <figcaption>{P.title}</figcaption>
      {#if wrap > 0 && vals.length}
        {@const L = bandLayout(bands.length, wrap, phone)}
        {@const y = scaleY(vals, P.targets, P.H, P.cap)}
        {@const top = y.domain[1]}
        {@const shown = tickShown(bands, L.bw)}
        <svg width={wrap} height={P.H} role="group" aria-label="{P.title} by density, one mark per trial">
          {#each P.targets as t, ti (t)}
            <rect class="over" x={L.left} y={y(top)} width={L.right - L.left} height={Math.max(0, y(t) - y(top))} />
            <line class="slo" x1={L.left} x2={L.right} y1={y(t)} y2={y(t)} />
            <text class="slo-label" x={L.left + 6} y={y(t) - 5 - ti * 13}>SLO ≤ {f.num(t)} ms{P.who[ti] ? ` · ${P.who[ti]}` : ''}</text>
          {/each}
          {#each bands as b, i (b.label)}
            {#if b.untested}
              <rect class="untested" x={L.start(i) + 2} y={TOP} width={L.bw - 4} height={P.H - BOTTOM - TOP} rx="4" />
              <text class="untested-label" x={L.center(i)} y={TOP + 14} text-anchor="middle">not tested</text>
            {:else if i > 0}
              <line class="sep" x1={L.start(i)} x2={L.start(i)} y1={TOP} y2={P.H - BOTTOM} />
            {/if}
          {/each}
          {#each y.ticks(P.key === 'step' ? 4 : 3) as v (v)}
            <line class="grid" x1={L.left} x2={L.right} y1={y(v)} y2={y(v)} />
            <text class="tick" x={L.left - 6} y={y(v)} dy="0.32em" text-anchor="end">{f.num(v)}</text>
          {/each}
          <line class="axis" x1={L.left} x2={L.right} y1={P.H - BOTTOM} y2={P.H - BOTTOM} />
          {#each bands as b, i (b.label)}
            {#if shown(i)}<text class="tick" class:off={b.untested} x={L.center(i)} y={P.H - BOTTOM + 15} text-anchor="middle">{b.label}</text>{/if}
          {/each}
          <text class="axis-title" x={L.left} y={P.H - 3}>{data.perVcpu ? 'Density per host vCPU' : 'Density (microVMs)'}</text>

          {#each points as p (p.key)}
            {@const v = P.value(p)}
            {#if v !== null}
              {@const cx = place(p, L)}
              {@const cy = y(Math.min(v, top))}
              {@const hollow = apart && p.replica > 1}
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
                <circle class="hit" {cx} {cy} r={hitR} />
                {#if hover?.key === p.key}<circle class="halo" {cx} {cy} r={R + 4} />{/if}
                {#if p.passed}
                  <circle class="dot" class:hollow {cx} {cy} r={hollow ? R - 0.8 : R} />
                {:else}
                  <path class="cross" class:hollow d={X(cx, cy, R * 0.8)} />
                {/if}
              </a>
            {/if}
          {/each}
        </svg>
      {/if}
    </figure>
  {/each}
  <ul class="key" aria-label="Key">
    {#if multi}
      {#each data.series as s, i (s.key)}
        <li><svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="5" fill={specColor(i)} /></svg>{s.label}</li>
      {/each}
    {/if}
    {#if apart}
      <li><svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="5" fill="var(--ink-2)" /></svg>replica 1</li>
      <!-- Every replica after the first is hollow, so the key counts them: "replica 2", or "replicas 2–5". -->
      <li><svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="4.2" fill="var(--bg)" stroke="var(--ink-2)" stroke-width="1.6" /></svg>{data.replicas === 2 ? 'replica 2' : `replicas 2–${data.replicas}`}</li>
    {/if}
    <li><svg width="12" height="12" aria-hidden="true"><path d={X(6, 6, 4)} stroke="var(--ink-2)" stroke-width="2.2" stroke-linecap="round" /></svg>failed</li>
    <li><i class="sw over-sw"></i>over the SLO</li>
    {#if bands.some((b) => b.untested)}<li><i class="sw untested-sw"></i>not tested</li>{/if}
  </ul>
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
    margin-top: 10px;
  }
  figcaption {
    font-weight: 600;
    font-size: 0.9rem;
    margin: 4px 0 0;
  }
  .pn-task figcaption {
    font-size: 0.84rem;
    color: var(--ink-2);
  }
  svg {
    display: block;
    overflow: visible;
  }
  .over {
    fill: var(--critical-bg);
    opacity: 0.75;
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
  .sep {
    stroke: var(--grid);
    stroke-dasharray: 2 3;
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
  .tick.off {
    fill: var(--muted);
  }
  .axis-title {
    fill: var(--muted);
    font-size: 12px;
  }
  .mark {
    cursor: pointer;
  }
  .hit {
    fill: transparent;
  }
  .dot {
    fill: var(--c);
    stroke: var(--bg);
    stroke-width: 1;
  }
  .dot.hollow {
    fill: var(--bg);
    stroke: var(--c);
    stroke-width: 1.8;
  }
  .cross {
    fill: none;
    stroke: var(--c);
    stroke-width: 2.6;
    stroke-linecap: round;
  }
  .cross.hollow {
    stroke-width: 1.8;
  }
  .halo {
    fill: var(--highlight);
    stroke: var(--ink);
    stroke-width: 1.5;
  }
  .mark:focus-visible {
    outline: none;
  }
  .mark:focus-visible .dot,
  .mark:focus-visible .cross {
    stroke: var(--ink);
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
  .sw {
    display: inline-block;
    width: 14px;
    height: 10px;
    border-radius: 2px;
  }
  .over-sw {
    background: var(--critical-bg);
    border-top: 1.5px dashed var(--critical);
  }
  .untested-sw {
    background: var(--surface);
    border: 1px dashed var(--axis);
  }
</style>
