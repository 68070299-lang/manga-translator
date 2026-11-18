<script lang="ts">
import type { OptionsState } from '../../ui/options/options-state.svelte';
import CheckboxLabel from './CheckboxLabel.svelte';

let { options }: { options: OptionsState } = $props();

const behavior = $derived(options.behavior);
</script>

<div class="card" id="behavior">
  <h2>Behavior</h2>

  <div class="checkbox-group">
    <CheckboxLabel id="autoTranslate" label="Auto-translate images on page load" bind:checked={behavior.autoTranslate} />
    <CheckboxLabel id="showProgress" label="Show progress overlay during translation" bind:checked={behavior.showProgress} />
  </div>
</div>
