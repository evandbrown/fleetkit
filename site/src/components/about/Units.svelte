<script lang="ts">
  // Campaign → runs → trials → microVMs, as four panels: left to right on a wide screen, two by two below 600 px.
  // All four are an illustration. The trials panel is an example ladder: a column of squares per density (one square
  // per microVM) under the trial's mark (passed, failed, not tested), with the number of trials where there were
  // more than one.
  import { num } from '../../lib/format';
  import MarkShape from '../MarkShape.svelte';

  interface Rung {
    density: number;
    result: 'passed' | 'failed' | 'not_tested';
    trials: number;
  }
  const ladder: Rung[] = [
    { density: 1, result: 'passed', trials: 1 },
    { density: 2, result: 'passed', trials: 1 },
    { density: 4, result: 'passed', trials: 1 },
    { density: 8, result: 'passed', trials: 3 },
    { density: 12, result: 'failed', trials: 3 },
    { density: 16, result: 'not_tested', trials: 0 },
  ];
  const focus = 8;

  // The trials panel: 280 × 150. Squares stand on y = 126, density labels below, marks above each column.
  const FW = 280;
  const PAD = 14;
  const BASE = 126;
  const H = 78;
  const GAP = 2;

  const layout = (() => {
    const max = Math.max(1, ...ladder.map((r) => r.density));
    const pitch = (FW - 2 * PAD) / Math.max(1, ladder.length);
    let cols = 0;
    let size = 0;
    for (const c of [1, 2, 3, 4]) {
      const rows = Math.ceil(max / c);
      const s = Math.min(9, Math.floor((H + GAP) / rows) - GAP);
      if (s >= 4 && c * (s + GAP) - GAP <= pitch - 8) {
        cols = c;
        size = s;
        break;
      }
    }
    return ladder.map((r, i) => {
      const cx = PAD + pitch * (i + 0.5);
      if (!cols) {
        const w = Math.min(16, pitch - 10);
        const h = Math.max(2, (r.density / max) * H);
        return { r, cx, top: BASE - h, squares: [], size: 0, bar: { x: cx - w / 2, y: BASE - h, w, h } };
      }
      const c = Math.min(cols, r.density);
      const w = c * (size + GAP) - GAP;
      const rows = Math.ceil(r.density / cols);
      const squares = Array.from({ length: r.density }, (_, k) => ({
        x: cx - w / 2 + (k % cols) * (size + GAP),
        y: BASE - (Math.floor(k / cols) + 1) * (size + GAP) + GAP,
      }));
      return { r, cx, top: BASE - rows * (size + GAP) + GAP, squares, size, bar: null };
    });
  })();
  const kind = (r: Rung) => (r.result === 'passed' ? 'pass' : r.result === 'failed' ? 'fail' : 'untested');

  // The microVMs panel: 220 × 150, one small browser window per microVM at the focused density.
  const TW = 200;
  const TH = 104;
  const tiles = (() => {
    const n = focus;
    const shown = n <= 24 ? n : 23;
    const cols = n <= 4 ? n : n <= 16 ? 4 : 6;
    const rows = Math.ceil((shown + (shown < n ? 1 : 0)) / cols);
    const w = (TW - (cols - 1) * 8) / cols;
    const h = Math.min(w * 0.72, (TH - (rows - 1) * 8) / rows);
    const out = Array.from({ length: shown }, (_, k) => ({ x: 10 + (k % cols) * (w + 8), y: 36 + Math.floor(k / cols) * (h + 8) }));
    const more = shown < n ? { x: 10 + (shown % cols) * (w + 8), y: 36 + Math.floor(shown / cols) * (h + 8), n: n - shown } : null;
    return { out, more, w, h, height: Math.ceil(36 + rows * (h + 8) + 4) };
  })();

  const HOSTS = [
    { y: 8, label: 'Spec A · replica 1' },
    { y: 42, label: 'Spec A · replica 2' },
    { y: 84, label: 'Spec B · replica 1' },
    { y: 118, label: 'Spec B · replica 2' },
  ];
</script>

<div
  class="units"
  role="img"
  aria-label="A campaign asks one question with several specs. Each run is one spec on its own worker host; a replica is the same spec on another host. A run tests its densities as trials, lowest first, with 3 trials at the boundary between pass and fail. A trial at density N starts N microVMs at once, one browser task in each."
