<script lang="ts">
  // The cloud setup, drawn once: AWS, the VPC, the worker host with its microVMs, the support host, the results
  // bucket. Numbered callouts match the component list below it. Wide screens get the diagram left to right; below
  // 600 px it is stacked top to bottom, so nothing hides off the side.
  let width = $state(0);
  const stacked = $derived(width > 0 && width < 600);

  const LAYERS = ['Chromium', 'guest daemon', 'Linux guest'];
  const LABEL =
    'AWS us-east-1. Inside a VPC, a worker host runs a driver, a host daemon and a hypervisor, and N microVMs, each with a Linux guest, the guest daemon and Chromium. A support host serves the test shopping site the browsers load over HTTP and collects telemetry from the host daemon. The driver writes results to an S3 bucket.';

  // Wide: the worker host's microVM slots. Stacked: three narrower slots.
  const WIDE = [
    { x: 172, label: 'microVM 1' },
    { x: 280, label: 'microVM 2' },
    { x: 388, label: null },
    { x: 496, label: 'microVM N' },
  ];
  const TALL = [
    { x: 32, label: 'microVM 1' },
    { x: 136, label: null },
    { x: 240, label: 'microVM N' },
  ];
</script>

{#snippet callout(n: number, cx: number, cy: number)}
  <g class="callout" aria-hidden="true">
    <circle {cx} {cy} r="9" />
    <text x={cx} y={cy + 4} text-anchor="middle">{n}</text>
  </g>
{/snippet}

{#snippet defs(id: string)}
  <defs>
    <marker {id} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" class="head" />
    </marker>
  </defs>
{/snippet}

<figure class="arch" bind:clientWidth={width}>
  {#if stacked}
    <svg viewBox="0 0 360 610" role="img" aria-label={LABEL}>
      {@render defs('arch-arrow-s')}
      <rect class="region" x="1" y="1" width="358" height="608" rx="12" />
      <text class="t-area" x="14" y="22">AWS <tspan class="t-muted">us-east-1</tspan></text>
      <rect class="vpc" x="10" y="34" width="340" height="506" rx="10" />
      <text class="t-area" x="22" y="54">VPC</text>

      <rect class="host" x="20" y="64" width="320" height="318" rx="10" />
      <text class="t-host" x="32" y="86">Worker host</text>
      <text class="t-sub" x="32" y="102">EC2 · nested or metal</text>
      {@render callout(1, 326, 80)}

      <rect class="box" x="32" y="114" width="136" height="42" rx="8" />
      <text class="t-name" x="100" y="132" text-anchor="middle">Driver</text>
      <text class="t-sub" x="100" y="148" text-anchor="middle">runs the procedure</text>
      <rect class="box" x="192" y="114" width="136" height="42" rx="8" />
      <text class="t-name" x="260" y="132" text-anchor="middle">Host daemon</text>
      <text class="t-sub" x="260" y="148" text-anchor="middle">starts, samples, stops</text>
      {@render callout(4, 326, 114)}
      <line class="wire" x1="168" y1="135" x2="190" y2="135" marker-end="url(#arch-arrow-s)" />

      <polyline class="wire" points="260,156 260,168" />
      <line class="wire" x1="76" y1="168" x2="284" y2="168" />
      {#each TALL as s (s.x)}
        <line class="wire" x1={s.x + 44} y1="168" x2={s.x + 44} y2="180" marker-end="url(#arch-arrow-s)" />
        {#if s.label}
          <rect class="vm" x={s.x} y="182" width="88" height="120" rx="8" />
          <text class="t-vm" x={s.x + 44} y="199" text-anchor="middle">{s.label}</text>
          {#each LAYERS as layer, i (layer)}
            <rect class="layer" x={s.x + 6} y={206 + i * 30} width="76" height="24" rx="5" />
            <text class="t-layer" x={s.x + 44} y={222 + i * 30} text-anchor="middle">{layer}</text>
          {/each}
        {:else}
          <rect class="ghost" x={s.x} y="182" width="88" height="120" rx="8" />
          <text class="t-dots" x={s.x + 44} y="240" text-anchor="middle">…</text>
          <text class="t-sub" x={s.x + 44} y="258" text-anchor="middle">N = browsers</text>
          <text class="t-sub" x={s.x + 44} y="272" text-anchor="middle">per host</text>
        {/if}
      {/each}
      {@render callout(2, 32, 182)}
      {@render callout(5, 32, 218)}
      {@render callout(3, 32, 278)}

      <rect class="bar" x="32" y="312" width="296" height="26" rx="6" />
      <text class="t-bar" x="180" y="329" text-anchor="middle">Hypervisor: Firecracker or Cloud Hypervisor</text>
      <rect class="bar base" x="32" y="344" width="296" height="26" rx="6" />
      <text class="t-bar" x="180" y="361" text-anchor="middle">Amazon Linux 2023 · KVM</text>

      <line class="wire" x1="100" y1="382" x2="100" y2="468" marker-end="url(#arch-arrow-s)" />
      <text class="t-label" x="106" y="408">HTTP</text>
      <line class="wire" x1="260" y1="382" x2="260" y2="468" marker-end="url(#arch-arrow-s)" />
      <text class="t-label" x="266" y="408">telemetry</text>

      <rect class="host" x="20" y="420" width="320" height="110" rx="10" />
      <text class="t-host" x="32" y="442">Support host</text>
      <text class="t-sub" x="150" y="442">separate EC2 instance</text>
      <rect class="box" x="32" y="470" width="136" height="46" rx="8" />
      <text class="t-name" x="100" y="490" text-anchor="middle">Test shopping site</text>
      <text class="t-sub" x="100" y="506" text-anchor="middle">what tasks shop on</text>
      <rect class="box" x="192" y="470" width="136" height="46" rx="8" />
      <text class="t-name" x="260" y="490" text-anchor="middle">Telemetry</text>
      <text class="t-sub" x="260" y="506" text-anchor="middle">metrics, traces, logs</text>

      <polyline class="wire" points="32,135 15,135 15,573 108,573" marker-end="url(#arch-arrow-s)" />
      <rect class="box" x="110" y="552" width="140" height="42" rx="8" />
      <text class="t-name" x="180" y="570" text-anchor="middle">Results</text>
      <text class="t-sub" x="180" y="586" text-anchor="middle">S3 bucket</text>
    </svg>
  {:else}
    <svg viewBox="0 0 960 496" role="img" aria-label={LABEL}>
      {@render defs('arch-arrow')}
      <rect class="region" x="1" y="1" width="958" height="494" rx="14" />
      <text class="t-area" x="20" y="28">AWS <tspan class="t-muted">us-east-1</tspan></text>

      <rect class="box" x="20" y="142" width="100" height="48" rx="8" />
      <text class="t-name" x="70" y="162" text-anchor="middle">Results</text>
      <text class="t-sub" x="70" y="179" text-anchor="middle">S3 bucket</text>

      <rect class="vpc" x="140" y="44" width="804" height="432" rx="12" />
      <text class="t-area" x="156" y="67">VPC</text>

      <rect class="host" x="156" y="80" width="456" height="380" rx="10" />
      <text class="t-host" x="172" y="106">Worker host</text>
      <text class="t-sub" x="172" y="124">EC2 · nested or metal</text>
      {@render callout(1, 598, 96)}

      <rect class="box" x="172" y="142" width="130" height="48" rx="8" />
      <text class="t-name" x="237" y="162" text-anchor="middle">Driver</text>
      <text class="t-sub" x="237" y="179" text-anchor="middle">runs the procedure</text>

      <rect class="box" x="358" y="142" width="130" height="48" rx="8" />
      <text class="t-name" x="423" y="162" text-anchor="middle">Host daemon</text>
      <text class="t-sub" x="423" y="179" text-anchor="middle">starts, samples, stops</text>
      {@render callout(4, 488, 142)}

      <line class="wire" x1="302" y1="166" x2="356" y2="166" marker-end="url(#arch-arrow)" />
      <text class="t-label" x="329" y="158" text-anchor="middle">trials</text>
      <line class="wire" x1="172" y1="166" x2="122" y2="166" marker-end="url(#arch-arrow)" />

      <polyline class="wire" points="423,190 423,212" />
      <line class="wire" x1="222" y1="212" x2="546" y2="212" />
      {#each WIDE as s (s.x)}
        <line class="wire" x1={s.x + 50} y1="212" x2={s.x + 50} y2="234" marker-end="url(#arch-arrow)" />
      {/each}
      <text class="t-label" x="431" y="205">start · task · stop</text>

      {#each WIDE as s (s.x)}
        {#if s.label}
          <rect class="vm" x={s.x} y="236" width="100" height="132" rx="8" />
          <text class="t-vm" x={s.x + 50} y="256" text-anchor="middle">{s.label}</text>
          {#each LAYERS as layer, i (layer)}
            <rect class="layer" x={s.x + 8} y={266 + i * 32} width="84" height="26" rx="5" />
            <text class="t-layer" x={s.x + 50} y={283 + i * 32} text-anchor="middle">{layer}</text>
          {/each}
        {:else}
          <rect class="ghost" x={s.x} y="236" width="100" height="132" rx="8" />
          <text class="t-dots" x={s.x + 50} y="298" text-anchor="middle">…</text>
          <text class="t-sub" x={s.x + 50} y="318" text-anchor="middle">N = browsers</text>
          <text class="t-sub" x={s.x + 50} y="332" text-anchor="middle">per host</text>
        {/if}
      {/each}
      {@render callout(2, 172, 236)}
      {@render callout(5, 172, 279)}
      {@render callout(3, 172, 343)}

      <rect class="bar" x="172" y="380" width="424" height="28" rx="6" />
      <text class="t-bar" x="384" y="398" text-anchor="middle">Hypervisor: Firecracker or Cloud Hypervisor</text>
      <rect class="bar base" x="172" y="416" width="424" height="28" rx="6" />
      <text class="t-bar" x="384" y="434" text-anchor="middle">Amazon Linux 2023 · KVM</text>

      <rect class="host" x="644" y="80" width="284" height="288" rx="10" />
      <text class="t-host" x="660" y="106">Support host</text>
      <text class="t-sub" x="660" y="124">separate EC2 instance</text>

      <rect class="box" x="660" y="142" width="252" height="48" rx="8" />
      <text class="t-name" x="786" y="162" text-anchor="middle">Telemetry</text>
      <text class="t-sub" x="786" y="179" text-anchor="middle">metrics, traces, logs</text>

      <rect class="box" x="660" y="278" width="252" height="48" rx="8" />
      <text class="t-name" x="786" y="298" text-anchor="middle">Test shopping site</text>
      <text class="t-sub" x="786" y="315" text-anchor="middle">what every task shops on</text>

      <line class="wire" x1="488" y1="166" x2="658" y2="166" marker-end="url(#arch-arrow)" />
      <text class="t-label" x="550" y="158" text-anchor="middle">telemetry</text>

      <line class="wire" x1="596" y1="302" x2="658" y2="302" marker-end="url(#arch-arrow)" />
      <text class="t-label" x="628" y="294" text-anchor="middle">HTTP</text>
    </svg>
  {/if}
</figure>

<style>
  .arch {
    margin: 20px 0 0;
    min-width: 0;
  }
  svg {
    display: block;
    width: 100%;
    height: auto;
    font-family: var(--font);
  }
  .region {
    fill: none;
    stroke: var(--axis);
  }
  .vpc {
    fill: none;
    stroke: var(--axis);
    stroke-dasharray: 5 4;
  }
  .host {
    fill: var(--surface);
    stroke: var(--rule);
  }
  .box {
    fill: var(--bg);
    stroke: var(--field-border);
  }
  .vm {
    fill: var(--bg);
    stroke: var(--ink-2);
  }
  .ghost {
    fill: none;
    stroke: var(--ink-2);
    stroke-dasharray: 4 3;
  }
  .layer {
    fill: var(--surface-2);
  }
  .bar {
    fill: var(--surface-2);
  }
  .bar.base {
    fill: var(--rule);
  }
  .wire {
    fill: none;
    stroke: var(--ink-2);
    stroke-width: 1.25;
  }
  .head {
    fill: var(--ink-2);
  }
  text {
    fill: var(--ink);
    font-size: 12px;
  }
  .t-area {
    font-size: 13px;
    font-weight: 650;
    fill: var(--ink-2);
  }
  .t-muted {
    font-weight: 400;
    fill: var(--muted);
  }
  .t-host {
    font-size: 15px;
    font-weight: 650;
  }
  .t-name {
    font-size: 13px;
    font-weight: 600;
  }
  .t-sub {
    font-size: 11.5px;
    fill: var(--ink-2);
  }
  .t-vm {
    font-size: 12px;
    font-weight: 650;
  }
  .t-layer {
    font-size: 11.5px;
  }
  .t-bar {
    font-size: 12px;
    fill: var(--ink-2);
  }
  .t-label {
    font-size: 11px;
    fill: var(--ink-2);
  }
  .t-dots {
    font-size: 24px;
    fill: var(--ink-2);
  }
  .callout circle {
    fill: var(--ink);
  }
  .callout text {
    fill: var(--bg);
    font-size: 11px;
    font-weight: 700;
  }
</style>
