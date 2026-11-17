<script lang="ts">
import type { PopupState } from '../../ui/popup/popup-state.svelte';
import BackendStatus from './BackendStatus.svelte';

let { popup }: { popup: PopupState } = $props();
</script>

<section class="section section-flat">
  <div class="field-heading">Pipeline Mode</div>
  <div class="mode-tabs" role="radiogroup" aria-label="Pipeline mode">
    <label class="mode-tab" for="mode-cloud">
      <input
        type="radio"
        name="pipeline-mode"
        id="mode-cloud"
        value="cloud"
        checked={popup.pipelineMode === 'cloud'}
        onchange={() => popup.setPipelineMode('cloud')}
      />
      <span>Cloud API</span>
    </label>
    <label class="mode-tab" for="mode-local">
      <input
        type="radio"
        name="pipeline-mode"
        id="mode-local"
        value="local"
        checked={popup.pipelineMode === 'local'}
        onchange={() => popup.setPipelineMode('local')}
      />
      <span>Local</span>
    </label>
  </div>

  <BackendStatus {popup} />
</section>
