<script lang="ts">
  // The home page (D72): what Fleetkit is, how it works, and a way into the results; then the setup, the task, the
  // SLOs, how we test and what we measure. Mostly pictures; the SLOs are the method's standard ones (the input schema's
  // defaults), the spec comes from the featured campaign and the filmstrip from its illustration trial, and the
  // featured result names any SLO of its own (D74). The diagrams are illustrations. The intro's wording is D92,
  // pending Evan's approval; the featured card and "What we measure" follow site-brief-v3 (D93). The spec is the
  // same table Results shows (SpecTable), for the featured campaign's first spec.
  import { imgUrl, loadCampaign, loadIndex, loadTrial } from '../lib/data';
  import { href } from '../lib/router';
  import { campaignHeadline, campaignRefs, sloTag } from '../lib/shape';
  import { STANDARD_CRITERIA } from '../lib/spec';
  import { STEP_NAMES, type CampaignDoc, type CampaignEntry, type StepName, type TrialDoc } from '../lib/types';
  import Architecture from '../components/about/Architecture.svelte';
  import Units from '../components/about/Units.svelte';
  import SloBadges from '../components/SloBadges.svelte';
  import SpecTable from '../components/SpecTable.svelte';
  import Mark from '../components/Mark.svelte';

  let entry = $state<CampaignEntry | null>(null);
  let campaign = $state<CampaignDoc | null>(null);
  let film = $state<NonNullable<TrialDoc['filmstrip']> | null>(null);
  let filmFull = $state(false); // the illustration has full-size screenshots (DATA.md, rule 10)
  let broken = $state<Partial<Record<StepName, boolean>>>({});

  (async () => {
    try {
      const index = await loadIndex();
      const id = index.featured ?? index.campaigns[0]?.id;
      if (!id) return;
      entry = index.campaigns.find((e) => e.id === id) ?? null;
      const c = await loadCampaign(id);
      campaign = c;
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

  /** The first sentence of an answer: a full stop, then a space, then a capital ("$0.019" and "1,000" hold none). */
  const firstSentence = (text: string) => text.split(/(?<=[.!?])\s+(?=[A-Z])/)[0];

  // The featured campaign's result: the spec the catalog names (else its best by midpoint) and its density ("≥" when
  // no replica reached a failure), density per host vCPU, the host it ran on, how its SLOs differ from the standard if
  // they do, and the first sentence of the catalog's answer, which carries the context; the campaign's question is
  // not on the card. Null until something has run and passed; the card then shows only its label and the button.
  const featured = $derived.by(() => {
    if (!entry) return null;
    const h = campaignHeadline(entry, entry.featured_spec ?? null);
    if (h.density === null) return null;
    const reps = entry.outcomes.find((o) => o.spec === h.spec)?.replicas ?? [];
    const noFailure = reps.length > 0 && reps.every((r) => r.first_failed === null);
    const density = noFailure && !h.density.startsWith('≥') ? `≥ ${h.density}` : h.density;
    const s = campaign?.specs.find((x) => x.name === h.spec) ?? null;
    return {
      title: entry.title,
      density,
      label: h.label,
      perVcpu: h.perVcpu,
      host: s?.host.instance_type ?? null,
      slo: s ? sloTag([s.spec.criteria]) : null,
      answer: entry.answer ? firstSentence(entry.answer) : null,
    };
  });

  const FLOW = [
    { name: 'Define', text: 'A campaign: the specs to compare, and replicas of each', link: { href: href({ name: 'builder', from: null }), text: 'Define your own in the Builder' } },
    { name: 'Launch', text: 'Real EC2 hosts, headless Chrome in a microVM per browser' },
    { name: 'Measure', text: 'A real five-step shopping task, at rising density' },
    { name: 'Publish', text: 'The highest density that met every SLO, and its cost' },
  ];

  // The six measures, worded as in site-brief-v3.
  const MEASURES = [
    { name: 'Latency', text: 'How long each step and the whole task took: the median (p50) and the slowest 5% (p95).' },
    { name: 'Time to ready', text: 'From starting a microVM to its browser answering, for every microVM.' },
    { name: 'Host pressure', text: 'CPU, memory and IO on the host, sampled 5 times a second, and which ran out first.' },
    { name: 'Cost', text: 'Dollars per 1,000 tasks at on-demand prices: what a full fleet pays per task, boot included; the cost of one burst beside it.' },
    { name: 'Max density', text: 'The most microVMs that ran at once with every SLO met. Also per host vCPU, so hosts of different sizes compare.' },
    { name: 'Midpoint', text: 'The middle of the gap between the last density that passed and the first that failed, per host vCPU. Specs are ranked by it.' },
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
    </div>
    <aside class="featured" aria-label="Featured result">
      <p class="label">Featured result</p>
      {#if featured}
        <p class="title">{featured.title}</p>
        <p class="figure">
          <strong>{featured.density}</strong>
          <span>{featured.label}{' on one '}<span class="host">{featured.host ?? 'host'}</span></span>
        </p>
        {#if featured.perVcpu !== null}<p class="per">{featured.perVcpu} microVMs per host vCPU</p>{/if}
        <!-- A green check before "SLOs" (read aloud as "every SLO met"): the charts' filled circle would read as a bullet here. -->
        <p class="slos">
          <svg class="tick" aria-hidden="true" viewBox="0 0 16 16" width="14" height="14">
            <path d="M3 8.5l3.2 3.2L13 5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <span class="vh">every SLO met</span><span aria-hidden="true">SLOs</span>
          {#if featured.slo}<span class="slo-tag">{featured.slo}</span>{/if}
        </p>
        {#if featured.answer}<p class="answer">{featured.answer}</p>{/if}
      {/if}
      <a class="button primary" href={href({ name: 'results', campaign: null })}>See the results <span aria-hidden="true">→</span></a>
    </aside>
  </header>

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
          <li>Test densities in order, lowest first.</li>
          <li>Stop at the first density that fails.</li>
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
    min-height: 168px;
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
  /* The unit stays beside the number where it fits ("6 microVMs on one c8i.xlarge") and takes its own line under it
     where it doesn't, never a hanging second line under the first word. */
  .featured .figure {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0 8px;
    font-weight: 600;
  }
  /* "≥ 200" and "6–7" stay on one line */
  .featured .figure strong {
    font-size: 2.75rem;
    line-height: 1.05;
    font-weight: 750;
    letter-spacing: -0.03em;
    color: var(--accent-ink);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  /* An instance type never breaks at its hyphen (m8i.metal-48xl). */
  .featured .figure .host {
    white-space: nowrap;
  }
  .featured .title {
    font-weight: 600;
    margin-bottom: 4px;
  }
  .featured .per {
    font-size: 0.88rem;
    color: var(--ink-2);
    font-variant-numeric: tabular-nums;
  }
  /* the pass mark, then "SLOs" (read aloud as "every SLO met"), then the tag when the SLOs aren't standard */
  .featured .slos {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 6px;
    font-size: 0.88rem;
    font-weight: 600;
  }
  .tick {
    flex: none;
    color: var(--good);
  }
  .featured .answer {
    margin-top: 6px;
    font-size: 0.95rem;
    line-height: 1.45;
    color: var(--ink);
  }
  .featured .button {
    margin-top: 10px;
  }
  /* read by screen readers, not shown */
  .vh {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }

  /* how it works: four steps, left to right */
  .flow {
    list-style: none;
    margin: 32px 0 0;
    padding: 0;
    max-width: none;
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 20px;
  }
  .flow li {
    position: relative;
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
  .flow li:not(:last-child)::after {
    content: '';
    position: absolute;
    top: 50%;
    right: -15px;
    width: 8px;
    height: 8px;
    border-top: 2px solid var(--axis);
    border-right: 2px solid var(--axis);
    transform: translateY(-50%) rotate(45deg);
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
  @media (max-width: 820px) {
    .flow {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .flow li:not(:last-child)::after {
      display: none;
    }
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
