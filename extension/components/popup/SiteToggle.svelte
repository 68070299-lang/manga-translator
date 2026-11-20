<script lang="ts">
import type { PopupState } from '../../ui/popup/popup-state.svelte';

let { popup }: { popup: PopupState } = $props();
</script>

<button
  id="site-toggle-btn"
  class="settings-btn site-toggle-btn"
  title={popup.siteTitle}
  aria-label={popup.siteTitle}
  aria-pressed={popup.siteEnabled}
  disabled={popup.siteBusy}
  onclick={() => popup.toggleSite()}
>
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M4 5h16v14H4z"></path>
    <path d="M8 9h8"></path>
    <path d="M8 13h5"></path>
  </svg>
  <span id="site-toggle-label">{popup.siteEnabled ? 'On' : 'Off'}</span>
</button>
