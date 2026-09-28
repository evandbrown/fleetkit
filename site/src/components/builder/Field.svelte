<script lang="ts">
  // One input, drawn from its schema: a select for a list of values, a checkbox for on or off, the densities
  // editor, a box of lines for a list of text (one item per line), or a text box that keeps what was typed and
  // hands over a number when it is one. Beside the label, an ⓘ
  // with what the field means; under the input, one line of facts (what follows from the value, and the base's value
  // in a named spec), then its messages.
  import type { Snippet } from 'svelte';
  import { same, type Field, type Json } from '../../lib/campaign';
  import { optionLabel, show, type Msg } from './draft';
  import Densities from './Densities.svelte';
  import DensityScale from './DensityScale.svelte';
  import Help from './Help.svelte';

  let {
    id,
    field,
    value,
    onchange,
    msgs = [],
    base,
    facts = '',
    spec,
    label = field.label,
    multiline = false,
    children,
  }: {
    id: string;
    field: Field;
    value: Json | undefined;
    onchange: (v: Json) => void;
    msgs?: Msg[];
    /** In a named spec: the base's value, shown beside the change. */
    base?: Json;
    facts?: string;
    /** For the densities: the whole spec, to draw them against its host. */
    spec?: unknown;
    label?: string;
    multiline?: boolean;
    children?: Snippet;
  } = $props();

  const s = $derived(field.schema);
  const kind = $derived(
    field.path === 'densities'
      ? 'densities'
      : s.enum
        ? 'enum'
        : s.type === 'boolean'
          ? 'boolean'
          : s.type === 'integer' || s.type === 'number'
            ? 'number'
            : s.type === 'array'
              ? 'lines'
              : 'text',
  );
  const errored = $derived(msgs.some((m) => m.error));
  const line = $derived([facts, base !== undefined ? `base ${show(field.path, base)}` : ''].filter(Boolean).join(' · '));
  let help: Help | undefined = $state();
  const helpId = $derived(`${id}-help`);
  const aboutId = $derived(`${id}-about`);
  /** The input names its explanation first, then the facts and messages under it. */
  const described = $derived(`${aboutId} ${helpId}`);

  // A text box shows what was typed ("2." on the way to "2.5") for as long as it still means the current value,
  // and the value itself once it changes from elsewhere.
  const parse = (t: string): Json => {
    if (kind === 'lines') return t.split('\n').map((x) => x.trim()).filter(Boolean);
    if (kind !== 'number') return t;
    const x = t.replace(/[,_\s]/g, '');
    return /^-?\d+(\.\d+)?$/.test(x) ? Number(x) : t;
  };
  const asText = (v: unknown) =>
    v === undefined || v === null ? '' : kind === 'lines' && Array.isArray(v) ? v.join('\n') : String(v);
  let typed: string | null = $state(null);
  const text = $derived(typed !== null && same(parse(typed), value) ? typed : asText(value));
  function input(e: Event & { currentTarget: HTMLInputElement | HTMLTextAreaElement }) {
    typed = e.currentTarget.value;
    onchange(parse(typed));
  }

  function msgText(m: Msg): string {
    const i = /\[(\d+)\]$/.exec(m.path);
    if (i && Array.isArray(value)) return `${value[Number(i[1])]}: ${m.text}`;
    return m.text;
  }
</script>

