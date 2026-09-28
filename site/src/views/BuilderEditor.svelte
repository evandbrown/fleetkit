<script lang="ts">
  // The experiment builder, loaded on its own when the page opens (Builder.svelte): define a campaign (a question, a base spec, named specs written as their changes from
  // the base, and replicas), see what it runs and costs, and copy the definition into Claude Code. It always starts
  // from something real: a campaign in experiments/campaigns/, a published campaign or run a link names
  // (?from=campaign:<c> or run:<c>/<run>), or pasted JSON. The draft is kept in this browser. The checks come from
  // lib/campaign.ts, which matches expand.py.
  import { tick, untrack } from 'svelte';
  import { nav } from '../lib/nav.svelte';
  import {
    CAMPAIGN_FIELDS,
    SPEC_FIELDS,
    TYPES,
    defaults,
    deletePath,
    evaluate,
    freeName,
    getPath,
    isObj,
    merge,
    same,
    scaleDensities,
    serialize,
    setPath,
    suggestName,
    usd,
    valueIn,
    type Field as FieldInfo,
    type Json,
    type Obj,
  } from '../lib/campaign';
  import {
    DEFAULT_START,
    STARTS,
    at,
    bundled,
    estimateOf,
    fromLink,
    fromStart,
    hostFacts,
    interpret,
    loadDraft,
    messages,
    nextName,
    saveDraft,
    type CompareOptions,
    START_HELP,
    type Draft,
    type Msg,
  } from '../components/builder/draft';
  import Field from '../components/builder/Field.svelte';
  import SpecCard from '../components/builder/SpecCard.svelte';
  import Compare from '../components/builder/Compare.svelte';
  import Review from '../components/builder/Review.svelte';
  import Help from '../components/builder/Help.svelte';

  // ---------------------------------------------------------------- the draft
  // A link's ?from= wins; a bundled campaign loads at once, a published one once it's fetched (the effect below).
  // Otherwise the draft kept in this browser, otherwise the first campaign.
  const linked = () => (nav.route.name === 'builder' ? nav.route.from : null);
  const quick = bundled(linked());
  const init = quick ?? loadDraft() ?? fromStart(DEFAULT_START);
  let handled = quick ? linked() : null;
  let def: Obj = $state(init.def);
  let label = $state(init.label);
  let source: Obj | null = $state.raw(init.source);
  let comparing: string | null = $state(null);
  let showAll = $state(false);
  let showTimer = $state(false);
  let loading: string | null = $state(null);

  /** A plain copy of builder state (typed loosely: Svelte's Snapshot type recurses too deep on Json). */
  const plain = (x: unknown) => $state.snapshot(x) as Obj;
  const snap = $derived(plain(def));
  const result = $derived(evaluate(snap));
  const est = $derived(estimateOf(snap, result));
  const msgs = $derived(messages(result));
  const text = $derived(serialize(snap));
  const base = $derived(isObj(snap.base) ? snap.base : {});
  const specs = $derived(isObj(snap.specs) ? snap.specs : {});
  const names = $derived(Object.keys(specs));
  const resolved = $derived(names.map((n): [string, Obj] => [n, isObj(specs[n]) ? merge(base, specs[n] as Obj) : base]));
  const edited = $derived(!!source && !same(snap, source));
  const why = $derived(isObj(snap.why) ? snap.why : {});

  $effect(() => saveDraft({ def: snap, label, source }));

  $effect(() => {
    const from = linked();
    if (!from || from === handled) return;
    handled = from;
    loading = from;
    fromLink(from)
      .then((d) => untrack(() => handled === from && load(d, `Loaded ${d.label}.`)))
      .catch((e: Error) => untrack(() => say(`Couldn't open ${from}: ${e.message}.`, null)))
      .finally(() => (loading = null));
  });

  // A link opens a definition as it is, so it can be shared; once edited, the address drops it, and a reload
  // brings back the edited draft.
  $effect(() => {
    if (edited && location.hash.includes('?')) history.replaceState(null, '', '#/builder');
  });

  // ---------------------------------------------------------------- what's shown
  const shown = (f: FieldInfo) =>
    showAll ||
    f.tier === 'basic' ||
    comparing === f.path ||
    !same(valueIn(base, f), f.schema.default) ||
    at(msgs, `base.${f.path}`).length > 0;
  const baseFields = $derived(SPEC_FIELDS.filter(shown));
  const hiddenCount = $derived(SPEC_FIELDS.length - baseFields.length);
  const TIMER = CAMPAIGN_FIELDS.shutdown_after_minutes;
  const timerShown = $derived(
    showAll || showTimer || !same(snap.shutdown_after_minutes, TIMER.schema.default) || at(msgs, TIMER.path).length > 0,
  );
  const fieldId = (path: string) => `b-${path.replace(/[._]/g, '-')}`;

  // A campaign file's name on a changed definition: the launcher would find the file already there.
  const clash = $derived.by(() => {
    const s = STARTS.find((x) => x.key === snap.name);
    return s && !same(snap, s.def) ? nextName(s.key, STARTS.map((x) => x.key)) : null;
  });

  // Messages with no input to sit beside, such as a field pasted JSON has and the schema doesn't.
  const claimed = (m: Msg) =>
    ['name', 'question', 'replicas', TIMER.path].includes(m.path) ||
    SPEC_FIELDS.some((f) => at([m], `base.${f.path}`).length) ||
    names.some((n) => m.path === `specs.${n}` || m.path.startsWith(`specs.${n}.`) || m.path === `why.${n}`);
  const others = $derived(
    msgs
      .filter((m) => !claimed(m))
      .map((msg) => {
        if (msg.text === 'not a field' || msg.text.startsWith('no spec named')) {
          return { msg, fix: { label: 'Remove', run: () => deletePath(def, msg.path) } };
        }
        const section = /^base(?:\.([a-z_]+))?$/.exec(msg.path);
        if (msg.text === 'required' && section) {
          return {
            msg,
            fix: {
              label: 'Use defaults',
              run: () => (section[1] ? setPath(def, msg.path, defaults(section[1])) : (def.base = defaults())),
            },
          };
        }
        return { msg };
      }),
  );

  function baseFacts(path: string): string {
    const v = getPath(base, path);
    if (path.endsWith('instance_type')) return hostFacts(v);
    if (path === 'microvm.memory_mib' && typeof v === 'number') return `${+(v / 1024).toFixed(2)} GiB`;
    return '';
  }

  // ---------------------------------------------------------------- undo, for the changes that replace a lot
  type Saved = { def: Obj; label: string; source: Obj | null };
  let toast: { text: string; undo: Saved | null } | null = $state.raw(null);
  let toastTimer: ReturnType<typeof setTimeout> | undefined;
  const saved = (): Saved => ({ def: plain(def), label, source });
  function say(text: string, undo: Saved | null) {
    toast = { text, undo };
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast = null), 8000);
  }
  function undo() {
    if (!toast?.undo) return;
    ({ def, label, source } = toast.undo);
    toast = null;
  }

  function load(d: Draft, what: string) {
    const before = saved();
    def = d.def;
    label = d.label;
    source = d.source;
    comparing = null;
    showAll = false;
    showTimer = false;
    say(what, before);
  }

  // ---------------------------------------------------------------- edits
  const CHV_DEVICES: [string, Json][] = [
    ['hypervisor.virtio_transport', 'pci'],
    ['hypervisor.virtio_rng', true],
  ];

  function setBase(path: string, v: Json) {
    if (!isObj(def.base)) def.base = {};
    const b = def.base as Obj;
    setPath(b, path, v);
    if (path === 'hypervisor.name' && v === 'cloud-hypervisor') for (const [p, x] of CHV_DEVICES) setPath(b, p, x);
  }

  function specsObj(): Obj {
    if (!isObj(def.specs)) def.specs = {};
    return def.specs as Obj;
  }

  function setSpec(name: string, path: string, v: Json) {
    const all = specsObj();
    if (!isObj(all[name])) all[name] = {};
    const ch = all[name] as Obj;
    setPath(ch, path, v);
    if (path === 'hypervisor.name' && v === 'cloud-hypervisor') {
      for (const [p, x] of CHV_DEVICES) {
        if (same(getPath(def.base, p), x)) deletePath(ch, p);
        else setPath(ch, p, x);
      }
    }
  }

  function unsetSpec(name: string, path: string) {
    const ch = specsObj()[name];
    if (isObj(ch)) deletePath(ch, path);
  }

  /** Rebuilds the specs (and whys) in order, so a rename or a copy keeps its place. */
  function reorder(entries: [string, Json][]) {
    def.specs = Object.fromEntries(entries);
    if (isObj(def.why)) {
      const w = plain(def.why);
      const kept = Object.fromEntries(Object.entries(w).filter(([k]) => entries.some(([n]) => n === k)));
      if (Object.keys(kept).length) def.why = kept;
      else delete def.why;
    }
  }

  function rename(from: string, to: string, focus: boolean) {
    const entries = Object.entries(plain(specsObj())).map(([k, v]): [string, Json] => [k === from ? to : k, v]);
    const w = isObj(def.why) ? plain(def.why) : null;
    def.specs = Object.fromEntries(entries);
    if (w && from in w) def.why = Object.fromEntries(Object.entries(w).map(([k, v]) => [k === from ? to : k, v]));
    if (focus) tick().then(() => document.getElementById(`s-${to}-name`)?.focus());
  }

  function setWhy(name: string, t: string) {
    if (t.trim()) {
      if (!isObj(def.why)) def.why = {};
      (def.why as Obj)[name] = t;
    } else if (isObj(def.why)) {
      delete def.why[name];
      if (!Object.keys(def.why).length) delete def.why;
    }
  }

  function duplicate(name: string) {
    const to = freeName(name, names);
    const entries = Object.entries(plain(specsObj())).flatMap(([k, v]): [string, Json][] =>
      k === name ? [[k, v], [to, structuredClone(v)]] : [[k, v]],
    );
    reorder(entries);
    tick().then(() => (document.getElementById(`s-${to}-name`) as HTMLInputElement | null)?.select());
  }

  function remove(name: string) {
    const before = saved();
    reorder(Object.entries(plain(specsObj())).filter(([k]) => k !== name));
    say(`Removed ${name}.`, before);
  }

  function addSpec() {
    const to = freeName(`spec-${names.length + 1}`, names);
    specsObj()[to] = {};
    tick().then(() => (document.querySelector(`select[aria-label="Change an input in ${to}"]`) as HTMLElement | null)?.focus());
  }

  function openCompare(path: string) {
    comparing = comparing === path ? null : path;
    if (comparing) tick().then(() => (document.querySelector('.compare input') as HTMLElement | null)?.focus());
  }

  function applyCompare(path: string, values: Json[], o: CompareOptions) {
    const before = saved();
    if (!isObj(def.base)) return;
    const b = def.base as Obj;
    if (o.matchDevices) for (const [p, x] of CHV_DEVICES) setPath(b, p, x);
    const snapBase = plain(b);
    const bv = getPath(snapBase, path);
    const host = TYPES[String(getPath(snapBase, 'worker_host.instance_type'))];
    const bd = getPath(snapBase, 'densities');
    const made: Obj = {};
    for (const v of values) {
      const ch: Obj = {};
      if (!same(v, bv)) setPath(ch, path, v);
      if (path === 'hypervisor.name' && v === 'cloud-hypervisor') {
        for (const [p, x] of CHV_DEVICES) if (!same(getPath(snapBase, p), x)) setPath(ch, p, x);
      }
      const to = TYPES[String(v)];
      if (o.scale && path === 'worker_host.instance_type' && host && to && Array.isArray(bd)) {
        const d = scaleDensities(bd as number[], host.vcpus, to.vcpus);
        if (!same(d, bd)) ch.densities = d;
      }
      made[freeName(suggestName(path, v), Object.keys(made))] = ch;
    }
    def.specs = made;
    delete def.why;
    comparing = null;
    say(`Made ${values.length} specs.`, before);
    tick().then(() => document.getElementById('h-specs')?.scrollIntoView({ block: 'start', behavior: 'smooth' }));
  }

  const reps = $derived(typeof snap.replicas === 'number' ? snap.replicas : 0);
  const setReplicas = (n: number) => (def.replicas = Math.min(5, Math.max(1, Math.round(n))));

  function showProblem() {
    const el = document.querySelector('.builder .problem');
    if (!el) return;
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    const box = el.closest('.field, .card')?.querySelector('input, select, textarea') as HTMLElement | null;
    box?.focus({ preventScroll: true });
  }

  // ---------------------------------------------------------------- starting points
  let pasting = $state(false);
  let pasteText = $state('');
  let pasteError = $state('');
  function openPaste() {
    pasting = !pasting;
    pasteError = '';
    if (pasting) tick().then(() => document.getElementById('paste')?.focus());
  }
  function loadPaste() {
    try {
      load(interpret(pasteText), 'Loaded the pasted JSON.');
      pasting = false;
      pasteText = '';
    } catch (e) {
      pasteError = `Couldn't read it: ${(e as Error).message}`;
    }
  }
  function pick(key: string) {
    const s = STARTS.find((x) => x.key === key);
    if (!s) return;
    load(fromStart(s), `Loaded ${s.title}.`);
    handled = `campaign:${s.key}`;
    history.replaceState(null, '', `#/builder?from=${handled}`);
  }

  // ---------------------------------------------------------------- the phone's review bar
  let side: HTMLElement | undefined = $state();
  let sideInView = $state(false);
  $effect(() => {
    if (!side || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver(([e]) => (sideInView = e.isIntersecting), { threshold: 0.05 });
    io.observe(side);
    return () => io.disconnect();
  });
</script>

<div class="builder">
  <div class="startbar">
    <span class="startname"><label for="start">Start from</label><Help id="start-about" label="Start from" text={START_HELP} /></span>
    <select id="start" aria-describedby="start-about" value={STARTS.some((s) => s.key === label) ? label : ''} onchange={(e) => pick(e.currentTarget.value)}>
      {#if !STARTS.some((s) => s.key === label)}<option value="">{label}</option>{/if}
      <optgroup label="Campaigns">
        {#each STARTS.filter((s) => !s.example) as s (s.key)}<option value={s.key}>{s.title}</option>{/each}
      </optgroup>
      <optgroup label="Examples, not run">
        {#each STARTS.filter((s) => s.example) as s (s.key)}<option value={s.key}>{s.title}</option>{/each}
      </optgroup>
    </select>
    <button type="button" aria-expanded={pasting} onclick={openPaste}>Paste JSON</button>
    {#if loading}
      <span class="muted small" role="status">Opening {loading.replace(/^\w+:/, '')}…</span>
    {:else if edited}
      <span class="edited small">Edited</span>
      <button type="button" class="link small" onclick={() => source && load({ def: structuredClone(source), label, source }, `Reset ${label}.`)}>
        Reset
      </button>
    {/if}
  </div>

  {#if pasting}
    <div class="paste">
      <label for="paste">Campaign or spec JSON</label>
      <textarea
        id="paste"
        rows="7"
        spellcheck="false"
        bind:value={pasteText}
        onkeydown={(e) => {
          if (e.key === 'Escape') pasting = false;
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) loadPaste();
        }}
      ></textarea>
      {#if pasteError}<p class="msg error" role="alert">{pasteError}</p>{/if}
      <div class="row">
        <button type="button" class="primary" onclick={loadPaste} disabled={!pasteText.trim()}>Load</button>
        <button type="button" class="link" onclick={() => (pasting = false)}>Cancel</button>
      </div>
    </div>
  {/if}

  <div class="layout">
    <div class="editor">
      <section aria-labelledby="h-question">
        <h2 id="h-question">Question</h2>
        <div class="box">
          <Field
            id="c-name"
            field={CAMPAIGN_FIELDS.name}
            value={snap.name}
            onchange={(v) => (def.name = v)}
            msgs={at(msgs, 'name')}
          />
          {#if clash}
            <p class="hint">
              Name taken by a campaign <button type="button" class="link" onclick={() => (def.name = clash)}>Use {clash}</button>
            </p>
          {/if}
          <Field id="c-question" field={CAMPAIGN_FIELDS.question} value={snap.question} onchange={(v) => (def.question = v)} msgs={at(msgs, 'question')} multiline />
        </div>
      </section>

      <section aria-labelledby="h-base">
        <h2 id="h-base">Base spec</h2>
        <div class="box">
          {#each baseFields as f (f.path)}
            <Field
              id={fieldId(f.path)}
              field={f}
              value={valueIn(base, f)}
              onchange={(v) => setBase(f.path, v)}
              msgs={at(msgs, `base.${f.path}`)}
              facts={baseFacts(f.path)}
              spec={base}
            >
              {#if f.variable && f.schema.type !== 'array'}
                <button type="button" class="ghost" aria-expanded={comparing === f.path} onclick={() => openCompare(f.path)}>
                  Vary this
                </button>
              {/if}
            </Field>
            {#if comparing === f.path}
              {#key f.path}
                <Compare
                  field={f}
                  {base}
                  specCount={names.length}
                  onapply={(v, o) => applyCompare(f.path, v, o)}
                  onclose={() => (comparing = null)}
                />
              {/key}
            {/if}
          {/each}
        </div>
        {#if hiddenCount || showAll}
          <button type="button" class="link more" aria-expanded={showAll} onclick={() => (showAll = !showAll)}>
            {showAll ? 'Fewer settings' : `${hiddenCount} more settings`}
          </button>
        {/if}
      </section>

      <section aria-labelledby="h-specs">
        <h2 id="h-specs">Specs</h2>
        {#each names as n (n)}
          <SpecCard
            name={n}
            changes={specs[n]}
            {base}
            spec={resolved.find(([k]) => k === n)?.[1] ?? base}
            why={typeof why[n] === 'string' ? (why[n] as string) : undefined}
            {msgs}
            names={names.filter((x) => x !== n)}
            onset={(p, v) => setSpec(n, p, v)}
            onunset={(p) => unsetSpec(n, p)}
            onrename={(to, focus) => rename(n, to, focus)}
            onwhy={(t) => setWhy(n, t)}
            onduplicate={() => duplicate(n)}
            onremove={() => remove(n)}
          />
        {:else}
          <p class="muted small">No specs yet.</p>
        {/each}
        {#if names.length < 12}<button type="button" class="add" onclick={addSpec}>+ Add spec</button>{/if}
      </section>

      <section aria-labelledby="h-runs">
        <h2 id="h-runs">Runs</h2>
        <div class="box">
          <div class="line">
            <span class="lname"
              ><span id="l-replicas">{CAMPAIGN_FIELDS.replicas.label}</span><Help
                id="c-replicas-about"
                label={CAMPAIGN_FIELDS.replicas.label}
                text={CAMPAIGN_FIELDS.replicas.help}
              /></span
            >
            <div>
              <div class="replicas">
                <div class="stepper" role="group" aria-labelledby="l-replicas" aria-describedby="c-replicas-about">
                  <button type="button" aria-label="Fewer replicas" disabled={reps <= 1} onclick={() => setReplicas(reps - 1)}>−</button>
                  <output id="c-replicas" aria-live="polite">{String(snap.replicas ?? '–')}</output>
                  <button type="button" aria-label="More replicas" disabled={reps >= 5} onclick={() => setReplicas(reps + 1)}>+</button>
                </div>
                <span class="hosts" aria-hidden="true">{#each { length: Math.min(5, Math.max(0, reps)) } as _, i (i)}<span class="host"></span>{/each}</span>
                <span class="small muted">{reps === 1 ? 'worker host' : 'worker hosts'} per spec</span>
              </div>
              {#each at(msgs, 'replicas') as m, i (i)}<p class="msg" class:error={m.error} class:problem={m.error}>{m.text}</p>{/each}
            </div>
          </div>
          {#if timerShown}
            <Field
              id="c-shutdown"
              field={TIMER}
              value={snap.shutdown_after_minutes}
              onchange={(v) => (def.shutdown_after_minutes = v)}
              msgs={at(msgs, TIMER.path)}
            />
          {:else}
            <div class="line">
              <span class="lname">{TIMER.label}<Help id="c-shutdown-about" label={TIMER.label} text={TIMER.help} /></span>
              <span class="timer">
                {String(snap.shutdown_after_minutes)} min
                <button type="button" class="link" onclick={() => (showTimer = true)}>Change</button>
              </span>
            </div>
          {/if}
        </div>
      </section>
    </div>

    <aside class="side" bind:this={side}>
      <Review {est} specs={resolved} {text} errors={result.errors.length} {others} onshow={showProblem} />
    </aside>
  </div>

  <div class="phonebar" class:away={sideInView}>
    <span>
      {est ? `${est.runs.length} runs · ${usd(est.expected_usd)} · worst ${usd(est.worst_case_usd)}` : '–'}
      {#if result.errors.length}<span class="err"> · {result.errors.length} to fix</span>{/if}
    </span>
    <button type="button" class="primary" onclick={() => side?.scrollIntoView({ behavior: 'smooth' })}>Review</button>
  </div>

  {#if toast}
    <div class="toast" role="status">
      {toast.text}
      {#if toast.undo}<button type="button" class="link" onclick={undo}>Undo</button>{/if}
    </div>
  {/if}
</div>

<style>
  .builder {
    padding-bottom: 8px;
  }
  .startbar,
  .row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 12px;
    margin: 12px 0 8px;
  }
  .startbar label {
    color: var(--ink-2);
    font-size: 0.92rem;
  }
  .startname {
    display: inline-flex;
    align-items: center;
  }
  .small {
    font-size: 0.88rem;
  }
  .paste {
    display: grid;
    gap: 6px;
    margin: 8px 0 16px;
    padding: 12px 14px;
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    font-size: 0.92rem;
  }
  .paste textarea {
    font-family: var(--mono);
    font-size: 0.8rem;
  }
  .layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 22rem;
    gap: 32px;
    align-items: start;
    margin-top: 28px;
  }
  /* Sticks below the site's sticky 56px header, not under it. */
  .side {
    position: sticky;
    top: 68px;
    max-height: calc(100vh - 80px);
    overflow: auto;
    overscroll-behavior: contain;
  }
  section {
    margin-bottom: 32px;
  }
  h2 {
    margin: 0 0 12px;
  }
  .box {
    border: 1px solid var(--rule);
    border-radius: 10px;
    background: var(--bg);
    padding: 2px 16px;
  }
  .box > :global(.field:first-child),
  .box > .line:first-child {
    border-top: 0;
  }
  .line {
    display: grid;
    grid-template-columns: 9rem minmax(0, 1fr);
    gap: 2px 16px;
    align-items: center;
    padding: 10px 0;
    border-top: 1px solid var(--rule);
  }
  .lname {
    color: var(--ink-2);
    font-size: 0.88rem;
  }
  .hint {
    margin: 0 0 10px;
    padding: 8px 10px;
    border-radius: var(--radius);
    background: var(--synthetic-bg);
    color: var(--synthetic-ink);
    font-size: 0.88rem;
  }
  .hint .link {
    color: inherit;
    font-weight: 600;
  }
  .more {
    margin-top: 12px;
    font-size: 0.88rem;
  }
  .replicas {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 16px;
  }
  .hosts {
    display: inline-flex;
    gap: 4px;
  }
  .host {
    width: 14px;
    height: 18px;
    border-radius: 3px;
    border: 1.5px solid var(--accent);
    background: var(--highlight);
  }
  .timer {
    display: inline-flex;
    gap: 12px;
    align-items: baseline;
    font-variant-numeric: tabular-nums;
  }
  .edited {
    padding: 1px 8px;
    border-radius: 999px;
    background: var(--highlight);
    color: var(--accent);
    font-weight: 600;
    font-size: 0.78rem;
  }
  .add {
    margin-top: 4px;
  }
  .stepper {
    display: inline-flex;
    align-items: center;
    border: 1px solid var(--field-border, var(--rule));
    border-radius: var(--radius);
    overflow: hidden;
  }
  .stepper button {
    border: 0;
    border-radius: 0;
    width: 2.4rem;
    height: 2.2rem;
    font-size: 1.1rem;
  }
  .stepper output {
    min-width: 2.4rem;
    text-align: center;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .msg {
    margin: 4px 0;
    font-size: 0.85rem;
    color: var(--synthetic-ink);
  }
  .msg::before {
    content: '⚠ ';
  }
  .msg.error {
    color: var(--critical);
  }
  .msg.error::before {
    content: '× ';
    font-weight: 700;
  }
  .phonebar {
    display: none;
  }
  .toast {
    position: fixed;
    left: 50%;
    bottom: 20px;
    transform: translateX(-50%);
    display: flex;
    gap: 14px;
    align-items: center;
    max-width: calc(100vw - 32px);
    padding: 8px 16px;
    border-radius: 999px;
    background: var(--ink);
    color: var(--bg);
    font-size: 0.9rem;
    box-shadow: 0 4px 16px rgb(0 0 0 / 0.2);
    z-index: 20;
  }
  .toast .link {
    color: var(--bg);
    font-weight: 600;
  }

  /* Form controls, for the builder only, at the lowest specificity so each component's own styles win. */
  :global(:where(.builder) :is(input:not([type='checkbox']), select, textarea)) {
    font: inherit;
    font-size: 0.95rem;
    color: var(--ink);
    background: var(--field-bg, var(--bg));
    border: 1px solid var(--field-border, var(--rule));
    border-radius: var(--radius);
    padding: 5px 8px;
    min-height: 34px;
    max-width: 100%;
  }
  :global(:where(.builder) textarea) {
    line-height: 1.45;
    resize: vertical;
  }
  :global(:where(.builder) input[type='checkbox']) {
    width: 1rem;
    height: 1rem;
    accent-color: var(--accent);
  }
  :global(:where(.builder) :is(input, select, textarea, button, output, pre):focus-visible) {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  :global(:where(.builder) [aria-invalid='true']) {
    border-color: var(--critical);
  }
  :global(:where(.builder) button) {
    font: inherit;
    font-size: 0.9rem;
    color: var(--ink);
    background: var(--field-bg, var(--bg));
    border: 1px solid var(--field-border, var(--rule));
    border-radius: var(--radius);
    padding: 5px 12px;
    cursor: pointer;
  }
  :global(:where(.builder) button:hover:not(:disabled)) {
    border-color: var(--accent);
  }
  :global(:where(.builder) button:disabled) {
    opacity: 0.5;
    cursor: not-allowed;
  }
  :global(:where(.builder) button.primary) {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
    font-weight: 600;
  }
  :global(:where(.builder) button.big) {
    width: 100%;
    padding: 10px 12px;
    font-size: 1rem;
  }
  :global(:where(.builder) button.link) {
    border: 0;
    background: none;
    color: var(--accent-ink, var(--accent));
    font-weight: 600;
    padding: 0;
    text-underline-offset: 2px;
  }
  :global(:where(.builder) button.link:hover) {
    text-decoration: underline;
  }
  :global(:where(.builder) button.ghost) {
    border-color: transparent;
    background: none;
    color: var(--accent-ink, var(--accent));
    font-weight: 600;
    padding: 4px 8px;
    white-space: nowrap;
  }
  :global(:where(.builder) button.ghost:hover),
  :global(:where(.builder) button.ghost[aria-expanded='true']) {
    background: var(--highlight);
  }

  @media (max-width: 560px) {
    .line {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  @media (max-width: 900px) {
    .layout {
      grid-template-columns: minmax(0, 1fr);
    }
    .side {
      position: static;
      max-height: none;
      overflow: visible;
    }
    .builder {
      padding-bottom: 64px;
    }
    .phonebar {
      display: flex;
      position: fixed;
      left: 0;
      right: 0;
      bottom: 0;
      z-index: 10;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px var(--gutter);
      background: var(--surface);
      border-top: 1px solid var(--rule);
      font-size: 0.9rem;
    }
    .phonebar.away {
      display: none;
    }
    .phonebar .err {
      color: var(--critical);
    }
    .toast {
      bottom: 72px;
    }
  }
</style>
