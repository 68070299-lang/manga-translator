<script lang="ts">
import { onMount } from 'svelte';
import ActionButtons from '../../components/popup/ActionButtons.svelte';
import ApiKeyWarning from '../../components/popup/ApiKeyWarning.svelte';
import MangaStrip from '../../components/popup/MangaStrip.svelte';
import PipelineMode from '../../components/popup/PipelineMode.svelte';
import PopupHeader from '../../components/popup/PopupHeader.svelte';
import ProgressBar from '../../components/popup/ProgressBar.svelte';
import QuickSettings from '../../components/popup/QuickSettings.svelte';
import Toast from '../../components/Toast.svelte';
import { PopupState } from '../../ui/popup/popup-state.svelte';

const popup = new PopupState();

onMount(() => {
  void popup.init();
  return () => popup.destroy();
});
</script>

<main class="popup-container">
  <PopupHeader {popup} />
  <MangaStrip />
  <PipelineMode {popup} />
  <ApiKeyWarning {popup} />
  <QuickSettings {popup} />
  <ProgressBar {popup} />
  <ActionButtons {popup} />

  <footer class="cache-stats" id="cache-stats">
    <span id="cache-count">{popup.cacheText}</span>
  </footer>
</main>

<Toast toast={popup.toast} />
