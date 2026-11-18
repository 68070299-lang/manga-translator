<script lang="ts">
import { POPUP_FONT_CHOICES, TARGET_LANGUAGES } from '../../ui/choices';
import type { PopupState } from '../../ui/popup/popup-state.svelte';

let { popup }: { popup: PopupState } = $props();
</script>

<section class="section">
  <div class="form-group">
    <label for="target-lang">Target Language</label>
    <select
      id="target-lang"
      class="select"
      value={popup.targetLang}
      onchange={(event) => {
        popup.targetLang = event.currentTarget.value;
        void popup.saveSettings();
      }}
    >
      {#each TARGET_LANGUAGES as language (language)}
        <option value={language}>{language}</option>
      {/each}
    </select>
  </div>
</section>

<section class="section">
  <div class="form-group">
    <label for="font-size">Font Size <span class="value-pill"><span id="font-size-value">{popup.fontSize}</span>px</span></label>
    <input
      type="range"
      id="font-size"
      class="slider"
      min="8"
      max="24"
      value={popup.fontSize}
      oninput={(event) => {
        popup.fontSize = Number(event.currentTarget.value);
      }}
      onchange={(event) => {
        popup.fontSize = Number(event.currentTarget.value);
        void popup.saveSettings();
      }}
    />
  </div>

  <div class="form-group">
    <label for="font-family">Font Family</label>
    <select
      id="font-family"
      class="select"
      value={popup.fontFamily}
      onchange={(event) => {
        popup.fontFamily = event.currentTarget.value;
        void popup.saveSettings();
      }}
    >
      {#each POPUP_FONT_CHOICES as font (font.value)}
        <option value={font.value}>{font.label}</option>
      {/each}
    </select>
  </div>
</section>

<section class="section options">
  <label class="checkbox-label">
    <input
      type="checkbox"
      id="auto-translate"
      checked={popup.autoTranslate}
      onchange={(event) => {
        popup.autoTranslate = event.currentTarget.checked;
        void popup.saveSettings();
      }}
    />
    <span>Auto-translate on load</span>
  </label>
  <label class="checkbox-label">
    <input
      type="checkbox"
      id="show-progress"
      checked={popup.showProgress}
      onchange={(event) => {
        popup.showProgress = event.currentTarget.checked;
        void popup.saveSettings();
      }}
    />
    <span>Show progress overlay</span>
  </label>
</section>
