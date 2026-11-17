<script lang="ts">
import { GOOGLE_LANGUAGE_HINT_CHOICES } from '../../ui/choices';
import type { OptionsState } from '../../ui/options/options-state.svelte';
import SecretInput from './SecretInput.svelte';

let { options }: { options: OptionsState } = $props();

const google = $derived(options.googleOcr);
</script>

<div class="card" id="google-ocr">
  <h2>Google OCR Settings</h2>

  <div class="form-group">
    <label for="googleOcrApiKey">Google Cloud Vision API Key</label>
    <SecretInput id="googleOcrApiKey" placeholder="Enter your Google Cloud Vision API key" bind:value={google.apiKey} />
    <p class="hint">Get your API key from <a href="https://console.cloud.google.com/apis/credentials" target="_blank">Google Cloud Console</a>. Enable Cloud Vision API first.</p>
    <p id="googleOcrKeyWarning" class="warning" style:display={options.googleKeyWarningVisible ? 'block' : 'none'}>The google-ocr model is selected but no Google Cloud Vision API key is configured. The backend rejects google-ocr requests without a key.</p>
  </div>

  <div class="form-group">
    <label for="googleOcrLanguageHint">Language Hint (optional)</label>
    <select
      id="googleOcrLanguageHint"
      value={google.languageHint}
      onchange={(event) => {
        google.languageHint = event.currentTarget.value;
      }}
    >
      {#each GOOGLE_LANGUAGE_HINT_CHOICES as choice (choice.value)}
        <option value={choice.value}>{choice.label}</option>
      {/each}
    </select>
    <p class="hint">Hint to improve OCR accuracy for specific languages</p>
  </div>
</div>
