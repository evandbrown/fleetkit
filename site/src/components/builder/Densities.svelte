<script lang="ts">
  // The densities as chips, kept in increasing order with each density once. Type a density, several ("12, 14"),
  // or a range ("9-11"), then Enter. Backspace in the empty box removes the last one; the left arrow moves onto the
  // chips, where Backspace or Delete removes one.
  import type { Json } from '../../lib/campaign';
  import { browsers } from '../../lib/glossary';

  let {
    id,
    value,
    onchange,
    invalid = false,
    describedby,
  }: { id: string; value: Json | undefined; onchange: (v: Json) => void; invalid?: boolean; describedby?: string } =
    $props();

  const list = $derived(Array.isArray(value) ? value : []);
  let text = $state('');
  let note = $state('');
  let box: HTMLInputElement | undefined = $state();
  const chips: HTMLButtonElement[] = $state([]);

  function commit() {
    const add: number[] = [];
    const bad: string[] = [];
    for (const t of text.split(/[\s,;]+/).filter(Boolean)) {
      const r = /^(\d+)[-–](\d+)$/.exec(t);
      if (r) {
        const [a, b] = [Number(r[1]), Number(r[2])].sort((x, y) => x - y);
        for (let n = a; n <= b && n - a < 200; n++) add.push(n);
      } else if (/^\d+$/.test(t)) add.push(Number(t));
      else bad.push(t);
    }
    note = bad.length ? `${bad.join(', ')}: use whole numbers or a range like 9-11` : '';
    text = bad.join(' ');
    if (!add.length) return;
    const nums = list.filter((x): x is number => typeof x === 'number');
    onchange([...new Set([...nums, ...add])].sort((a, b) => a - b));
  }

  function remove(i: number) {
    onchange(list.filter((_, k) => k !== i));
  }

  function chipKey(e: KeyboardEvent, i: number) {
    const go = (k: number) => (k >= list.length ? box?.focus() : chips[Math.max(0, k)]?.focus());
    if (e.key === 'ArrowLeft') go(i - 1);
    else if (e.key === 'ArrowRight') go(i + 1);
    else if (e.key === 'Backspace' || e.key === 'Delete') {
      remove(i);
      requestAnimationFrame(() => go(e.key === 'Backspace' ? i - 1 : i));
    } else return;
    e.preventDefault();
  }

  function key(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      commit();
    } else if (e.key === 'Backspace' && !text && list.length) {
      e.preventDefault();
      remove(list.length - 1);
    } else if (e.key === 'ArrowLeft' && box?.selectionStart === 0 && list.length) {
      e.preventDefault();
      chips[list.length - 1]?.focus();
    }
  }
</script>

<div class="chips" class:invalid>
  {#each list as d, i (i)}
    <span class="chip"
      >{String(d)}<button
        type="button"
        tabindex="-1"
        bind:this={chips[i]}
        aria-label="Remove {browsers(String(d))}"
        onclick={() => remove(i)}
        onkeydown={(e) => chipKey(e, i)}>×</button
      ></span
    >
  {/each}
  <input
    {id}
    bind:this={box}
    bind:value={text}
    onkeydown={key}
    onblur={commit}
    inputmode="numeric"
    autocomplete="off"
    placeholder={list.length ? 'add' : 'e.g. 1, 2, 4, 8'}
    aria-describedby={describedby}
    aria-invalid={invalid}
  />
</div>
{#if note}<p class="note" role="status">{note}</p>{/if}

<style>
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    align-items: center;
    padding: 4px;
    border: 1px solid var(--field-border, var(--rule));
    border-radius: var(--radius);
    background: var(--field-bg, var(--bg));
    flex: 1 1 16rem;
    min-width: 0;
    cursor: text;
  }
  .chips:focus-within {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  .chips.invalid {
    border-color: var(--critical);
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 1px 2px 1px 8px;
    border-radius: 999px;
    background: var(--surface-2);
    font-variant-numeric: tabular-nums;
    font-size: 0.9rem;
  }
  .chip button {
    border: 0;
    background: none;
    color: var(--muted);
    cursor: pointer;
    font: inherit;
    padding: 0 5px;
    border-radius: 999px;
  }
  .chip button:focus-visible {
    outline: 2px solid var(--accent);
  }
  .chip button:hover {
    color: var(--critical);
    background: var(--surface);
  }
  input {
    flex: 1 0 4rem;
    min-width: 4rem;
    border: 0 !important;
    outline: none !important;
    background: transparent !important;
    padding: 3px 4px !important;
    font: inherit;
    color: var(--ink);
  }
  .note {
    margin: 4px 0 0;
    font-size: 0.85rem;
    color: var(--critical);
    flex-basis: 100%;
  }
</style>
