<script lang="ts">
import { onMount } from 'svelte';
import BehaviorSection from '../../components/options/BehaviorSection.svelte';
import CacheSection from '../../components/options/CacheSection.svelte';
import LocalBackendSection from '../../components/options/LocalBackendSection.svelte';
import OptionsNav from '../../components/options/OptionsNav.svelte';
import ProviderSection from '../../components/options/ProviderSection.svelte';
import RenderSection from '../../components/options/RenderSection.svelte';
import Toast from '../../components/Toast.svelte';
import { OptionsState } from '../../ui/options/options-state.svelte';

const options = new OptionsState();

onMount(() => {
  void options.init();
  return () => options.destroy();
});
</script>

<OptionsNav onsave={() => options.saveSettings()} />

<div class="main-content">
  <div class="page-header">
    <h1>Settings</h1>
    <p>Configure your translation pipeline and preferences</p>
  </div>

  <LocalBackendSection {options} />
  <ProviderSection {options} />
  <RenderSection {options} />
  <BehaviorSection {options} />
  <CacheSection {options} />
</div>

<Toast toast={options.toast} />
