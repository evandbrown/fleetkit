<script lang="ts">
  // Every microVM in a trial as one lane on the trial's clock: its boot phases (a grey ramp), its five steps (one
  // colour each), and its destruction. The boot phases are named once, inside the first lane, where they fit.
  // Vertical rules mark when all were ready, when the tasks started and when the last one was done. Pointing at a lane
  // reads it out beside those marks. Each lane's number links to that microVM.
  import * as f from '../lib/format';
  import { SUBJECT_LABEL } from '../lib/glossary';
  import { linear } from '../lib/scale';
  import { type Lane, type LaneSeg } from '../lib/shape';
  import { STEP_NAMES, type Range, type TrialMarks } from '../lib/types';

  let {
    lanes,
    domain,
    marks,
    selected = null,
    hrefFor,
  }: { lanes: Lane[]; domain: Range; marks: TrialMarks; selected?: number | null; hrefFor: (index: number) => string } =
    $props();

  let w = $state(0);
  let hover: { lane: Lane; seg: LaneSeg | null; t: number; px: number } | null = $state(null);

  const n = $derived(lanes.length);
  const rowH = $derived(n <= 16 ? 18 : n <= 32 ? 12 : n <= 64 ? 7 : 5);
  const labelEvery = $derived(rowH >= 12 ? 1 : n <= 64 ? 5 : 10);
  const M = { top: 6, right: 12, bottom: 26, left: 44 };
  const H = $derived(M.top + n * rowH + M.bottom);
  const x = $derived(linear(domain, [M.left, Math.max(M.left + 1, w - M.right)]));

  const MARKS = $derived(
    [
      { key: 'all_ready', label: 'ready', ms: marks.all_ready_ms },
      { key: 'release', label: 'tasks start', ms: marks.release_ms },
      { key: 'last_return', label: 'tasks done', ms: marks.last_return_ms },
    ].filter((m): m is { key: string; label: string; ms: number } => m.ms !== null),
  );

  const BOOT_SHORT = ['hypervisor', 'kernel', 'guest daemon', 'Chromium'];

  function fill(s: LaneSeg): string {
    if (s.kind === 'boot') return `var(--ordinal-${s.phase})`;
    if (s.kind === 'step') return `var(--step-${s.step!.replaceAll('_', '-')})`;
    return 'var(--surface-2)';
  }

  function move(e: PointerEvent) {
    const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const px = e.clientX - box.left;
    const py = e.clientY - box.top;
    const row = Math.floor((py - M.top) / rowH);
    const lane = lanes[row];
    if (!lane || px < M.left) {
      hover = null;
      return;
    }
    const t = x.invert(px);
    const slack = x.invert(M.left + 3) - domain[0];
    const seg = lane.segs.find((s) => t >= s.start - slack && t <= s.end + slack) ?? null;
    hover = { lane, seg, t, px };
  }

  function segText(s: LaneSeg): string {
    const span = `${f.num(s.start / 1000, 2)}–${f.num(s.end / 1000, 2)} s`;
    return `${s.kind === 'boot' ? BOOT_SHORT[(s.phase ?? 1) - 1] : s.label}${s.kind === 'step' && !s.ok ? ' failed' : ''} ${f.num(s.end - s.start)} ms · ${span}`;
  }
  /** A boot phase's name inside the first lane, if it fits. */
  const phaseFont = $derived(rowH >= 16 ? 10 : 9);
  const fits = (label: string, width: number) => rowH >= 12 && label.length * phaseFont * 0.58 + 6 <= width;
</script>

