<script lang="ts">
import type { PopupState } from '../../ui/popup/popup-state.svelte';

let { popup }: { popup: PopupState } = $props();
</script>

{#snippet trashIcon()}
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M3 6h18"></path>
    <path d="M8 6V4h8v2"></path>
    <path d="M19 6l-1 14H6L5 6"></path>
    <path d="M10 11v5"></path>
    <path d="M14 11v5"></path>
  </svg>
{/snippet}

<section class="actions">
  <button id="translate-btn" class="btn btn-primary" disabled={popup.busy.translate} onclick={() => popup.translatePage()}>
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M21 12a9 9 0 0 1-9 9 9.6 9.6 0 0 1-6.8-2.8"></path>
      <path d="M3 12a9 9 0 0 1 9-9 9.6 9.6 0 0 1 6.8 2.8"></path>
      <path d="M21 3v6h-6"></path>
      <path d="M3 21v-6h6"></path>
    </svg>
    <span>All</span>
  </button>
  <button id="translate-visible-btn" class="btn btn-secondary" disabled={popup.busy.translateVisible} onclick={() => popup.translateVisibleImages()}>
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M4 5h16v14H4z"></path>
      <path d="M8 9h8"></path>
      <path d="M8 13h5"></path>
    </svg>
    <span>Visible</span>
  </button>
  <button id="translate-region-btn" class="btn btn-secondary" disabled={popup.busy.translateRegion} onclick={() => popup.translateRegion()}>
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="M4 8V4h4"></path>
      <path d="M16 4h4v4"></path>
      <path d="M20 16v4h-4"></path>
      <path d="M8 20H4v-4"></path>
    </svg>
    <span>Region</span>
  </button>
  <button id="clear-page-btn" class="btn btn-secondary" disabled={popup.busy.clearPage} onclick={() => popup.clearPageTranslations()}>
    {@render trashIcon()}
    <span>Clear Page</span>
  </button>
  <button id="stop-btn" class="btn btn-secondary" disabled={popup.busy.stop} onclick={() => popup.stopTranslation()}>
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <rect x="6" y="6" width="12" height="12"></rect>
    </svg>
    <span>Stop</span>
  </button>
  <button id="clear-cache-btn" class="btn btn-secondary" disabled={popup.busy.clearCache} onclick={() => popup.clearCache()}>
    {@render trashIcon()}
    <span>Clear Cache</span>
  </button>
</section>
