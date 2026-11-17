<script lang="ts">
import type { OptionsState } from '../../ui/options/options-state.svelte';

let { options }: { options: OptionsState } = $props();
</script>

<div class="card" id="cache">
  <h2>Cache Statistics</h2>

  <div class="stats">
    <div class="stat-card">
      <div class="stat-value" id="cacheCount">{options.cacheCount}</div>
      <div class="stat-label">Cached Translations</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" id="cacheSize">{options.cacheSize}</div>
      <div class="stat-label">Cache Size</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" id="cacheAge">{options.cacheAge}</div>
      <div class="stat-label">Oldest Entry</div>
    </div>
  </div>

  <div class="actions" style="margin-top: 20px;">
    <button class="btn btn-secondary" id="resetBtn" onclick={() => options.resetSettings()}>Reset to Defaults</button>
    <button class="btn btn-danger" id="clearCacheBtn" onclick={() => options.clearCache()}>Clear All Cache</button>
  </div>
</div>
