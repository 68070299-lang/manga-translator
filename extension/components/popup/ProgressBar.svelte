<script lang="ts">
import type { PopupState } from '../../ui/popup/popup-state.svelte';

let { popup }: { popup: PopupState } = $props();
</script>

<section
  class="section status-section"
  id="status-section"
  style:display={popup.statusVisible ? 'block' : 'none'}
  aria-live="polite"
>
  <div class="status-bar">
    <div class="status-text" id="status-text">{popup.statusText}</div>
    <div class="progress-bar">
      <div class="progress-fill" id="progress-fill" style:width="{popup.percent}%"></div>
    </div>
  </div>
</section>
