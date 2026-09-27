<script lang="ts">
  import { loadIndex } from '../lib/data';
  import { href } from '../lib/router';
  import * as f from '../lib/format';
  import Failed from '../components/Failed.svelte';
  import Term from '../components/Term.svelte';

  const index = loadIndex();
</script>

<h1>Campaigns</h1>
<p class="lead">Each <Term k="campaign" /> is a set of runs launched together to answer one question.</p>

{#await index}
  <p class="status">Loading…</p>
{:then i}
  {#if i.campaigns.length === 0}
    <p>Nothing has been published yet.</p>
  {:else}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Campaign</th>
            <th>Question</th>
            <th><Term k="tested_successfully" text="Tested successfully" />, by spec</th>
            <th class="num">Specs × replicas</th>
            <th class="num">Started</th>
          </tr>
        </thead>
        <tbody>
          {#each i.campaigns as c (c.id)}
            <tr>
              <td>
                <a href={href({ name: 'campaign', campaign: c.id })}>{c.title}</a>
                {#if c.synthetic}<span class="tag">synthetic</span>{/if}
                {#if c.before_campaigns}<span class="tag">a campaign of one</span>{/if}
              </td>
              <td>{c.question}</td>
              <td>
                {#each c.outcomes as o (o.spec)}
                  {@const s = f.spread(o.tested_successfully)}
                  <div class="outcome">
                    <span class="muted">{c.specs.find((x) => x.name === o.spec)?.label ?? o.spec}:</span>
                    <strong>{s ? f.range(s) : 'none'}</strong>
                  </div>
                {/each}
              </td>
              <td class="num">{c.specs.length} × {c.replicas} = {c.runs}</td>
              <td class="num">{f.when(c.started)}{c.status === 'partial' ? ', partial' : ''}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
{:catch e}
  <Failed error={e} />
{/await}

<style>
  .outcome {
    white-space: nowrap;
  }
  .tag {
    display: inline-block;
    font-size: 0.75rem;
    color: var(--ink-2);
    border: 1px solid var(--rule);
    border-radius: 4px;
    padding: 0 5px;
    margin-left: 6px;
  }
</style>