<div class="lanes">
  <ul class="legend" aria-label="Colours">
    {#each STEP_NAMES as s (s)}
      <li><span class="sw" style:background="var(--step-{s.replaceAll('_', '-')})"></span>{SUBJECT_LABEL[s]}</li>
    {/each}
    <li>
      <span class="ramp">
        {#each [1, 2, 3, 4] as p (p)}<span class="sw" style:background="var(--ordinal-{p})"></span>{/each}
      </span>
      boot
    </li>
    <li><span class="sw destroy"></span>destroyed</li>
  </ul>

  <div class="plot" bind:clientWidth={w}>
    {#if w > 0}
      <svg width={w} height={H} role="img" aria-label="Each microVM's start, steps and destruction over the trial; the table below lists the same times" onpointermove={move} onpointerleave={() => (hover = null)}>
        {#each lanes as lane, r (lane.index)}
          {@const y0 = M.top + r * rowH}
          {#if selected === lane.index}
            <rect class="sel" x="0" y={y0} width={w} height={rowH} />
          {/if}
          {#if r % labelEvery === 0 || selected === lane.index}
            <a href={hrefFor(lane.index)} aria-label="microVM {lane.index}">
              <text class="lane-label" class:bad={lane.ok === false} x={M.left - 8} y={y0 + rowH / 2} dy="0.34em" text-anchor="end"
                >{lane.index}</text
              >
            </a>
          {/if}
          {#each lane.segs as s, k (k)}
            {@const x0 = x(s.start)}
            {@const sw = Math.max(1, x(s.end) - x0)}
            <rect
              x={sw > 3 ? x0 + 0.5 : x0}
              y={y0 + (rowH > 8 ? 2 : 0.5)}
              width={sw > 3 ? sw - 1 : sw}
              height={rowH - (rowH > 8 ? 4 : 1)}
              rx={rowH > 8 ? 2 : 0}
              fill={fill(s)}
              class:failed={s.kind === 'step' && !s.ok}
            />
            {#if r === 0 && s.kind === 'boot' && s.phase && fits(BOOT_SHORT[s.phase - 1], sw)}
              <text class="phase" class:dark={s.phase >= 3} x={x0 + 4} y={y0 + rowH / 2} dy="0.34em" style:font-size="{phaseFont}px">{BOOT_SHORT[s.phase - 1]}</text>
            {/if}
          {/each}
        {/each}
        {#each MARKS as m (m.key)}
          <line class="mark" x1={x(m.ms)} x2={x(m.ms)} y1={M.top - 4} y2={H - M.bottom} />
        {/each}
        <line class="axis" x1={M.left} x2={w - M.right} y1={H - M.bottom} y2={H - M.bottom} />
        {#each x.ticks(Math.max(2, Math.floor(w / 80))) as v (v)}
          <text class="tick" x={x(v)} y={H - M.bottom + 15} text-anchor="middle">{f.num(v / 1000, v % 1000 ? 1 : 0)} s</text>
        {/each}
        {#if hover}
          <line class="crosshair" x1={hover.px} x2={hover.px} y1={M.top} y2={H - M.bottom} />
        {/if}
      </svg>
    {/if}
  </div>
  <div class="foot">
    <ul class="marks" aria-label="Marks">
      {#each MARKS as m (m.key)}<li><span class="rule"></span>{m.label} <strong>{f.num(m.ms / 1000, 1)} s</strong></li>{/each}
    </ul>
    {#if hover}
      <p class="readout" aria-hidden="true">
        <strong>microVM {hover.lane.index}</strong>
        {hover.seg ? segText(hover.seg) : `${f.num(hover.t / 1000, 2)} s waiting`}
      </p>
    {/if}
  </div>
</div>

<style>
  .legend {
    list-style: none;
    padding: 0;
    margin: 6px 0 8px;
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
  .ramp .sw {
    margin-right: 1px;
  }
  .ramp {
    margin-right: 4px;
  }
  .sw.destroy {
    background: var(--surface-2);
    border: 1px solid var(--rule);
  }
  .plot {
    position: relative;
  }
  svg {
    display: block;
  }
  .sel {
    fill: var(--highlight);
  }
  .lane-label {
    fill: var(--ink-2);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  .lane-label.bad {
    fill: var(--critical);
    font-weight: 700;
  }
  a:focus-visible .lane-label {
    outline: 2px solid var(--accent);
    fill: var(--accent);
    font-weight: 700;
  }
  rect.failed {
    stroke: var(--critical);
    stroke-width: 1.5;
  }
  .mark {
    stroke: var(--ink);
    stroke-width: 1;
    opacity: 0.5;
  }
  .axis {
    stroke: var(--axis);
  }
  .tick {
    fill: var(--muted);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  .crosshair {
    stroke: var(--ink-2);
    opacity: 0.4;
  }
  .phase {
    fill: var(--ink);
    pointer-events: none;
  }
  .phase.dark {
    fill: #fff;
  }
  .foot {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: baseline;
    gap: 2px 16px;
    margin-top: 4px;
  }
  .readout {
    margin: 0;
    font-size: 0.82rem;
    color: var(--ink-2);
    font-variant-numeric: tabular-nums;
  }
  .readout strong {
    color: var(--ink);
    font-weight: 600;
  }
  .marks {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 2px 16px;
    font-size: 0.82rem;
    color: var(--ink-2);
    max-width: none;
  }
  .marks strong {
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .rule {
    display: inline-block;
    width: 1px;
    height: 11px;
    background: var(--ink);
    opacity: 0.5;
    margin-right: 6px;
    vertical-align: -1px;
  }
</style>
