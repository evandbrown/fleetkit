<script lang="ts">
  // One trial: its result against each criterion, every microVM's start and steps on one clock, what limited it over
  // time, the final screens, and (for the illustration) the filmstrip.
  import { loadCampaign, loadRun, loadTrial } from '../lib/data';
  import { trialSource } from '../lib/repo';
  import { href } from '../lib/router';
  import * as f from '../lib/format';
  import { coresStack, lanes, limitPanels, runTitle, trialSlos } from '../lib/shape';
  import { GUEST_GROUP_LABEL, HOST_CONSUMER_LABEL, NOT_JUDGED, RULE_LABEL, SUBJECT_LABEL, trialLabel, VERDICT_SHORT } from '../lib/glossary';
  import { HOST_CONSUMER_COLOR } from '../lib/colors';
  import { GUEST_GROUPS, HOST_CONSUMERS, STEP_NAMES } from '../lib/types';
  import Failed from '../components/Failed.svelte';
  import SyntheticBanner from '../components/SyntheticBanner.svelte';
  import LaneChart from '../components/LaneChart.svelte';
  import TimeChart from '../components/TimeChart.svelte';
  import ShareBar from '../components/ShareBar.svelte';
  import Screenshot from '../components/Screenshot.svelte';
  import SloBadges from '../components/SloBadges.svelte';
  import StepChart from '../components/StepChart.svelte';
  import Mark from '../components/Mark.svelte';
  import SourceLinks from '../components/SourceLinks.svelte';

  let { campaign, run, trial, microvm }: { campaign: string; run: string; trial: string; microvm: number | null } =
    $props();
  const data = $derived(Promise.all([loadCampaign(campaign), loadRun(campaign, run)]));
  const detail = $derived(loadTrial(campaign, run, trial));

  const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  const mib = (bytes: number) => (bytes >= 1024 * 1024 ? `${f.num(bytes / 1024 / 1024, 1)} MiB` : `${f.num(bytes / 1024)} KiB`);
</script>

