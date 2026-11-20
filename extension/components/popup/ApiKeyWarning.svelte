<script lang="ts">
import type { PopupState } from '../../ui/popup/popup-state.svelte';

let { popup }: { popup: PopupState } = $props();

function openSettings(event: MouseEvent): void {
  event.preventDefault();
  popup.openSettings();
}
</script>

<section
  class="section warning-section"
  id="api-warning-section"
  style:display={popup.warningVisible ? 'block' : 'none'}
  aria-live="polite"
>
  <!-- The link keeps its original `#` target; the click handler opens the options page instead. -->
  <p id="api-warning-text" class="warning-text">{popup.warningText}<!-- svelte-ignore a11y_invalid_attribute --><a href="#" id="api-warning-link" onclick={openSettings}>Open Settings</a></p>
</section>
