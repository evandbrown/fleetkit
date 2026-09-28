<script lang="ts">
  // One named spec: only its changes from the base, each beside the base's value, and an optional sentence on why
  // it is in the campaign. Changing the host size offers the same densities per host vCPU.
  import {
    CAMPAIGN_FIELDS,
    SPEC_FIELDS,
    TYPES,
    fieldAt,
    flatten,
    getPath,
    isObj,
    same,
    scaleDensities,
    suggestName,
    valueIn,
    type Json,
    type Obj,
  } from '../../lib/campaign';
  import { ADD_HELP, NAME_RULE, SPEC_NAME_HELP, WHY_HELP, at, hostFacts, under, type Msg } from './draft';
  import Field from './Field.svelte';
  import Help from './Help.svelte';

  let {
    name,
    changes,
    base,
    spec,
    why,
    msgs,
    names,
    onset,
    onunset,
    onrename,
    onwhy,
    onduplicate,
    onremove,
  }: {
    name: string;
    changes: Json;
    base: Obj;
    spec: Obj;
    why: string | undefined;
    msgs: Msg[];
    names: string[];
    onset: (path: string, v: Json) => void;
    onunset: (path: string) => void;
    onrename: (to: string, focus: boolean) => void;
    onwhy: (text: string) => void;
    onduplicate: () => void;
    onremove: () => void;
  } = $props();

  const slug = $derived(name.replace(/[^a-z0-9-]/g, '_'));
  let nameHelp: Help | undefined = $state();
  const idOf = (path: string) => `s-${slug}-${path.replace(/[._]/g, '-')}`;
  const own = $derived(isObj(changes) ? flatten(changes) : {});
  const paths = $derived(Object.keys(own));
  const addable = $derived(SPEC_FIELDS.filter((f) => f.variable && !paths.includes(f.path)));
  const mine = $derived(under(msgs, `specs.${name}`));
  const top = $derived(at(mine, `specs.${name}`));
  const rest = $derived(mine.filter((m) => m.path !== `specs.${name}` && !paths.some((p) => at([m], `specs.${name}.${p}`).length)));

  // ---- the name: checked as it's typed, applied on Enter or leaving the box
  const NAME = CAMPAIGN_FIELDS.name.schema;
  let nameText = $state('');
  $effect.pre(() => {
    nameText = name;
  });
  const nameNote = $derived(
    nameText === name
      ? ''
      : !new RegExp(NAME.pattern).test(nameText) || nameText.length < 2 || nameText.length > 40
        ? NAME_RULE
        : names.includes(nameText)
          ? 'taken by another spec'
          : '',
  );
  function applyName(focus: boolean) {
    if (nameText !== name && !nameNote) onrename(nameText, focus);
  }

  // ---- why: an empty box removes it
  let whyText = $state('');
  $effect.pre(() => {
    whyText = why ?? '';
  });

  // ---- adding a change starts from a value that differs from the base
  function startValue(path: string): Json {
    const f = fieldAt(path)!;
    const b = valueIn(base, f) as Json;
    const opts = f.schema.enum as Json[] | undefined;
    if (opts) {
      const i = opts.findIndex((o) => same(o, b));
      return opts[i > 0 ? i - 1 : Math.min(1, opts.length - 1)];
    }
    if (typeof b === 'boolean') return !b;
    // extra Chromium flags: a first flag to edit when the base has none (the base's value when it leaves them out)
    if (path === 'workload.chromium_extra_flags') {
      return Array.isArray(b) && b.length ? structuredClone(b) : ['--renderer-process-limit=2'];
    }
    if (typeof b === 'number' && path !== 'densities') {
      const max = f.schema.maximum ?? Infinity;
      return b * 2 <= max ? b * 2 : Math.max(f.schema.minimum ?? 1, Math.floor(b / 2));
    }
    return structuredClone(b);
  }

  let offer: number[] | null = $state(null);
  let offerFrom = $state(0);

  function set(path: string, v: Json) {
    onset(path, v);
    if (path === 'worker_host.instance_type' && typeof v === 'string' && TYPES[v]) {
      const bt = TYPES[String(getPath(base, 'worker_host.instance_type'))];
      const bd = getPath(base, 'densities');
      if (bt && Array.isArray(bd) && bd.every((x) => typeof x === 'number') && bt.vcpus !== TYPES[v].vcpus) {
        const scaled = scaleDensities(bd as number[], bt.vcpus, TYPES[v].vcpus);
        offer = same(scaled, getPath(spec, 'densities')) ? null : scaled;
        offerFrom = bt.vcpus;
      } else offer = null;
    }
  }

  function scale() {
    if (!offer) return;
    if (same(offer, getPath(base, 'densities'))) onunset('densities');
    else onset('densities', offer);
    offer = null;
  }

  function add(e: Event & { currentTarget: HTMLSelectElement }) {
    const path = e.currentTarget.value;
    e.currentTarget.value = '';
    if (!path) return;
    const v = startValue(path);
    const first = !paths.length;
    set(path, v);
    if (/^spec-\d+$/.test(name) && first) {
      const to = suggestName(path, v);
      if (!names.includes(to)) onrename(to, false);
    }
    requestAnimationFrame(() => document.getElementById(idOf(path))?.focus());
  }

  function facts(path: string, v: unknown): string {
    if (path.endsWith('instance_type')) return hostFacts(v);
    if (path === 'microvm.memory_mib' && typeof v === 'number') return `${+(v / 1024).toFixed(2)} GiB`;
    return '';
  }
