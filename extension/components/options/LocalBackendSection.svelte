<script lang="ts">
import {
  DETECTOR_CHOICES,
  INPAINTER_CHOICES,
  LOCAL_TRANSLATOR_CHOICES,
  OCR_CHOICES,
} from '../../ui/choices';
import type { OptionsState } from '../../ui/options/options-state.svelte';
import SelectField from './SelectField.svelte';

let { options }: { options: OptionsState } = $props();

const backend = $derived(options.backend);
</script>

<div class="card" id="pipeline">
  <h2>Translation Pipeline</h2>

  <!-- Local Backend Section -->
  <div class="subsection" id="localBackendSection">
    <h3>Local Backend Settings</h3>

    <div class="form-group">
      <label for="backendUrl">Backend URL</label>
      <div class="url-input-group">
        <input type="text" id="backendUrl" placeholder="http://localhost:8001" bind:value={backend.url} />
        <button class="btn btn-secondary" id="testConnectionBtn" disabled={options.testingConnection} onclick={() => options.testConnection()}>Test Connection</button>
      </div>
      <div class="connection-status {options.connection?.type ?? ''}" id="connectionStatus">{options.connection?.message ?? ''}</div>
    </div>

    <div class="form-group">
      <label for="backendAuthToken">Backend Access Token</label>
      <input type="password" id="backendAuthToken" autocomplete="off" bind:value={backend.authToken} />
    </div>

    <div class="form-row">
      <SelectField id="localDetector" label="Detector" choices={DETECTOR_CHOICES} bind:value={backend.detector} />
      <SelectField id="localOcr" label="OCR Model" choices={OCR_CHOICES} bind:value={backend.ocr} />
    </div>

    <SelectField id="localInpainter" label="Inpainter" choices={INPAINTER_CHOICES} bind:value={backend.inpainter} />

    <SelectField id="localTranslator" label="Translator" choices={LOCAL_TRANSLATOR_CHOICES} bind:value={backend.translator} />

    <div id="localLlmSettings" style:display={backend.llmVisible ? 'block' : 'none'}>
      <div class="form-group">
        <label for="localLlmBaseUrl">Local LLM Base URL</label>
        <input type="text" id="localLlmBaseUrl" placeholder="http://localhost:8000/v1" bind:value={backend.llmBaseUrl} />
        <p class="hint">llama.cpp server default: http://localhost:8080/v1, vLLM default: http://localhost:8000/v1</p>
      </div>
      <div class="form-group">
        <label for="localLlmModel">Local LLM Model Name</label>
        <input type="text" id="localLlmModel" placeholder="llama-3.2-3b-instruct (or leave blank for server default)" bind:value={backend.llmModel} />
      </div>
      <div class="form-group">
        <label for="localLlmApiKey">Local LLM API Key (optional)</label>
        <input type="password" id="localLlmApiKey" placeholder="Not needed for most local servers" bind:value={backend.llmApiKey} />
      </div>
    </div>
  </div>
</div>
