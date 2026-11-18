<script lang="ts">
import type { PopupState } from '../../ui/popup/popup-state.svelte';

let { popup }: { popup: PopupState } = $props();

const MODEL_NAMES: Record<string, string> = {
  detector: 'Detector',
  ocr: 'OCR',
  paddle_ocr: 'PaddleOCR',
  inpainter: 'Inpainter',
};
const MODEL_STATUS_ICONS: Record<string, string> = {
  loaded: 'OK',
  loading: '..',
  error: '!',
  not_loaded: '-',
};
</script>

<div
  id="connection-indicator"
  class="connection-indicator"
  style:display={popup.backendVisible ? 'flex' : 'none'}
  aria-live="polite"
>
  <span class="status-dot {popup.backendState}"></span>
  <span class="status-label">{popup.backendLabel}</span>
  {#if popup.backendModels}
    <div class="model-status-list">
      {#each Object.entries(popup.backendModels) as [key, info] (key)}
        <div class="model-status-item {info.status}">
          <span class="model-status-icon">{MODEL_STATUS_ICONS[info.status] || '-'}</span>
          <span class="model-status-name">{MODEL_NAMES[key] || key}</span>
        </div>
      {/each}
    </div>
  {/if}
</div>
