<script lang="ts">
import { PROVIDER_CHOICES } from '../../ui/choices';
import type { OptionsState } from '../../ui/options/options-state.svelte';
import SecretInput from './SecretInput.svelte';
import SelectField from './SelectField.svelte';

let { options }: { options: OptionsState } = $props();

const provider = $derived(options.provider);
</script>

<div class="card" id="translate-api">
  <h2>Translate API Settings</h2>

  <div class="form-row">
    <SelectField id="provider" label="Provider" choices={PROVIDER_CHOICES} bind:value={provider.provider} />

    <div class="form-group">
      <label for="model">Model</label>
      <input type="text" id="model" placeholder="Leave blank to use backend .env" bind:value={provider.model} />
    </div>
  </div>

  <div class="form-group">
    <label for="apiKey">API Key</label>
    <SecretInput id="apiKey" placeholder="Enter your API key" bind:value={provider.apiKey} />
    <p class="hint">For local backend mode, leave API key and model blank to use backend/.env values such as OPENROUTER_API_KEY and OPENROUTER_MODEL.</p>
    <p id="apiKeyWarning" class="warning" style:display={options.apiKeyWarningVisible ? 'block' : 'none'}>A Translate API provider is selected but no API key is configured. Set an API key or configure it in the backend's .env file.</p>
  </div>

  <!-- The custom provider was removed, so its base URL is never editable. -->
  <div class="form-group" id="baseUrlGroup" style="display: none;">
    <label for="baseUrl">Base URL (Custom Provider)</label>
    <input type="text" id="baseUrl" placeholder="https://api.example.com/v1" bind:value={provider.baseUrl} />
  </div>
</div>