<div class="field" class:errored class:inspec={base !== undefined}>
  <div class="name">
    <label for={id} onpointerenter={(e) => help?.peek(e)} onpointerleave={(e) => help?.unpeek(e)}>{label}</label><Help
      bind:this={help}
      id={aboutId}
      {label}
      text={field.help}
    />
  </div>
  <div class="body">
    <div class="control">
      {#if kind === 'enum'}
        <select {id} value={value} onchange={(e) => onchange(s.enum.find((o: Json) => String(o) === e.currentTarget.value) ?? e.currentTarget.value)} aria-describedby={described} aria-invalid={errored}>
          {#if !s.enum.some((o: Json) => same(o, value))}<option value={asText(value)}>{asText(value) || '(none)'}</option>{/if}
          {#each s.enum as o (o)}<option value={o}>{optionLabel(field.path, o)}</option>{/each}
        </select>
      {:else if kind === 'boolean'}
        <label class="check">
          <input {id} type="checkbox" checked={value === true} onchange={(e) => onchange(e.currentTarget.checked)} aria-describedby={described} />
          {value === true ? 'on' : value === false ? 'off' : asText(value)}
        </label>
      {:else if kind === 'densities'}
        <Densities {id} {value} {onchange} invalid={errored} describedby={described} />
      {:else if kind === 'number'}
        <input
          {id}
          class="num"
          inputmode="decimal"
          autocomplete="off"
          value={text}
          oninput={input}
          aria-describedby={described}
          aria-invalid={errored}
        />
        {#if field.unit}<span class="unit">{field.unit}</span>{/if}
      {:else if kind === 'lines'}
        <textarea
          {id}
          class="lines"
          rows="2"
          spellcheck="false"
          autocomplete="off"
          placeholder="none"
          value={text}
          oninput={input}
          aria-describedby={described}
          aria-invalid={errored}
        ></textarea>
      {:else if multiline}
        <textarea {id} rows="3" value={text} oninput={input} aria-describedby={described} aria-invalid={errored}></textarea>
      {:else}
        <input {id} class="text" autocomplete="off" spellcheck="false" value={text} oninput={input} aria-describedby={described} aria-invalid={errored} />
      {/if}
      {@render children?.()}
    </div>
    <div id={helpId}>
      {#if kind === 'densities' && spec}<DensityScale {spec} />{/if}
      {#if line}<p class="facts">{line}</p>{/if}
      {#each msgs as m, i (i)}<p class="msg" class:error={m.error} class:problem={m.error}>{msgText(m)}</p>{/each}
    </div>
  </div>
</div>

<style>
  .field {
    display: grid;
    grid-template-columns: 9rem minmax(0, 1fr);
    gap: 2px 16px;
    padding: 10px 0;
    border-top: 1px solid var(--rule);
  }
  .name {
    color: var(--ink-2);
    font-size: 0.88rem;
    padding-top: 7px;
  }
  .errored .name {
    color: var(--critical);
  }
  /* The label explains itself on hover, as its ⓘ does. */
  .name label {
    cursor: help;
    text-decoration: underline dotted transparent;
    text-underline-offset: 3px;
  }
  .name label:hover {
    text-decoration-color: var(--muted);
  }
  .control {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 8px;
  }
  /* In a named spec every field is a change from the base, marked as Results marks what differs; the button that
     goes back to the base's value stays beside the input. */
  .inspec {
    margin: 8px -8px 0;
    padding: 10px 8px 10px 11px;
    border-top: 0;
    border-radius: 6px;
    background: var(--highlight);
    box-shadow: inset 3px 0 0 var(--accent);
  }
  .inspec .control {
    flex-wrap: nowrap;
  }
  .inspec .control > select {
    min-width: 0;
  }
  .unit {
    color: var(--muted);
    font-size: 0.9rem;
  }
  /* The widest option (a metal host) sets a select's width otherwise, and pushes Compare onto its own line. */
  .control > select {
    max-width: min(100%, 17rem);
  }
  .num {
    width: 8rem;
    font-variant-numeric: tabular-nums;
  }
  .text,
  textarea {
    width: 100%;
  }
  textarea {
    field-sizing: content;
    min-height: 4.8em;
  }
  textarea.lines {
    min-height: 2.6em;
    font-family: var(--mono);
    font-size: 0.85rem;
  }
  .check {
    display: inline-flex;
    gap: 8px;
    align-items: center;
    padding: 6px 0;
  }
  .facts,
  .msg {
    margin: 4px 0 0;
    font-size: 0.82rem;
  }
  .facts {
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .msg {
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
  @media (max-width: 560px) {
    .field {
      grid-template-columns: minmax(0, 1fr);
    }
    .name {
      padding-top: 0;
    }
  }
</style>