</script>

<article class="card" aria-label="Spec {name}">
  <header>
    <label class="snl" for="s-{slug}-name" onpointerenter={(e) => nameHelp?.peek(e)} onpointerleave={(e) => nameHelp?.unpeek(e)}>Spec name</label>
    <Help bind:this={nameHelp} id="s-{slug}-name-about" label="Spec name" text={SPEC_NAME_HELP} />
    <input
      id="s-{slug}-name"
      class="specname"
      autocomplete="off"
      spellcheck="false"
      bind:value={nameText}
      onblur={() => applyName(false)}
      onkeydown={(e) => {
        if (e.key === 'Enter') applyName(true);
        if (e.key === 'Escape') nameText = name;
      }}
      aria-invalid={!!nameNote || top.some((m) => m.error)}
      aria-describedby="s-{slug}-name-about"
    />
    <span class="actions">
      <button type="button" class="link" onclick={onduplicate}>Duplicate</button>
      <button type="button" class="link" onclick={onremove}>Remove</button>
    </span>
  </header>
  {#if nameNote}<p class="msg error">{nameNote}</p>{/if}
  {#each top as m, i (i)}<p class="msg" class:error={m.error} class:problem={m.error}>{m.text}</p>{/each}
  {#if !isObj(changes)}<p class="msg error problem">must be an object of changes</p>{/if}

  {#each paths as path (path)}
    {@const f = fieldAt(path)}
    {#if f}
      <Field
        id={idOf(path)}
        field={f}
        value={own[path]}
        onchange={(v) => set(path, v)}
        msgs={at(mine, `specs.${name}.${path}`)}
        base={valueIn(base, f) as Json}
        facts={facts(path, own[path])}
        {spec}
      >
        <button type="button" class="unset" aria-label="Use the base's {f.label.toLowerCase()}" onclick={() => onunset(path)}>×</button>
      </Field>
      {#if path === 'worker_host.instance_type' && offer}
        <p class="offer">
          <span>Browser counts for {TYPES[String(own[path])]?.vcpus} vCPUs, not {offerFrom}: <strong>{offer.join(', ')}</strong></span>
          <button type="button" onclick={scale}>Scale</button>
          <button type="button" class="link" onclick={() => (offer = null)}>Keep</button>
        </p>
      {/if}
    {:else}
      <p class="msg error problem">
        <code>{path}</code>: not an input <button type="button" class="link" onclick={() => onunset(path)}>Remove</button>
      </p>
    {/if}
  {/each}

  {#each rest as m, i (i)}
    {@const rel = m.path.slice(`specs.${name}.`.length).replace(/\[\d+\]$/, '')}
    <p class="msg" class:error={m.error} class:problem={m.error}>{fieldAt(rel)?.label ?? rel}: {m.text}</p>
  {/each}

  {#if addable.length}
    <span class="addrow">
      <select class="add" aria-label="Change an input in {name}" aria-describedby="s-{slug}-add-about" onchange={add}>
        <option value="">+ Change an input</option>
        {#each addable as f (f.path)}<option value={f.path}>{f.label}</option>{/each}
      </select>
      <Help id="s-{slug}-add-about" label="Change an input" text={ADD_HELP} />
    </span>
  {/if}

  <div class="why">
    <span class="whyname"><label for="s-{slug}-why">Why</label><Help id="s-{slug}-why-about" label="Why" text={WHY_HELP} /></span>
    <textarea
      id="s-{slug}-why"
      aria-describedby="s-{slug}-why-about"
      rows="2"
      bind:value={whyText}
      oninput={(e) => onwhy(e.currentTarget.value)}
      placeholder="Optional. One sentence."
      aria-invalid={at(msgs, `why.${name}`).some((m) => m.error)}
    ></textarea>
  </div>
  {#each at(msgs, `why.${name}`) as m, i (i)}<p class="msg" class:error={m.error} class:problem={m.error}>{m.text}</p>{/each}
</article>

<style>
  .card {
    background: var(--bg);
    border: 1px solid var(--rule);
    border-radius: 10px;
    padding: 12px 16px 14px;
    margin: 0 0 12px;
  }
  header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 10px;
    margin-bottom: 6px;
  }
  .specname {
    font: 600 1rem var(--mono);
    width: min(100%, 16rem);
    margin-left: 4px;
  }
  .actions {
    margin-left: auto;
    display: flex;
    gap: 12px;
  }
  .unset {
    border: 0;
    background: none;
    color: var(--muted);
    font-size: 1.1rem;
    line-height: 1;
    padding: 4px 6px;
    cursor: pointer;
    border-radius: var(--radius);
  }
  .unset:hover {
    color: var(--critical);
    background: var(--surface-2);
  }
  .offer {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 12px;
    margin: 0 0 8px;
    padding: 8px 10px;
    border-radius: var(--radius);
    background: var(--highlight);
    font-size: 0.88rem;
  }
  .offer span {
    flex: 1 1 14rem;
  }
  .addrow {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    margin-top: 10px;
  }
  .snl {
    font-size: 0.88rem;
    color: var(--ink-2);
    cursor: help;
  }
  .why {
    display: grid;
    gap: 4px;
    margin-top: 12px;
    font-size: 0.92rem;
    color: var(--ink-2);
  }
  .whyname {
    display: inline-flex;
    align-items: center;
  }
  .why textarea {
    width: 100%;
    field-sizing: content;
    min-height: 3.2em;
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
</style>