{#await data}
  <p class="status">Loading…</p>
{:then [c, r]}
  {@const s = r.trials.find((t) => t.id === trial)}
  {@const spec = c.specs.find((x) => x.name === r.spec)!}
  {@const crit = spec.spec.criteria}
  {#if !s}
    <Failed error={new Error(`Run ${r.id} has no trial ${trial}.`)} />
  {:else}
    {@const microvmHref = (i: number) => href({ name: 'trial', campaign: c.id, run: r.id, trial: s.id, microvm: i })}
    {@const source = trialSource(c, r, s.id)}
    <p class="crumbs">
      <a href={href({ name: 'results', campaign: null })}>Results</a> ›
      <a href={href({ name: 'results', campaign: c.id })}>{c.title}</a> ›
      <a href={href({ name: 'run', campaign: c.id, run: r.id, density: null })}>{runTitle(c, r.id)}</a> ›
      <a href={href({ name: 'run', campaign: c.id, run: r.id, density: s.density })}>density {s.density}</a>
    </p>
    {#if r.synthetic}<SyntheticBanner />{/if}

    <h1>{capital(trialLabel(s))}</h1>
    {#if !s.counts}
      <p class="verdict label">{capital(NOT_JUDGED[s.role] ?? '')}.</p>
    {:else if s.passed}
      <p class="verdict pass"><Mark kind="pass" size={14} /> Passed</p>
    {:else}
      <p class="verdict fail"><Mark kind="fail" size={13} /> Failed: {s.failed.join('; ')}</p>
    {/if}

    <h2>Success criteria</h2>
    <SloBadges criteria={crit} results={trialSlos(s)} judged={s.counts} />

    {#if s.checks.length}
      <h2>Steps</h2>
      <StepChart checks={s.checks} criteria={crit} />
    {/if}

    {#await detail}
      <p class="status">Loading the trial…</p>
    {:then d}
      {@const L = lanes(d)}
      {@const domain = [0, Math.max(L.domain[1], d.window_ms[1])] as [number, number]}
      {@const sel = microvm === null ? null : d.microvms.find((m) => m.index === microvm) ?? null}
      {@const panels = limitPanels(d, s, c.rules)}
      {@const stack = coresStack(d, r.host.vcpus)}
      {@const anyFailed = d.microvms.some((m) => (m.task && !m.task.ok) || m.outcome !== 'completed')}
      <h2>MicroVMs</h2>
      <LaneChart lanes={L.lanes} {domain} marks={d.marks} selected={microvm} hrefFor={microvmHref} />

      {#if sel}
        {@const lim = d.limit.microvms.find((x) => x.index === sel.index)}
        <section class="panel selected" aria-labelledby="microvm-{sel.index}">
          <h3 id="microvm-{sel.index}">
            MicroVM {sel.index}
            <a class="close" href={href({ name: 'trial', campaign: c.id, run: r.id, trial: s.id, microvm: null })}>close</a>
          </h3>
          <div class="sel-grid">
            <table>
              <tbody>
                <tr>
                  <th scope="row">Task</th>
                  <td class="num {sel.task && !sel.task.ok ? 'fail' : ''}">
                    {sel.task ? (sel.task.ok ? f.ms(sel.task.task_ms) : `failed at ${sel.task.failed_step ? SUBJECT_LABEL[sel.task.failed_step] : 'a step'}`) : 'did not run'}
                  </td>
                </tr>
                <tr><th scope="row">Ready</th><td class="num">{sel.ready_ms !== null ? f.seconds(sel.ready_ms) : 'never'}</td></tr>
                {#if sel.boot}
                  <tr><th scope="row">Guest kernel</th><td class="num">{f.seconds(sel.boot.kernel_start_ms)}</td></tr>
                  <tr><th scope="row">Chromium ready</th><td class="num">{f.seconds(sel.boot.chromium_ready_ms)}</td></tr>
                {/if}
                {#each sel.steps as st (st.name)}
                  <tr>
                    <th scope="row">{capital(SUBJECT_LABEL[st.name])}</th>
                    <td class="num {st.end_ms - st.start_ms > crit.step_p50_target_ms ? 'fail' : ''}">{f.ms(st.end_ms - st.start_ms)}{st.ok ? '' : ', failed'}</td>
                  </tr>
                {/each}
                {#if sel.task}
                  <tr><th scope="row">Downloaded</th><td class="num">{mib(sel.task.bytes)} · {f.num(sel.task.requests)} requests</td></tr>
                  <tr><th scope="row">Chromium RSS</th><td class="num">{f.num(sel.task.chromium_rss_mib)} MiB</td></tr>
                {/if}
                {#if lim}
                  <tr><th scope="row">vCPU time</th><td class="num">{f.num(lim.vcpu_s, 2)} CPU-s</td></tr>
                  <tr><th scope="row">Memory peak</th><td class="num">{f.num(lim.mem_peak_mib)} MiB</td></tr>
                {/if}
              </tbody>
            </table>
            <Screenshot sha={sel.img ?? null} alt="The final screen of microVM {sel.index}'s task" caption="Final screen" />
          </div>
        </section>
      {/if}

      <details>
        <summary>All microVMs</summary>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="num">#</th>
                {#if anyFailed}<th>Outcome</th>{/if}
                <th class="num">Ready (s)</th>
                <th class="num">Task (ms)</th>
                {#each STEP_NAMES as n (n)}<th class="num">{capital(SUBJECT_LABEL[n])} (ms)</th>{/each}
                <th class="num">Memory (MiB)</th>
              </tr>
            </thead>
            <tbody>
              {#each d.microvms as m (m.index)}
                {@const lim = d.limit.microvms.find((x) => x.index === m.index)}
                <tr class:selected={microvm === m.index}>
                  <td class="num"><a href={microvmHref(m.index)}>{m.index}</a></td>
                  {#if anyFailed}<td class:fail={!!m.task && !m.task.ok}>{m.task && !m.task.ok ? `${m.task.failure_category} at ${m.task.failed_step}` : m.outcome === 'completed' ? '' : m.outcome}</td>{/if}
                  <td class="num">{m.ready_ms !== null ? f.num(m.ready_ms / 1000, 2) : 'never'}</td>
                  <td class="num">{m.task ? f.num(m.task.task_ms) : '–'}</td>
                  {#each STEP_NAMES as n (n)}
                    {@const st = m.steps.find((x) => x.name === n)}
                    <td class="num {st && st.end_ms - st.start_ms > crit.step_p50_target_ms ? 'fail' : ''}">{st ? f.num(st.end_ms - st.start_ms) : '–'}</td>
                  {/each}
                  <td class="num">{lim ? f.num(lim.mem_peak_mib) : '–'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </details>

      <h2>What limited it</h2>
      {#if s.attribution}
        {@const a = s.attribution}
        <p class="verdict-line">
          <strong>{a.verdicts.map((v) => VERDICT_SHORT[v]).join(', ')}</strong>
          {#if a.missing.length}<span class="muted">not recorded: {a.missing.map((k) => RULE_LABEL[k]).join(', ')}</span>{/if}
        </p>
        <div class="charts">
          {#each panels as p (p.key)}
            <TimeChart
              title={p.title}
              unit={p.unit}
              t={p.t}
              series={p.series.map((x) => ({ ...x, color: 'var(--series-1)' }))}
              many={p.many}
              {domain}
              window={a.window_ms}
              threshold={p.threshold ?? null}
              digits={p.unit === '' ? 2 : 1}
            />
          {/each}
          {#if stack}
            <TimeChart
              title="Host CPU"
              unit="vCPUs"
              t={[stack.t]}
              series={stack.layers.map((l) => ({ label: HOST_CONSUMER_LABEL[l.key], values: l.values, color: HOST_CONSUMER_COLOR[l.key] }))}
              stack
              {domain}
              window={a.window_ms}
              ceiling={{ value: r.host.vcpus, label: `${r.host.vcpus} vCPUs` }}
            />
          {/if}
        </div>
        <ul class="key">
          <li><span class="sw window"></span>tasks running</li>
          <li><span class="sw dash"></span>limit</li>
        </ul>
        <div class="shares">
          <ShareBar
            title="Host CPU by process"
            parts={HOST_CONSUMERS.map((k) => ({ label: HOST_CONSUMER_LABEL[k], color: HOST_CONSUMER_COLOR[k], share: a.host_cpu_s.busy ? a.host_cpu_s[k] / HOST_CONSUMERS.reduce((t, x) => t + a.host_cpu_s[x], 0) : 0, detail: `${f.num(a.host_cpu_s[k], 1)} CPU-s` }))}
          />
          {#if a.guest_share}
            <ShareBar title="Guest CPU by process" parts={GUEST_GROUPS.map((g) => ({ label: GUEST_GROUP_LABEL[g], share: a.guest_share![g] }))} />
          {/if}
        </div>
      {:else}
        <p class="muted">not recorded</p>
      {/if}

      <h2>Final screens</h2>
      {#if d.microvms.some((m) => m.img)}
        <div class="shots">
          {#each d.microvms as m (m.index)}
            <Screenshot sha={m.img ?? null} alt="The final screen of microVM {m.index}'s task" caption="microVM {m.index}" />
          {/each}
        </div>
      {:else}
        <p class="muted">not recorded</p>
      {/if}

      {#if d.filmstrip}
        <h2>Filmstrip</h2>
        <div class="shots film">
          {#each d.filmstrip.frames as fr, i (fr.step)}
            <Screenshot sha={fr.img} alt="After step {i + 1}, {SUBJECT_LABEL[fr.step]}" caption="{i + 1}. {capital(SUBJECT_LABEL[fr.step])}" />
          {/each}
        </div>
      {/if}
    {:catch e}
      <Failed error={e} />
    {/await}

    {#if source.length}<div class="next"><SourceLinks links={source} /></div>{/if}
  {/if}
{:catch e}
  <Failed error={e} />
{/await}

<style>
  .next {
    margin: 40px 0 0;
    padding-top: 16px;
    border-top: 1px solid var(--rule);
  }
  .verdict {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 1.15rem;
    font-weight: 600;
    margin: 4px 0 0;
  }
  .label {
    color: var(--ink-2);
  }
  tr.selected td {
    background: var(--highlight);
  }
  .selected h3 {
    margin-top: 0;
    display: flex;
    gap: 12px;
    align-items: baseline;
  }
  .close {
    font-size: 0.85rem;
    font-weight: 400;
  }
  .sel-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr));
    gap: 16px;
  }
  .sel-grid table {
    min-width: 0;
  }
  .sel-grid th {
    font-weight: 400;
    white-space: normal;
  }
  .verdict-line {
    font-size: 1.15rem;
    margin: 0 0 8px;
  }
  .verdict-line .muted {
    font-size: 0.88rem;
    margin-left: 8px;
  }
  .charts {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr));
    gap: 16px 32px;
  }
  .shares {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr));
    gap: 8px 32px;
  }
  .key {
    list-style: none;
    padding: 0;
    margin: 4px 0 8px;
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
  .sw.window {
    background: var(--surface-2);
  }
  .sw.dash {
    height: 0;
    border-top: 2px dashed var(--ink-2);
    border-radius: 0;
    vertical-align: 3px;
  }
  .shots {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
    gap: 10px;
    margin: 12px 0;
  }
  .shots.film {
    grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
  }
</style>
