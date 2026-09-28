<script lang="ts">
  // What a campaign tested and the SLOs it was held to, in one compact strip under its answer: the host, hypervisor,
  // microVM and densities, where an input that differs between specs is shown per spec and highlighted, then the
  // five SLOs as chips, where one that differs from the standard is highlighted the same way, with the standard
  // beside it (D74). The full spec-by-spec table is further down the page.
  import * as f from '../lib/format';
  import { specColor } from '../lib/colors';
  import { differingRows, sameCriteria, sloChips, specCard, type SpecRef } from '../lib/shape';

  let { refs, replicas }: { refs: SpecRef[]; replicas: number } = $props();

  const diff = $derived(differingRows(refs.map((r) => r.spec)));
  const devices = $derived(new Set(refs.map((r) => `${r.spec.spec.hypervisor.virtio_transport}${r.spec.spec.hypervisor.virtio_rng}`)).size > 1);
  const cards = $derived(refs.map((r) => specCard(r.spec, devices)));
  const densities = (ds: number[]) => (ds.length > 4 ? `${f.num(ds[0])}–${f.num(ds[ds.length - 1])}` : ds.map((d) => f.num(d)).join(', '));

  /** The host kind once after the host, or in each host's chip when they differ (metal beside nested). */
  const kinds = $derived(new Set(refs.map((r) => r.spec.host.host_kind)).size > 1);
  const items = $derived([
    {
      key: 'host',
      label: 'Host',
      values: cards.map((c) => ({ main: c.host.value, sub: kinds ? c.host.sub : c.host.sub.replace(/ · (nested|metal)$/, '') })),
      kind: kinds ? undefined : refs[0]?.spec.host.host_kind,
    },
    { key: 'hypervisor', label: 'Hypervisor', values: cards.map((c) => ({ main: c.hypervisor.value, sub: c.hypervisor.sub?.replace(/ devices$/, '') ?? null })) },
    { key: 'microvm', label: 'MicroVM', values: cards.map((c) => ({ main: c.microvm, sub: null })) },
    {
      key: 'densities',
      label: 'Densities',
      values: cards.map((c) => ({ main: densities(c.densities), sub: c.densities.length > 4 ? `${c.densities.length} steps` : null })),
    },
  ] as const);

  const same = $derived(sameCriteria(refs.map((r) => r.spec)));
</script>

{#snippet slo(c: ReturnType<typeof sloChips>[number])}
  <li class:on={c.standard !== null}>
    <span class="sub">{c.short}</span><b>{c.value}</b>{#if c.standard !== null}<span class="std">standard {c.standard}</span>{/if}
  </li>
{/snippet}

<dl class="strip">
  {#each items as it (it.key)}
    {@const differs = diff.has(it.key)}
    <div class="item" class:differs>
      <dt>{it.label}</dt>
      <dd>
        {#if differs}
          {#each it.values as v, i (i)}
            <span class="chip on"><i class="dot" style:background={specColor(i)}></i><b>{v.main}</b>{#if v.sub}<span class="sub">{v.sub}</span>{/if}</span>
          {/each}
        {:else}
          <span class="chip"><b>{it.values[0].main}</b>{#if it.values[0].sub}<span class="sub">{it.values[0].sub}</span>{/if}</span>
        {/if}
        {#if it.key === 'host' && 'kind' in it && it.kind}<span class="sub kind">{it.kind}</span>{/if}
      </dd>
    </div>
  {/each}
  {#if replicas > 1}
    <div class="item">
      <dt>Replicas</dt>
      <dd><span class="chip"><b>{replicas}</b><span class="sub">hosts per spec</span></span></dd>
    </div>
  {/if}
  <div class="item slo">
    <dt>SLOs</dt>
    <dd>
      {#if same}
        <ul class="slos" aria-label="Success criteria">
          {#each sloChips(refs[0].spec.spec.criteria, true) as c (c.key)}{@render slo(c)}{/each}
        </ul>
      {:else}
        <div class="slo-rows">
          {#each refs as r, i (`${r.campaign}/${r.spec.name}`)}
            <span class="who"><i class="dot" style:background={specColor(i)}></i>{r.groupLabel}</span>
            <ul class="slos" aria-label="Success criteria, {r.groupLabel}">
              {#each sloChips(r.spec.spec.criteria, true) as c (c.key)}{@render slo(c)}{/each}
            </ul>
          {/each}
        </div>
      {/if}
    </dd>
  </div>
</dl>

<style>
  .strip {
    margin: 12px 0 0;
    padding: 8px 14px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px 22px;
    border: 1px solid var(--rule);
    border-radius: 10px;
    background: var(--surface);
  }
  .item {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
  }
  dt {
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--muted);
  }
  dd {
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 4px 6px;
    min-width: 0;
  }
  .chip {
    display: inline-flex;
    align-items: baseline;
    gap: 5px;
    font-size: 0.88rem;
    white-space: nowrap;
  }
  .chip.on {
    padding: 1px 8px;
    border-radius: 6px;
    background: var(--highlight);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 35%, transparent);
  }
  b {
    font-weight: 650;
  }
  .sub {
    font-size: 0.78rem;
    color: var(--ink-2);
  }
  .kind {
    color: var(--muted);
  }
  .dot {
    align-self: center;
    flex: none;
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }
  .slos {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 14px;
    max-width: none;
  }
  .slos li {
    display: inline-flex;
    align-items: baseline;
    gap: 5px;
    font-size: 0.88rem;
    white-space: nowrap;
  }
  .slos li.on {
    padding: 1px 8px;
    border-radius: 6px;
    background: var(--highlight);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 35%, transparent);
  }
  .std {
    font-size: 0.74rem;
    color: var(--muted);
  }
  /* Specs judged by different SLOs: one row each, the spec's name in a column of its own so its chips wrap under
     each other, not under the name. */
  .slo-rows {
    display: grid;
    grid-template-columns: max-content minmax(0, 1fr);
    align-items: baseline;
    gap: 6px 14px;
    min-width: 0;
  }
  .who {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 0.88rem;
    font-weight: 600;
    white-space: nowrap;
  }
  @media (max-width: 600px) {
    .strip {
      gap: 8px 16px;
      padding: 10px 12px;
    }
    .item.slo {
      flex-basis: 100%;
    }
    .slo-rows {
      grid-template-columns: minmax(0, 1fr);
      row-gap: 4px;
    }
    .slo-rows .slos:not(:last-child) {
      margin-bottom: 6px;
    }
  }
</style>
