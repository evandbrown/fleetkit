<script lang="ts">
  // A block of text to copy, such as a spec or a campaign definition. Copies with the clipboard API, and falls
  // back to selecting the text so the reader can copy it themselves.
  let { text, label }: { text: string; label: string } = $props();
  let pre: HTMLElement | undefined = $state();
  let status = $state('');

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      status = 'Copied.';
    } catch {
      if (pre) {
        const range = document.createRange();
        range.selectNodeContents(pre);
        const sel = window.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(range);
      }
      status = 'Selected: press Ctrl+C or ⌘C to copy.';
    }
    setTimeout(() => (status = ''), 4000);
  }
</script>

<div class="copy">
  <div class="bar">
    <button type="button" onclick={copy}>Copy {label}</button>
    <span class="status" role="status">{status}</span>
  </div>
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -- focusable so a keyboard reader can scroll it -->
  <pre bind:this={pre} tabindex="0" aria-label={label}><code>{text}</code></pre>
</div>

<style>
  .copy {
    margin: 8px 0 16px;
  }
  .bar {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 6px;
  }
  .status {
    font-size: 0.85rem;
    color: var(--ink-2);
  }
  pre {
    margin: 0;
    max-height: 24rem;
    overflow: auto;
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    padding: 12px;
    font-size: 0.8rem;
    line-height: 1.45;
  }
</style>