>
  <div class="pane p-campaign">
    <p class="h">Campaign</p>
    <p class="s">a question and its specs</p>
    <svg viewBox="0 0 170 150" aria-hidden="true">
      <rect class="card" x="0.5" y="0.5" width="169" height="149" rx="10" />
      <text class="t-q" x="14" y="28">Question</text>
      <rect class="box" x="14" y="46" width="142" height="32" rx="6" />
      <text class="t-item" x="26" y="67">Spec A</text>
      <rect class="box" x="14" y="92" width="142" height="32" rx="6" />
      <text class="t-item" x="26" y="113">Spec B</text>
    </svg>
  </div>
  <span class="arrow" aria-hidden="true">→</span>
  <div class="pane p-runs">
    <p class="h">Runs</p>
    <p class="s">a replica is the same spec on another host</p>
    <svg viewBox="0 0 150 150" aria-hidden="true">
      {#each HOSTS as h (h.label)}
        <rect class="box" x="0.5" y={h.y} width="149" height="26" rx="6" />
        <text class="t-item" x="75" y={h.y + 17} text-anchor="middle">{h.label}</text>
      {/each}
    </svg>
  </div>
  <span class="arrow" aria-hidden="true">→</span>
  <div class="pane p-trials">
    <p class="h">Trials</p>
    <p class="s">one per density, 3 at the boundary</p>
    <svg viewBox="0 0 {FW} 150" aria-hidden="true">
      <rect class="frame" x="0.5" y="0.5" width={FW - 1} height="149" rx="10" />
      {#each layout as c (c.r.density)}
        {#if c.bar}
          <rect class="sq {c.r.result === 'not_tested' ? 'off' : ''}" x={c.bar.x} y={c.bar.y} width={c.bar.w} height={c.bar.h} rx="2" />
        {:else}
          {#each c.squares as s, k (k)}
            <rect class="sq {c.r.result === 'not_tested' ? 'off' : ''}" x={s.x} y={s.y} width={c.size} height={c.size} rx="1.5" />
          {/each}
        {/if}
        <MarkShape kind={kind(c.r)} cx={c.cx} cy={c.top - 12} size={10} />
        {#if c.r.trials > 1}<text class="t-count" x={c.cx + 8} y={c.top - 8}>×{c.r.trials}</text>{/if}
        <text class="t-density" x={c.cx} y="142" text-anchor="middle">{num(c.r.density)}</text>
      {/each}
    </svg>
  </div>
  <span class="arrow" aria-hidden="true">→</span>
  <div class="pane p-vms">
    <p class="h">MicroVMs</p>
    <p class="s">density N starts N at once</p>
    <svg viewBox="0 0 220 {tiles.height}" aria-hidden="true">
      <rect class="frame" x="0.5" y="0.5" width="219" height={tiles.height - 1} rx="10" />
      <text class="t-item" x="10" y="22">Density {num(focus)}</text>
      {#each tiles.out as t, k (k)}
        <rect class="tile" x={t.x} y={t.y} width={tiles.w} height={tiles.h} rx="4" />
        <line class="tile-bar" x1={t.x} y1={t.y + 8} x2={t.x + tiles.w} y2={t.y + 8} />
        {#each [5, 10, 15] as dx (dx)}<circle class="dot" cx={t.x + dx} cy={t.y + 4.5} r="1.6" />{/each}
      {/each}
      {#if tiles.more}
        <text class="t-item" x={tiles.more.x + tiles.w / 2} y={tiles.more.y + tiles.h / 2 + 4} text-anchor="middle">+{num(tiles.more.n)}</text>
      {/if}
    </svg>
  </div>
</div>

<style>
  .units {
    display: grid;
    grid-template-columns: minmax(0, 170fr) 22px minmax(0, 150fr) 22px minmax(0, 280fr) 22px minmax(0, 220fr);
    align-items: start;
    gap: 0 4px;
  }
  .pane {
    min-width: 0;
  }
  .h {
    margin: 0;
    font-weight: 650;
  }
  .s {
    margin: 0 0 10px;
    font-size: 0.85rem;
    color: var(--ink-2);
    min-height: 2.6em;
  }
  .arrow {
    align-self: center;
    margin-top: 56px;
    color: var(--ink-2);
    text-align: center;
  }
  svg {
    display: block;
    width: 100%;
    height: auto;
    font-family: var(--font);
  }
  @media (max-width: 600px) {
    .units {
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 20px 14px;
    }
    .arrow {
      display: none;
    }
    .p-trials {
      grid-column: 1 / -1;
    }
    .p-vms {
      grid-column: 1 / -1;
    }
    .p-vms svg {
      max-width: 260px;
    }
  }
  .card {
    fill: var(--surface);
    stroke: var(--rule);
  }
  .box {
    fill: var(--bg);
    stroke: var(--field-border);
  }
  .frame {
    fill: var(--bg);
    stroke: var(--field-border);
  }
  .sq {
    fill: var(--ordinal-2);
  }
  .sq.off {
    fill: none;
    stroke: var(--axis);
  }
  .tile {
    fill: var(--bg);
    stroke: var(--ink-2);
  }
  .tile-bar {
    stroke: var(--ink-2);
    opacity: 0.4;
  }
  .dot {
    fill: var(--ink-2);
    opacity: 0.5;
  }
  text {
    fill: var(--ink);
    font-size: 12px;
  }
  .t-q {
    font-weight: 600;
    fill: var(--ink-2);
  }
  .t-item {
    font-weight: 500;
  }
  .t-count {
    font-size: 10px;
    fill: var(--ink-2);
  }
  .t-density {
    font-size: 11px;
    fill: var(--ink-2);
    font-variant-numeric: tabular-nums;
  }
</style>
