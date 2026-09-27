<script lang="ts">
  // Latency against density, one line per run, with each spec's criterion as a reference line. Two panels, one
  // per criterion that decides most densities: the whole task's p95 and the slowest step's median. Each point is
  // the slowest trial at that density, since a density passes only if every trial does; the small dots are the
  // other trials. Colour follows the spec, marker shape the replica. A table below carries every value.
  import * as f from '../lib/format';
  import { SUBJECT_LABEL } from '../lib/glossary';
  import { href } from '../lib/router';
  import { linear, linePath, nearest, niceDomain } from '../lib/scale';
  import type { CapacityLine, CapacityPoint, CapacityTrial } from '../lib/shape';

  let { campaign, lines, labels }: { campaign: string; lines: CapacityLine[]; labels: Record<string, string> } =
    $props();

  type XMode = 'density' | 'per_vcpu';
  let xMode: XMode = $state('density');
  let hoverX: number | null = $state(null);
  let active: number | null = $state(null);
  let widths = $state([0, 0]);
  let tipWidths = $state([0, 0]);

  const PANELS = [
    {
      key: 'task',
      title: 'Whole task p95',
      point: (p: CapacityPoint) => p.taskP95,
      trial: (t: CapacityTrial) => t.taskP95,
      target: (l: CapacityLine) => l.targets.task_p95_ms,
    },
    {
      key: 'step',
      title: "Slowest step's median (p50)",
      point: (p: CapacityPoint) => p.stepP50,
      trial: (t: CapacityTrial) => t.stepP50,
      target: (l: CapacityLine) => l.targets.step_p50_ms,
    },
  ] as const;

  const H = 230;
  const M = { top: 14, right: 14, bottom: 36, left: 54 };

  const xOf = (l: CapacityLine, p: CapacityPoint) => (xMode === 'density' ? p.density : p.perHostVcpu);
  const color = (l: CapacityLine) => `var(--series-${(l.specIndex % 8) + 1})`;
  const sameX = (a: number, b: number) => Math.abs(a - b) < 1e-9;

  const xs = $derived([...new Set(lines.flatMap((l) => l.points.map((p) => xOf(l, p))))].sort((a, b) => a - b));
  const xDomain = $derived(niceDomain(xs, { count: 6 }));

  function yDomain(panel: (typeof PANELS)[number]): [number, number] {
    const vals = lines.flatMap((l) => [
      panel.target(l) * 1.15,
      ...l.points.flatMap((p) => p.trials.map((t) => panel.trial(t) ?? 0)),
    ]);
    return niceDomain(vals, { count: 5 });
  }

  /** A marker for the replica: circle, square, triangle, diamond. */
  function marker(replica: number, x: number, y: number, r = 4.5): string {
    const s = (replica - 1) % 4;
    if (s === 0) return `M${x - r},${y}a${r},${r} 0 1,0 ${2 * r},0a${r},${r} 0 1,0 ${-2 * r},0`;
    if (s === 1) {
      const q = r * 0.88;
      return `M${x - q},${y - q}h${2 * q}v${2 * q}h${-2 * q}Z`;
    }
    if (s === 2) return `M${x},${y - r * 1.15}L${x + r * 1.1},${y + r * 0.8}L${x - r * 1.1},${y + r * 0.8}Z`;
    return `M${x},${y - r * 1.25}L${x + r * 1.1},${y}L${x},${y + r * 1.25}L${x - r * 1.1},${y}Z`;
  }

  const xLabel = $derived(xMode === 'density' ? 'Density (microVMs started at once)' : 'Density per host vCPU');
  const fmtX = (v: number) => (xMode === 'density' ? f.num(v) : f.ratio(v));

  function move(i: number, e: PointerEvent) {
    const svg = e.currentTarget as SVGSVGElement;
    const box = svg.getBoundingClientRect();
    const x = linear(xDomain, [M.left, widths[i] - M.right]).invert(e.clientX - box.left);
    const k = nearest(xs, x);
    hoverX = k >= 0 ? xs[k] : null;
    active = i;
  }
  function key(i: number, e: KeyboardEvent) {
    if (!xs.length) return;
    let k = hoverX === null ? -1 : xs.findIndex((v) => sameX(v, hoverX!));
    if (e.key === 'ArrowRight') k = Math.min(xs.length - 1, k + 1);
    else if (e.key === 'ArrowLeft') k = Math.max(0, k - 1);
    else if (e.key === 'Home') k = 0;
    else if (e.key === 'End') k = xs.length - 1;
    else if (e.key === 'Escape') {
      hoverX = null;
      return;
    } else return;
    e.preventDefault();
    hoverX = xs[k];
    active = i;
  }

  /** Every run with a point at x: its point, for the readout. */
  const at = (x: number) =>
    lines.flatMap((l) => {
      const p = l.points.find((q) => sameX(xOf(l, q), x));
      return p ? [{ l, p }] : [];
    });

  const runLabel = (l: CapacityLine) => `${labels[l.spec] ?? l.spec}, replica ${l.replica}`;

  /** The readout as one sentence, for screen readers. */
  function readout(panel: (typeof PANELS)[number], x: number): string {
    const head = xMode === 'density' ? `Density ${fmtX(x)}` : `${fmtX(x)} per host vCPU`;
    const rows = at(x).map(({ l, p }) => {
      const v = panel.point(p);
      return `${runLabel(l)}: ${v === null ? 'not recorded' : `${f.vs(v, panel.target(l))} ms`}, ${p.result}`;
    });
    return `${head}. ${rows.join('; ')}.`;
  }
