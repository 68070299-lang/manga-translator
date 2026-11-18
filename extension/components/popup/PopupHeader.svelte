<script lang="ts">
import type { PopupState } from '../../ui/popup/popup-state.svelte';
import SiteToggle from './SiteToggle.svelte';

let { popup }: { popup: PopupState } = $props();
</script>

<header class="header">
  <div class="brand-lockup">
    <div class="brand-mark" data-testid="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 40 40" role="img">
        <path d="M20 5l3.3 4.3 5.4-1.1-1.1 5.3 4.4 3.2-5 2.4.5 5.6-5-2.3-4.6 3.2-.2-5.6-4.4-3.4 5-2.1L20 5z"></path>
        <path d="M15.3 18.7c1.2-1.8 2.8-2.8 4.7-2.8s3.5 1 4.7 2.8c1.1 1.7 1.1 3.8.1 5.7-1 1.8-2.8 3.3-4.8 4.6-2-1.3-3.8-2.8-4.8-4.6-1-1.9-1-4 .1-5.7z"></path>
        <path d="M17.1 20.8h.1M22.8 20.8h.1M18.3 24.4c1 .7 2.4.7 3.4 0"></path>
      </svg>
    </div>
    <div>
      <p class="brand-eyebrow">Reader controls</p>
      <h1 class="header-title">Manga Translator</h1>
    </div>
  </div>
  <SiteToggle {popup} />
  <button id="settings-btn" class="settings-btn" title="Open settings" aria-label="Open settings" onclick={() => popup.openSettings()}>
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3"></circle>
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9c.14.45.14 1 0 1.45H21a2 2 0 0 1 0 4h-1.6z"></path>
    </svg>
  </button>
</header>
