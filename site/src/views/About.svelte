<script lang="ts">
  // The home page (D72): what Fleetkit is, how it works, and a way into the results; then the setup, the task, the
  // SLOs, how we test and what we measure. Mostly pictures; the SLOs are the method's standard ones (the input schema's
  // defaults), the spec comes from the featured campaign and the filmstrip from its illustration trial. The featured
  // card leads with the topline figure (D102): the featured spec's steady-state cost per 1,000 tasks, the browsers
  // on one host it came from, and the Headroom chart; the four-step flow sits under the intro beside it, so the hero
  // is one block. The diagrams are illustrations. The intro's wording is D92,
  // pending Evan's approval; "What we measure" follows site-brief-v3 (D93). The spec is the same table Results
  // shows (SpecTable), for the featured campaign's first spec.
  import { costNote, costText, specCost } from '../lib/cost';
  import { imgUrl, loadCampaign, loadIndex, loadRun, loadTrial } from '../lib/data';
  import { browsers } from '../lib/glossary';
  import { href } from '../lib/router';
  import { campaignHeadline, campaignRefs } from '../lib/shape';
  import { STANDARD_CRITERIA } from '../lib/spec';
  import { STEP_NAMES, type CampaignDoc, type CampaignEntry, type RunDoc, type StepName, type TrialDoc } from '../lib/types';
  import Architecture from '../components/about/Architecture.svelte';
  import Headroom from '../components/about/Headroom.svelte';
  import Units from '../components/about/Units.svelte';
  import SloBadges from '../components/SloBadges.svelte';
  import SpecTable from '../components/SpecTable.svelte';
  import Mark from '../components/Mark.svelte';

  let entry = $state<CampaignEntry | null>(null);
  let campaign = $state<CampaignDoc | null>(null);
  let film = $state<NonNullable<TrialDoc['filmstrip']> | null>(null);
  /** The featured spec's first replica, for the card's chart. */
  let featuredRun = $state<RunDoc | null>(null);
  let filmFull = $state(false); // the illustration has full-size screenshots (DATA.md, rule 10)
  let broken = $state<Partial<Record<StepName, boolean>>>({});

  (async () => {
    try {
      const index = await loadIndex();
      const id = index.featured ?? index.campaigns[0]?.id;
      if (!id) return;
      const e = index.campaigns.find((x) => x.id === id) ?? null;
      entry = e;
      const c = await loadCampaign(id);
      campaign = c;
      const spec = e ? campaignHeadline(e, e.featured_spec ?? null).spec : null;
      const first = c.runs.find((r) => r.spec === spec);
      if (first) loadRun(c.id, first.id).then((r) => (featuredRun = r)).catch(() => null);
      for (const r of c.runs.slice(0, 3)) {
        const t = await loadTrial(c.id, r.id, 'illustration').catch(() => null);
        if (t?.filmstrip) {
          film = t.filmstrip;
          filmFull = t.full_size_screenshots;
          break;
        }
      }
    } catch {
      // No dataset: the page still explains everything; only the example numbers and screenshots are missing.
    }
  })();

  const spec = $derived(campaign?.specs[0] ?? null);
  const run = $derived(campaign?.runs.find((r) => r.spec === spec?.name) ?? null);

  const STEP_LABEL: Record<StepName, string> = {
    home: 'Home',
    search: 'Search',
    open_product: 'Open product',
    add_to_cart: 'Add to cart',
    verify_cart: 'Verify cart',
  };
  const frames = $derived(STEP_NAMES.map((step) => ({ step, img: film?.frames.find((f) => f.step === step)?.img ?? null })));

  const chromium = $derived(run?.host.chromium_version.split('.')[0] ?? null);

  // The featured campaign's result: the spec the catalog names (else its best by midpoint), its steady-state cost per
  // 1,000 tasks (cost.ts: the median of the replicas' range middles), the browsers per host most replicas held ("≥"
  // when no replica reached a failure), the host, and one replica's closest-SLO chart (Headroom). No question, no
  // answer, no SLO note, no vCPU count and no per-vCPU figure: the card is a glance, Results has the rest. With no
  // steady-state cost (no host was ever full) the count takes the big slot and a line says why. Null until something
  // has run and passed; the card then shows only its label and the button.
  const featured = $derived.by(() => {
    if (!entry) return null;
    const h = campaignHeadline(entry, entry.featured_spec ?? null);
    if (!h.spec) return null;
    const sc = specCost(entry, h.spec);
    if (sc.count === null) return null;
    const s = campaign?.specs.find((x) => x.name === h.spec) ?? null;
    return {
      title: entry.title,
      cost: sc.cost,
      note: costNote(sc),
      count: sc.count,
      host: s?.host.instance_type ?? null,
      densities: s?.spec.densities ?? [],
    };
  });

  const FLOW = [
    { name: 'Define', text: 'A campaign: the specs to compare, and replicas of each', link: { href: href({ name: 'builder', from: null }), text: 'Define your own in the Builder' } },
    { name: 'Launch', text: 'Real EC2 hosts, headless Chrome in a microVM per browser' },
    { name: 'Measure', text: 'A real five-step shopping task, with more browsers per host each trial' },
    { name: 'Publish', text: 'The most browsers per host that met every SLO, and their cost' },
  ];

  // The six measures, worded as in site-brief-v3.
  const MEASURES = [
    { name: 'Latency', text: 'How long each step and the whole task took: the median (p50) and the slowest 5% (p95).' },
    { name: 'Time to ready', text: 'From starting a microVM to its browser answering, for every microVM.' },
    { name: 'Host pressure', text: 'CPU, memory and IO on the host, sampled 5 times a second, and which ran out first.' },
    { name: 'Cost', text: 'Dollars per 1,000 tasks at on-demand prices: what a full fleet pays per task, boot included; the cost of one burst beside it.' },
    { name: 'Most browsers', text: 'The most browsers one host ran at once with every SLO met; each browser runs in its own microVM. Also per host vCPU, so hosts of different sizes compare.' },
    { name: 'Midpoint', text: 'The middle of the gap between the last browser count that passed and the first that failed, per host vCPU. Specs are ranked by it.' },
  ];
