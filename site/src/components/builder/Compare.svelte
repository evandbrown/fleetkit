<script lang="ts">
  // "Vary this" input: pick the values, and the builder makes one spec per value, each changing only
  // this input. The value the base already has becomes the spec that runs the base.
  import { TYPES, same, suggestName, type Field, type Json, type Obj } from '../../lib/campaign';
  import { optionLabel, type CompareOptions } from './draft';

  let {
    field,
    base,
    specCount,
    onapply,
    onclose,
  }: {
    field: Field;
    base: Obj;
    specCount: number;
    onapply: (values: Json[], opts: CompareOptions) => void;
    onclose: () => void;
  } = $props();

  const path = $derived(field.path);
  const baseValue = $derived(path.split('.').reduce<unknown>((o, k) => (o as Obj)?.[k], base) as Json);
  const opts = $derived(
    (field.schema.enum as Json[] | undefined) ?? (field.schema.type === 'boolean' ? [false, true] : null),
  );

  // Starting picks: the base's value and its neighbour; for a number, the base's value and double it.
  function initialPicks(): Json[] {
    if (opts) {
      const i = Math.max(0, opts.findIndex((o) => same(o, baseValue)));
      return [opts[i], opts[i > 0 ? i - 1 : Math.min(1, opts.length - 1)]];
    }
    return [];
  }
  function initialText(): string {
    const b = baseValue as number;
    const max = field.schema.maximum ?? Infinity;
    return typeof b === 'number' ? `${b}, ${b * 2 <= max ? b * 2 : Math.floor(b / 2)}` : '';
  }
  let picks: Json[] = $state(initialPicks());
  let text = $state(initialText());
  let scale = $state(true);
  let matchDevices = $state(true);

  const values = $derived.by((): Json[] => {
    if (opts) return opts.filter((o) => picks.some((p) => same(p, o)));
    const out: number[] = [];
    for (const t of text.split(/[\s,;]+/).filter(Boolean)) {
      const n = Number(t.replace(/_/g, ''));
      if (Number.isFinite(n) && !out.includes(n)) out.push(n);
    }
    return out;
  });
  const names = $derived(values.map((v) => suggestName(path, v)));
  const ready = $derived(values.length >= 2 && values.length <= 12);
  const baseHv = $derived((base.hypervisor as Obj | undefined) ?? {});
  const devicesDiffer = $derived(baseHv.virtio_transport !== 'pci' || baseHv.virtio_rng !== true);
  const hostVcpusDiffer = $derived(
    path === 'worker_host.instance_type' &&
      new Set(values.map((v) => TYPES[String(v)]?.vcpus).filter(Boolean)).size > 1,
  );

  function toggle(o: Json, on: boolean) {
    picks = on ? [...picks, o] : picks.filter((p) => !same(p, o));
  }

  function apply() {
    if (ready) onapply(values, { scale: scale && hostVcpusDiffer, matchDevices: matchDevices && devicesDiffer });
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -- Escape anywhere in the panel closes it -->
<div
  class="compare"
  role="group"
  aria-label="Vary {field.label}"
  onkeydown={(e) => {
    if (e.key === 'Escape') onclose();
  }}
>
  <p class="head"><strong>Vary {field.label}</strong> <span class="muted">one spec per value</span></p>
  {#if opts}
    <div class="opts">
      {#each opts as o (String(o))}
        <label>
          <input type="checkbox" checked={picks.some((p) => same(p, o))} onchange={(e) => toggle(o, e.currentTarget.checked)} />
          {optionLabel(path, o)}{#if same(o, baseValue)}<span class="muted"> (base)</span>{/if}
        </label>
      {/each}
    </div>
  {:else}
    <label class="vals">
      Values{field.unit ? ` (${field.unit})` : ''}
      <input
        placeholder="e.g. {initialText()}"
        bind:value={text}
        inputmode="decimal"
        autocomplete="off"
        onkeydown={(e) => {
          if (e.key === 'Enter') apply();
        }}
      />
    </label>
  {/if}

  {#if path === 'worker_host.instance_type' && hostVcpusDiffer}
    <label class="opt"><input type="checkbox" bind:checked={scale} /> Scale browser counts per host vCPU</label>
  {/if}
  {#if path === 'hypervisor.name' && values.includes('cloud-hypervisor') && devicesDiffer}
    <label class="opt">
      <input type="checkbox" bind:checked={matchDevices} /> Same devices in every spec (PCI, random-number device)
    </label>
  {/if}

  <div class="preview">
    {#if ready}
      {#each names as n, i (i)}<code class="chip">{n}{#if same(values[i], baseValue)}<span class="muted"> · base</span>{/if}</code>{/each}
      {#if specCount}<span class="muted">replaces {specCount === 1 ? '1 spec' : `${specCount} specs`}</span>{/if}
    {:else}
      <span class="muted">Pick {values.length > 12 ? 'at most 12' : 'at least 2'} values</span>
    {/if}
  </div>
  <div class="buttons">
    <button type="button" class="primary" disabled={!ready} onclick={apply}>Make {ready ? values.length : ''} specs</button>
    <button type="button" class="link" onclick={onclose}>Cancel</button>
  </div>
</div>

<style>
  .compare {
    margin: 0 0 12px;
    padding: 12px 14px;
    border: 1px solid var(--accent);
    border-radius: var(--radius);
    background: var(--highlight);
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 10px;
    align-items: baseline;
    margin: 0 0 8px;
  }
  .head .muted {
    font-size: 0.85rem;
  }
  .opts {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(100%, 15rem), 1fr));
    gap: 4px 16px;
  }
  .opts label,
  .opt {
    display: flex;
    gap: 8px;
    align-items: baseline;
    font-size: 0.92rem;
  }
  .opt {
    margin-top: 10px;
  }
  .vals {
    display: grid;
    gap: 4px;
    font-size: 0.92rem;
    max-width: 24rem;
  }
  .preview {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    font-size: 0.88rem;
    margin: 12px 0 10px;
  }
  .chip {
    padding: 1px 8px;
    border-radius: 999px;
    background: var(--bg);
    border: 1px solid var(--rule);
    font-size: 0.8rem;
  }

  .buttons {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 16px;
    font-size: 0.9rem;
  }
</style>
