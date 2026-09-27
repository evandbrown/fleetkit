<script lang="ts">
  // Every microVM in a trial as one lane on the trial's clock: its boot phases (a grey ramp), its five steps (one
  // colour each), and its destruction. Vertical rules mark when all were ready, when tasks were released, when
  // the last task returned and when the host was clean. Each lane's number links to that microVM.
  import * as f from '../lib/format';
  import { SUBJECT_LABEL } from '../lib/glossary';
  import { linear } from '../lib/scale';
  import { BOOT_PHASES, type Lane, type LaneSeg } from '../lib/shape';
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
  let tipW = $state(0);
  let hover: { lane: Lane; seg: LaneSeg | null; t: number; px: number; py: number } | null = $state(null);

  const n = $derived(lanes.length);
  const rowH = $derived(n <= 16 ? 18 : n <= 32 ? 12 : n <= 64 ? 7 : 5);
  const labelEvery = $derived(rowH >= 12 ? 1 : n <= 64 ? 5 : 10);
  const M = { top: 6, right: 12, bottom: 26, left: 44 };
  const H = $derived(M.top + n * rowH + M.bottom);
  const x = $derived(linear(domain, [M.left, Math.max(M.left + 1, w - M.right)]));

  const MARKS = $derived(
    [
      { key: 'all_ready', label: 'all ready', ms: marks.all_ready_ms },
      { key: 'release', label: 'tasks released', ms: marks.release_ms },
      { key: 'last_return', label: 'last task back', ms: marks.last_return_ms },
      { key: 'clean', label: 'host clean', ms: marks.clean_ms },
    ].filter((m): m is { key: string; label: string; ms: number } => m.ms !== null),
  );

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
    hover = { lane, seg, t, px, py };
  }

  function segText(s: LaneSeg): string {
    const span = `${f.num(s.start / 1000, 2)}–${f.num(s.end / 1000, 2)} s`;
    return `${s.label}${s.kind === 'step' && !s.ok ? ' (failed)' : ''}: ${f.num(s.end - s.start)} ms, ${span}`;
  }
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
      starting: {BOOT_PHASES.join(', then ')}
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
    {#if hover && w > 0}
      <div
        class="tip"
        bind:offsetWidth={tipW}
        style:left="{Math.max(0, hover.px + 12 + tipW > w ? hover.px - 12 - tipW : hover.px + 12)}px"
        style:top="{Math.max(0, hover.py + 14)}px"
      >
        <div class="tip-head">microVM {hover.lane.index} · {hover.lane.product}</div>
        <div>{hover.seg ? segText(hover.seg) : `${f.num(hover.t / 1000, 2)} s: waiting`}</div>
      </div>
    {/if}
  </div>
  <p class="marks muted">
    Rules, left to right:
    {#each MARKS as m, i (m.key)}{i ? '; ' : ''}{m.label} at {f.num(m.ms / 1000, 2)} s{/each}. Time is from the moment
    {n === 1 ? 'the microVM was' : `all ${n} microVMs were`} requested.
  </p>
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
  .tip {
    position: absolute;
    z-index: 5;
    width: max-content;
    max-width: min(20rem, 100%);
    background: var(--bg);
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    box-shadow: 0 2px 10px rgb(0 0 0 / 0.12);
    padding: 6px 9px;
    font-size: 0.78rem;
    pointer-events: none;
  }
  .tip-head {
    font-weight: 600;
  }
  .marks {
    font-size: 0.82rem;
    margin-top: 4px;
  }
</style>
