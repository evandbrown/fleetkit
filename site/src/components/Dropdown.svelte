<script lang="ts" module>
  /** Numbers each dropdown on the page, for element ids when the caller gives none. */
  let seq = 0;
</script>

<script lang="ts" generics="T extends { id: string }">
  // A select-style dropdown: a button naming the choice, and under it a panel of options, the button's width, that
  // scrolls past 70vh. What the button and each option show is the caller's (a rich card, a colour dot and a name),
  // so the campaign picker and the At the limit spec selector share this. It behaves like a native select: Enter,
  // Space or an arrow key opens; the arrows, Home and End move the cursor; Enter chooses; Escape closes; a click
  // outside closes; focus returns to the button. Typing does nothing special. Light only, tokens only, no
  // dependencies.
  import { tick, type Snippet } from 'svelte';

  let {
    items,
    selected,
    label,
    onselect,
    button,
    option,
    id: given,
    controls,
  }: {
    /** The choices, each with a unique id (ascii, no spaces). */
    items: T[];
    /** The id of the chosen item. */
    selected: string;
    /** The control's accessible name ("Campaign", "Spec"); the button's name is this followed by its content. */
    label: string;
    /** Called with the id of the item chosen. The caller sets `selected`, or navigates. */
    onselect: (id: string) => void;
    /** The closed control's content, for the selected item. */
    button: Snippet<[T]>;
    /** One option's content: the item, and whether it is the selected one. */
    option: Snippet<[T, boolean]>;
    /** A base for the element ids ("campaign-picker" gives campaign-picker-button); numbered when left out. */
    id?: string;
    /** The id of an element outside the control whose content the choice sets, for aria-controls. */
    controls?: string;
  } = $props();

  const numbered = `dropdown-${++seq}`;
  const base = $derived(given ?? numbered);
  const buttonId = $derived(`${base}-button`);
  const listId = $derived(`${base}-list`);
  const labelId = $derived(`${base}-label`);
  const optionId = (item: T) => `${base}-option-${item.id.replace(/[^A-Za-z0-9_-]/g, '-')}`;

  let open = $state(false);
  /** The option under the keyboard cursor while open. */
  let active = $state(0);
  /** On a phone, the panel's offset and width so it runs gutter to gutter; null elsewhere. */
  let span = $state<{ left: number; width: number } | null>(null);
  let root: HTMLElement | undefined = $state();
  let btn: HTMLButtonElement | undefined = $state();
  let list: HTMLElement | undefined = $state();

  const current = $derived(items.find((i) => i.id === selected));

  /** Puts focus on the button, for a caller that re-renders around the control and wants focus back on it. */
  export function focus() {
    btn?.focus({ preventScroll: true });
  }

  function show() {
    const i = items.findIndex((x) => x.id === selected);
    active = i < 0 ? 0 : i;
    span = phoneSpan();
    open = true;
    // The list renders on the next tick; it takes focus so the arrow keys move the cursor.
    tick().then(() => {
      list?.focus({ preventScroll: true });
      reveal();
    });
  }

  function hide(refocus = true) {
    if (!open) return;
    open = false;
    if (refocus) btn?.focus({ preventScroll: true });
  }

  function choose(id: string) {
    hide();
    onselect(id);
  }

  /** The page's gutter, when the screen is a phone's: the panel then spans the page's width, not the button's. */
  function phoneSpan(): { left: number; width: number } | null {
    if (!root || typeof matchMedia === 'undefined' || !matchMedia('(max-width: 640px)').matches) return null;
    const gutter = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--gutter')) || 16;
    return { left: gutter - root.getBoundingClientRect().left, width: document.documentElement.clientWidth - 2 * gutter };
  }

  /** Scrolls the panel so the option under the cursor is in view. */
  function reveal() {
    (list?.children[active] as HTMLElement | undefined)?.scrollIntoView({ block: 'nearest' });
  }

  function keyButton(e: KeyboardEvent) {
    if (!['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) return;
    e.preventDefault();
    if (open) hide();
    else show();
  }

  function keyList(e: KeyboardEvent) {
    const last = items.length - 1;
    let next = -1;
    if (e.key === 'ArrowDown') next = Math.min(active + 1, last);
    else if (e.key === 'ArrowUp') next = Math.max(active - 1, 0);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = last;
    else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (items[active]) choose(items[active].id);
      return;
    } else if (e.key === 'Escape') {
      e.preventDefault();
      hide();
      return;
    } else if (e.key === 'Tab') {
      // Focus goes back to the button, so the Tab moves on from there.
      hide();
      return;
    } else return;
    e.preventDefault();
    active = next;
    reveal();
  }

  /** Focus leaving the control (a Tab away, a click elsewhere) closes it without taking focus back. */
  function focusout(e: FocusEvent) {
    if (open && root && !(e.relatedTarget instanceof Node && root.contains(e.relatedTarget))) hide(false);
  }

  // A click or tap outside closes it: focusout alone misses taps on things that never take focus.
  $effect(() => {
    if (!open) return;
    const down = (e: PointerEvent) => {
      if (root && !root.contains(e.target as Node)) hide(false);
    };
    document.addEventListener('pointerdown', down, true);
    return () => document.removeEventListener('pointerdown', down, true);
  });
</script>

<div class="dropdown" bind:this={root} onfocusout={focusout}>
  <span class="sr" id={labelId}>{label}</span>
  <button
    type="button"
    class="control"
    id={buttonId}
    bind:this={btn}
    aria-haspopup="listbox"
    aria-expanded={open}
    aria-labelledby="{labelId} {buttonId}"
    aria-controls={[open ? listId : null, controls].filter(Boolean).join(' ') || undefined}
    onclick={() => (open ? hide() : show())}
    onkeydown={keyButton}
  >
    <span class="content">{#if current}{@render button(current)}{/if}</span>
    <svg class="chevron" aria-hidden="true" viewBox="0 0 16 16" width="16" height="16">
      <path d="M3.5 6l4.5 4.5L12.5 6" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  </button>
  {#if open}
    <!-- The list holds focus and names the option under the cursor (aria-activedescendant); the options themselves
         don't take focus, as in a native select. -->
    <!-- svelte-ignore a11y_interactive_supports_focus, a11y_click_events_have_key_events -->
    <ul
      class="panel"
      role="listbox"
      id={listId}
      tabindex="-1"
      aria-labelledby={labelId}
      aria-activedescendant={items[active] ? optionId(items[active]) : undefined}
      bind:this={list}
      onkeydown={keyList}
      style:left={span ? `${span.left}px` : undefined}
      style:width={span ? `${span.width}px` : undefined}
    >
      {#each items as item, i (item.id)}
        <li
          role="option"
          id={optionId(item)}
          aria-selected={item.id === selected}
          class:active={i === active}
          onclick={() => choose(item.id)}
          onpointermove={() => (active = i)}
        >
          {@render option(item, item.id === selected)}
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .dropdown {
    position: relative;
  }
  /* Read by assistive technology only. */
  .sr {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
  .control {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    margin: 0;
    padding: 10px 12px 10px 14px;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--surface);
    color: var(--ink);
    font: inherit;
    text-align: left;
    cursor: pointer;
    transition:
      border-color 0.12s,
      box-shadow 0.12s;
  }
  .control:hover,
  .control[aria-expanded='true'] {
    border-color: var(--accent);
  }
  .control[aria-expanded='true'] {
    box-shadow: var(--shadow);
  }
  .content {
    flex: 1;
    min-width: 0;
  }
  .chevron {
    flex: none;
    color: var(--muted);
    transition: transform 0.12s;
  }
  .control[aria-expanded='true'] .chevron {
    transform: rotate(180deg);
  }
  .panel {
    position: absolute;
    z-index: 5;
    top: calc(100% + 6px);
    left: 0;
    width: 100%;
    max-width: none;
    max-height: 70vh;
    overflow-y: auto;
    overscroll-behavior: contain;
    margin: 0;
    padding: 6px;
    list-style: none;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--bg);
    box-shadow:
      var(--shadow),
      0 12px 32px rgb(27 26 24 / 0.12);
  }
  /* The cursor's option is marked instead of the list. */
  .panel:focus,
  .panel:focus-visible {
    outline: none;
  }
  .panel li {
    padding: 8px 10px;
    border-radius: 6px;
    cursor: pointer;
  }
  .panel li + li {
    margin-top: 2px;
  }
  .panel li.active {
    background: var(--surface);
  }
  .panel li[aria-selected='true'] {
    background: var(--highlight);
  }
  .panel li[aria-selected='true'].active {
    box-shadow: inset 0 0 0 1px var(--accent);
  }
</style>