</script>

<article class="about">
  <header class="hero">
    <div class="intro">
      <h1>What is Fleetkit?</h1>
      <!-- The intro's wording is D92, pending Evan's approval. -->
      <p class="lead">
        A benchmark framework for the browser fleet behind an agentic product: many headless Chrome browsers per cloud
        host, each in its own microVM, each driven by a test harness through the same five-step task.
      </p>
      <p class="lead-2">
        It finds how many browsers one host can run while every task stays inside its SLOs, and what each task costs.
      </p>
      <ol class="flow" aria-label="How Fleetkit works">
        {#each FLOW as step, i (step.name)}
          <li>
            <span class="n" aria-hidden="true">{i + 1}</span>
            <strong>{step.name}</strong>
            <span class="t">{step.text}</span>
            {#if 'link' in step && step.link}<a class="t more" href={step.link.href}>{step.link.text} <span aria-hidden="true">→</span></a>{/if}
          </li>
        {/each}
      </ol>
    </div>
    <aside class="featured" aria-label="Featured result">
      <p class="label">Featured result</p>
      {#if featured}
        <p class="title">{featured.title}</p>
        {#if featured.cost !== null}
          <!-- The one number: the ≈ (small, base.css's .approx) says estimate; the full ranges are in its title. -->
          <p class="big" title={featured.note ?? undefined}><span class="approx">≈</span>{' '}<span class="n">{costText(featured.cost)}</span></p>
          <p class="unit">per 1,000 tasks</p>
          <p class="detail">{browsers(featured.count)} on one <strong class="host">{featured.host ?? 'host'}</strong></p>
        {:else}
          <p class="big"><span class="n">{featured.count}</span></p>
          <p class="unit">browsers per host</p>
          <p class="detail">on one <strong class="host">{featured.host ?? 'host'}</strong></p>
          <p class="note">No fleet cost: the host was never full</p>
        {/if}
        {#if featuredRun && featured.densities.length}<Headroom run={featuredRun} densities={featured.densities} />{/if}
      {/if}
      <a class="button primary" href={href({ name: 'results', campaign: null })}>See the results <span aria-hidden="true">→</span></a>
    </aside>
  </header>

  <section aria-labelledby="architecture">
    <h2 id="architecture">Architecture</h2>
    <Architecture />
    <ol class="parts">
      <li><strong>Host</strong> EC2, nested (inside a VM) or metal (bare hardware)</li>
      <li><strong>MicroVM</strong> One small KVM virtual machine per browser</li>
      <li><strong>OS</strong> Debian guest, read-only root, writes in memory</li>
      <li><strong>Daemons</strong> Host daemon runs the microVMs; guest daemon drives Chromium and times each step</li>
      <li><strong>Chrome</strong> Headless Chromium{chromium ? ` ${chromium}` : ''}, one task per microVM</li>
    </ol>
  </section>

  <section aria-labelledby="task">
    <h2 id="task">The task</h2>
    <!-- The public copy of the fixture (evan.mx/fleetkit-fixture): the one link off the site besides GitHub. -->
    <p class="sub">Five steps on <a href="https://evan.mx/fleetkit-fixture/" target="_blank" rel="noopener">the test shopping site</a>, the same in every browser.</p>
    <ol class="film">
      {#each frames as fr, i (fr.step)}
        <li style:--step="var(--step-{fr.step.replaceAll('_', '-')})">
          {#if fr.img && !broken[fr.step]}
            {#snippet thumb(img: string, step: StepName)}
              <img
                src={imgUrl(img, 't')}
                alt="{STEP_LABEL[step]}: the page after this step"
                width="320"
                height="178"
                loading="lazy"
                onerror={() => (broken = { ...broken, [step]: true })}
              />
            {/snippet}
            {#if filmFull}
              <a href={imgUrl(fr.img, 'f')} target="_blank" rel="noopener">{@render thumb(fr.img, fr.step)}</a>
            {:else}
              {@render thumb(fr.img, fr.step)}
            {/if}
          {:else}
            <div class="blank" aria-hidden="true"></div>
          {/if}
          <p class="step"><span class="n">{i + 1}</span>{STEP_LABEL[fr.step]}</p>
        </li>
      {/each}
    </ol>
  </section>

  <section aria-labelledby="slos">
    <h2 id="slos">SLOs</h2>
    <!-- The design's SLOs, read from the schema's defaults. Campaigns run before they changed carry a tag on Results. -->
    <p class="sub">The design's targets. Every campaign published so far was judged by targets of its own, tighter for most (step p50 ≤ 1 s, step p95 ≤ 2 s, task p95 ≤ 5 s); Results says so beside each.</p>
    <SloBadges criteria={STANDARD_CRITERIA} />
  </section>

  <section aria-labelledby="testing">
    <h2 id="testing">How we test</h2>
    <div class="units">
      <Units />
      <p class="legend">
        <span class="key"><Mark kind="pass" size={11} />passed</span>
        <span class="key"><Mark kind="fail" size={10} />failed</span>
        <span class="key"><Mark kind="untested" size={10} />not tested</span>
        <span class="key"><i class="sq"></i>one microVM</span>
      </p>
    </div>

    <div class="how">
      <div>
        <h3>The spec</h3>
        <p class="sub">Everything fixed about one run. A campaign's specs differ in one or two of these rows.</p>
        {#if campaign && spec}
          <SpecTable refs={campaignRefs(campaign).slice(0, 1)} replicas={campaign.definition.replicas} support />
        {/if}
      </div>
      <div>
        <h3>The procedure</h3>
        <ol class="steps">
          <li>Test the browser counts in order, lowest first.</li>
          <li>Stop at the first count that fails.</li>
          <li>Run extra trials at the last pass and the first failure.</li>
        </ol>
      </div>
    </div>
  </section>

  <section aria-labelledby="measure">
    <h2 id="measure">What we measure</h2>
    <dl class="measures">
      {#each MEASURES as m (m.name)}
        <div>
          <dt>{m.name}</dt>
          <dd>{m.text}</dd>
        </div>
      {/each}
    </dl>
  </section>

</article>

<style>
  .about {
    padding-bottom: 24px;
  }
  /* what Fleetkit is, and the way into the results */
  /* The heading lines up with the card's top, whatever the card's height. */
  .hero {
    padding: 8px 0 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 300px;
    align-items: start;
    gap: 24px 48px;
  }
  .intro {
    padding-top: 8px;
  }
  @media (max-width: 820px) {
    .hero {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  .hero .lead {
    margin: 0;
    max-width: 58ch;
  }
  .lead-2 {
    margin: 10px 0 0;
    max-width: 58ch;
    color: var(--ink-2);
  }
  .flow a.more {
    grid-column: 2;
    margin-top: 4px;
    font-weight: 600;
    color: var(--accent-ink);
  }
  .featured {
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    align-items: flex-start;
    gap: 4px;
    padding: 16px 18px 18px;
    border: 1px solid var(--rule);
    border-top: 3px solid var(--accent);
    border-radius: var(--radius);
    background: var(--highlight);
  }
  .featured p {
    margin: 0;
  }
  .featured .label {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--ink-2);
  }
  .featured .title {
    font-weight: 600;
    margin-bottom: 4px;
  }
  /* The number: the cost large in the accent, tabular, its unit on the line under it; the ≈ is base.css's .approx. */
  .featured .big {
    font-size: 2.75rem;
    line-height: 1.05;
    white-space: nowrap;
  }
  .featured .n {
    font-size: 2.75rem;
    font-weight: 750;
    letter-spacing: -0.03em;
    color: var(--accent-ink);
    font-variant-numeric: tabular-nums;
  }
  .featured .unit {
    font-size: 0.92rem;
    font-weight: 600;
    margin-top: 2px;
  }
  /* Where the number came from: the browsers on one host, the host in ink; an instance type never breaks at its hyphen. */
  .featured .detail {
    margin-top: 6px;
    font-size: 0.9rem;
    color: var(--ink-2);
    font-variant-numeric: tabular-nums;
  }
  .featured .detail .host {
    white-space: nowrap;
    color: var(--ink);
    font-weight: 600;
  }
  /* Why there is no cost, when there isn't one. */
  .featured .note {
    margin-top: 4px;
    font-size: 0.8rem;
    color: var(--muted);
  }
  .featured .button {
    margin-top: 12px;
  }

  /* how it works: four steps, two by two under the intro, one column on a phone */
  .flow {
    list-style: none;
    margin: 24px 0 0;
    padding: 0;
    max-width: none;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .flow li {
    display: grid;
    grid-template-columns: 22px minmax(0, 1fr);
    align-content: start;
    gap: 2px 10px;
    padding: 14px 16px;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--surface);
    font-size: 0.9rem;
    line-height: 1.45;
  }
  .flow .n {
    grid-row: span 2;
    display: inline-grid;
    place-items: center;
    width: 22px;
    height: 22px;
    margin-top: 1px;
    border-radius: 50%;
    background: var(--accent);
    color: #fff;
    font-size: 0.75rem;
    font-weight: 700;
  }
  .flow strong {
    font-size: 1rem;
    font-weight: 650;
  }
  .flow .t {
    color: var(--ink-2);
  }
  @media (max-width: 420px) {
    .flow {
      grid-template-columns: minmax(0, 1fr);
      gap: 8px;
    }
    .flow li {
      padding: 10px 14px;
    }
  }
  .sub {
    margin: -4px 0 16px;
    color: var(--ink-2);
  }

  /* the components, numbered as in the diagram */
  .parts {
    list-style: none;
    counter-reset: part;
    margin: 20px 0 0;
    padding: 0;
    max-width: none;
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 16px 24px;
  }
  @media (max-width: 900px) {
    .parts {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  @media (max-width: 480px) {
    .parts {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  .parts li {
    counter-increment: part;
    border-top: 1px solid var(--rule);
    padding-top: 10px;
    font-size: 0.92rem;
    color: var(--ink-2);
    line-height: 1.5;
  }
  .parts strong {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--ink);
    font-weight: 650;
    margin-bottom: 2px;
  }
  .parts strong::before {
    content: counter(part);
    display: inline-grid;
    place-items: center;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: var(--ink);
    color: var(--bg);
    font-size: 0.7rem;
    font-weight: 700;
  }

  /* the filmstrip */
  .film {
    list-style: none;
    margin: 0;
    padding: 0;
    max-width: none;
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 12px;
  }
  @media (max-width: 720px) {
    .film {
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }
  }
  @media (max-width: 480px) {
    .film {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  .film li {
    min-width: 0;
  }
  .film img,
  .film .blank {
    display: block;
    width: 100%;
    height: auto;
    aspect-ratio: 320 / 178;
    object-fit: cover;
    object-position: top;
    border: 1px solid var(--rule);
    border-top: 3px solid var(--step);
    border-radius: 6px;
    background: var(--surface);
    box-shadow: var(--shadow);
  }
  .film a {
    display: block;
  }
  .film a:hover img {
    border-color: var(--ink-2);
    border-top-color: var(--step);
  }
  .step {
    margin: 8px 0 0;
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.9rem;
    font-weight: 600;
  }
  .n {
    display: inline-grid;
    place-items: center;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: var(--step);
    color: #fff;
    font-size: 0.72rem;
    font-weight: 700;
    flex: none;
  }

  /* how we test */
  .units {
    margin: 8px 0 0;
  }
  .legend {
    margin: 12px 0 0;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 16px;
    font-size: 0.85rem;
    color: var(--ink-2);
  }
  .key {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .sq {
    width: 8px;
    height: 8px;
    border-radius: 1.5px;
    background: var(--ordinal-2);
  }
  .how {
    margin-top: 32px;
    display: grid;
    grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
    gap: 0 48px;
  }
  @media (max-width: 760px) {
    .how {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  .how h3 {
    margin-top: 16px;
  }
  .steps {
    list-style: none;
    counter-reset: step;
    margin: 0;
    padding: 0;
  }
  .steps li {
    counter-increment: step;
    display: grid;
    grid-template-columns: 32px minmax(0, 1fr);
    align-items: baseline;
    padding: 10px 0;
    border-top: 1px solid var(--rule);
  }
  .steps li:first-child {
    border-top: 0;
    padding-top: 4px;
  }
  .steps li::before {
    content: counter(step) '.';
    font-weight: 700;
    color: var(--ink-2);
    font-variant-numeric: tabular-nums;
  }

  /* what we measure */
  .measures {
    margin: 0;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px 24px;
  }
  @media (max-width: 760px) {
    .measures {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  @media (max-width: 480px) {
    .measures {
      grid-template-columns: minmax(0, 1fr);
      gap: 0;
    }
    .measures div {
      display: grid;
      grid-template-columns: 7.5rem minmax(0, 1fr);
      gap: 12px;
      padding: 8px 0;
    }
  }
  .measures div {
    border-top: 1px solid var(--rule);
    padding-top: 10px;
  }
  .measures dt {
    font-weight: 650;
    margin-bottom: 2px;
  }
  .measures dd {
    margin: 0;
    font-size: 0.9rem;
    color: var(--ink-2);
    line-height: 1.5;
  }

</style>