</script>

<div class="capacity">
  <div class="controls">
    <fieldset>
      <legend>Across</legend>
      <label><input type="radio" bind:group={xMode} value="density" /> density</label>
      <label><input type="radio" bind:group={xMode} value="per_vcpu" /> density per host vCPU</label>
    </fieldset>
  </div>

  <ul class="legend" aria-label="Runs">
    {#each lines as l (l.run)}
      <li>
        <a href={href({ name: 'run', campaign, run: l.run, density: null })}>
          <svg width="30" height="14" aria-hidden="true">
            <line x1="1" x2="29" y1="7" y2="7" stroke={color(l)} stroke-width="2" />
            <path d={marker(l.replica, 15, 7)} fill={color(l)} stroke="var(--bg)" stroke-width="1.5" />
          </svg>
          {runLabel(l)}</a
        >
      </li>
    {/each}
    <li class="key muted">
      <svg width="30" height="14" aria-hidden="true">
        <path d={marker(1, 8, 7)} fill="var(--ink-2)" />
        <path d={marker(1, 22, 7)} fill="var(--bg)" stroke="var(--ink-2)" stroke-width="2" />
      </svg>
      filled: the density passed; hollow: it failed
    </li>
  </ul>

  <div class="panels">
    {#each PANELS as panel, i (panel.key)}
      {@const w = widths[i]}
      {@const x = linear(xDomain, [M.left, Math.max(M.left + 1, w - M.right)])}
      {@const y = linear(yDomain(panel), [H - M.bottom, M.top])}
      {@const targets = [...new Set(lines.map((l) => panel.target(l)))]}
      <figure class="chart">
        <figcaption>{panel.title}, ms</figcaption>
        <div class="plot" bind:clientWidth={widths[i]}>
          <div
            class="focus"
            role="slider"
            tabindex="0"
            aria-label="{panel.title} against {xLabel.toLowerCase()}. Arrow keys step through the densities; the table below lists every value."
            aria-valuemin={0}
            aria-valuemax={Math.max(0, xs.length - 1)}
            aria-valuenow={hoverX === null ? 0 : Math.max(0, xs.findIndex((v) => sameX(v, hoverX!)))}
            aria-valuetext={hoverX === null ? 'no density selected' : readout(panel, hoverX)}
            onkeydown={(e) => key(i, e)}
            onfocus={() => {
              active = i;
              if (hoverX === null && xs.length) hoverX = xs[0];
            }}
            onblur={() => {
              hoverX = null;
              active = null;
            }}
          >
            {#if w > 0}
              <svg
                width={w}
                height={H}
                role="img"
                aria-label="{panel.title} against {xLabel.toLowerCase()}, one line per run"
                onpointermove={(e) => move(i, e)}
                onpointerleave={() => {
                  hoverX = null;
                  active = null;
                }}
              >
                {#each y.ticks(5) as t (t)}
                  <line class="grid" x1={M.left} x2={w - M.right} y1={y(t)} y2={y(t)} />
                  <text class="tick" x={M.left - 6} y={y(t)} dy="0.32em" text-anchor="end">{f.num(t)}</text>
                {/each}
                <line class="axis" x1={M.left} x2={w - M.right} y1={H - M.bottom} y2={H - M.bottom} />
                {#each x.ticks(Math.max(2, Math.floor(w / 70))) as t (t)}
                  <text class="tick" x={x(t)} y={H - M.bottom + 16} text-anchor="middle">{fmtX(t)}</text>
                {/each}
                <text class="label" x={(M.left + w - M.right) / 2} y={H - 4} text-anchor="middle">{xLabel}</text>

                {#each targets as t (t)}
                  <line class="target" x1={M.left} x2={w - M.right} y1={y(t)} y2={y(t)} />
                  <text class="target-label" x={w - M.right} y={y(t) - 5} text-anchor="end">target {f.num(t)}</text>
                {/each}

                {#if hoverX !== null}
                  <line class="crosshair" x1={x(hoverX)} x2={x(hoverX)} y1={M.top} y2={H - M.bottom} />
                {/if}

                {#each lines as l (l.run)}
                  {@const c = color(l)}
                  <path
                    d={linePath(l.points.map((p) => ({ x: x(xOf(l, p)), y: panel.point(p) === null ? null : y(panel.point(p)!) })))}
                    fill="none"
                    stroke={c}
                    stroke-width="2"
                    stroke-linejoin="round"
                    stroke-linecap="round"
                  />
                  {#each l.points as p (p.density)}
                    {#each p.trials as t (t.id)}
                      {#if panel.trial(t) !== null}
                        <circle cx={x(xOf(l, p))} cy={y(panel.trial(t)!)} r="2.5" fill={c} opacity="0.45" />
                      {/if}
                    {/each}
                  {/each}
                  {#each l.points as p (p.density)}
                    {@const v = panel.point(p)}
                    {#if v !== null}
                      {@const big = hoverX !== null && sameX(hoverX, xOf(l, p))}
                      <path
                        d={marker(l.replica, x(xOf(l, p)), y(v), big ? 6 : 4.5)}
                        fill={p.result === 'passed' ? c : 'var(--bg)'}
                        stroke={p.result === 'passed' ? 'var(--bg)' : c}
                        stroke-width={p.result === 'passed' ? 1.5 : 2}
                      />
                    {/if}
                  {/each}
                {/each}
              </svg>
            {/if}
          </div>
          {#if hoverX !== null && active === i && w > 0}
            {@const px = x(hoverX)}
            {@const tw = tipWidths[i]}
            <div
              class="tip"
              bind:offsetWidth={tipWidths[i]}
              style:left="{Math.max(0, px + 14 + tw > w ? px - 14 - tw : px + 14)}px"
              aria-hidden="true"
            >
              <div class="tip-head">
                {xMode === 'density' ? `Density ${fmtX(hoverX)}` : `${fmtX(hoverX)} per host vCPU`}
              </div>
              {#each at(hoverX) as { l, p } (l.run)}
                {@const v = panel.point(p)}
                {@const vals = p.trials.map((t) => panel.trial(t)).filter((q) => q !== null) as number[]}
                <div class="tip-row">
                  <svg width="14" height="10" aria-hidden="true"><line x1="0" x2="14" y1="5" y2="5" stroke={color(l)} stroke-width="2" /></svg>
                  <span class="tip-v">{v === null ? '–' : `${f.vs(v, panel.target(l))} ms`}</span>
                  <span class="tip-l"
                    >{[
                      `${runLabel(l)}${xMode === 'per_vcpu' ? `, density ${p.density}` : ''}`,
                      p.result,
                      panel.key === 'step' && p.step ? SUBJECT_LABEL[p.step] : null,
                      vals.length > 1 ? `${vals.length} trials ${f.range([Math.min(...vals), Math.max(...vals)])}` : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')}</span
                  >
                </div>
              {/each}
            </div>
          {/if}
        </div>
      </figure>
    {/each}
  </div>

  <details>
    <summary>Show as a table</summary>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Run</th>
            <th class="num">Density</th>
            <th class="num">Per host vCPU</th>
            <th>Result</th>
            <th class="num">Whole task p95, slowest trial</th>
            <th class="num">Slowest step's p50, slowest trial</th>
          </tr>
        </thead>
        <tbody>
          {#each lines as l (l.run)}
            {#each l.points as p (p.density)}
              <tr>
                <td><a href={href({ name: 'run', campaign, run: l.run, density: p.density })}>{l.run}</a></td>
                <td class="num">{p.density}</td>
                <td class="num">{f.ratio(p.perHostVcpu)}</td>
                <td class={p.result === 'passed' ? 'pass' : 'fail'}>{p.result === 'passed' ? '✓ passed' : '× failed'}</td>
                <td class="num">{p.taskP95 === null ? '–' : `${f.vs(p.taskP95, l.targets.task_p95_ms)} ms`}</td>
                <td class="num">
                  {p.stepP50 === null ? '–' : `${f.vs(p.stepP50, l.targets.step_p50_ms)} ms`}
                  {#if p.step}<span class="muted">({SUBJECT_LABEL[p.step]})</span>{/if}
                </td>
              </tr>
            {/each}
          {/each}
        </tbody>
      </table>
    </div>
  </details>
</div>

<style>
  .controls {
    margin: 8px 0;
  }
  fieldset {
    border: 0;
    padding: 0;
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 14px;
    align-items: center;
    font-size: 0.9rem;
  }
  legend {
    float: left;
    color: var(--ink-2);
    margin-right: 4px;
  }
  .legend {
    list-style: none;
    padding: 0;
    margin: 8px 0;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 18px;
    font-size: 0.88rem;
    max-width: none;
  }
  .legend a {
    color: var(--ink);
    text-decoration-color: var(--rule);
  }
  .legend svg {
    vertical-align: -2px;
    margin-right: 4px;
  }
  .panels {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 8px 24px;
  }
  .chart {
    margin: 0;
    min-width: 0;
  }
  figcaption {
    font-weight: 600;
    font-size: 0.92rem;
    margin: 6px 0 2px;
  }
  .plot {
    position: relative;
    width: 100%;
  }
  .focus {
    border-radius: 4px;
    min-height: 230px;
  }
  .focus:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .plot svg {
    display: block;
    overflow: visible;
  }
  .grid {
    stroke: var(--grid);
    stroke-width: 1;
  }
  .axis {
    stroke: var(--axis);
    stroke-width: 1;
  }
  .tick {
    fill: var(--muted);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  .label {
    fill: var(--ink-2);
    font-size: 12px;
  }
  .target {
    stroke: var(--ink-2);
    stroke-width: 1;
    stroke-dasharray: 5 4;
  }
  .target-label {
    fill: var(--ink-2);
    font-size: 11px;
    paint-order: stroke;
    stroke: var(--bg);
    stroke-width: 3px;
  }
  .crosshair {
    stroke: var(--ink-2);
    stroke-width: 1;
    opacity: 0.6;
  }
  .tip {
    position: absolute;
    top: 8px;
    z-index: 5;
    background: var(--bg);
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    box-shadow: 0 2px 10px rgb(0 0 0 / 0.12);
    padding: 8px 10px;
    font-size: 0.8rem;
    width: max-content;
    max-width: min(22rem, 100%);
    pointer-events: none;
  }
  .tip-head {
    font-weight: 600;
    margin-bottom: 4px;
  }
  .tip-row {
    display: grid;
    grid-template-columns: 14px auto 1fr;
    gap: 6px;
    align-items: baseline;
  }
  .tip-v {
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .tip-l {
    color: var(--ink-2);
  }
</style>
