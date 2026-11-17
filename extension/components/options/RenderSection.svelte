<script lang="ts">
import {
  OPTIONS_FONT_CHOICES,
  TARGET_LANGUAGES,
  TEXT_ALIGNMENT_CHOICES,
  TEXT_DIRECTION_CHOICES,
} from '../../ui/choices';
import { withCurrentValue } from '../../ui/options/forms.svelte';
import type { OptionsState } from '../../ui/options/options-state.svelte';
import CheckboxLabel from './CheckboxLabel.svelte';
import ColorField from './ColorField.svelte';
import SelectField from './SelectField.svelte';
import SliderField from './SliderField.svelte';

let { options }: { options: OptionsState } = $props();

const render = $derived(options.render);

const languageChoices = $derived(
  withCurrentValue(
    TARGET_LANGUAGES.map((language) => ({ value: language, label: language })),
    render.targetLang,
  ),
);
const fontChoices = $derived(withCurrentValue(OPTIONS_FONT_CHOICES, render.fontFamily));

const px = (value: number): string => `${value}px`;
const plain = (value: number): string => String(value);
const percent = (value: number): string => `${Math.round(value * 100)}%`;
</script>

<div class="card" id="render">
  <h2>Render Settings</h2>

  <div class="form-row">
    <SelectField id="targetLang" label="Target Language" choices={languageChoices} bind:value={render.targetLang} />
    <SelectField id="fontFamily" label="Font Family" choices={fontChoices} bind:value={render.fontFamily} />
  </div>

  <SliderField id="fontSize" label="Font Size" min={8} max={24} bind:value={render.fontSize} format={px} />

  <div class="form-row">
    <SliderField id="lineSpacing" label="Line Spacing" min={1} max={2.5} step={0.1} bind:value={render.lineSpacing} format={plain} />
    <SliderField id="borderSize" label="Text Border/Stroke" min={0} max={5} step={0.5} bind:value={render.borderSize} format={px} />
  </div>

  <!-- Color Settings -->
  <div class="subsection">
    <h3>Colors</h3>
    <div class="form-row">
      <ColorField id="textColor" label="Text Color" bind:value={render.textColor} />
      <ColorField id="borderColor" label="Border/Stroke Color" bind:value={render.borderColor} />
    </div>

    <div class="form-row">
      <ColorField id="bgColor" label="Background Color" bind:value={render.bgColor} />
      <SliderField id="bgOpacity" label="Background Opacity" min={0} max={1} step={0.1} bind:value={render.bgOpacity} format={percent} />
    </div>
  </div>

  <!-- Text Effects -->
  <div class="subsection">
    <h3>Text Effects</h3>
    <div class="form-row">
      <div class="form-group">
        <label for="glowEnabled">Text Glow/Halo</label>
        <div class="checkbox-inline">
          <CheckboxLabel id="glowEnabled" label="Enable glow effect" bind:checked={render.glowEnabled} />
        </div>
      </div>

      <SliderField
        id="glowSize"
        label="Glow Size"
        min={1}
        max={10}
        step={1}
        bind:value={render.glowSize}
        format={px}
        disabled={!render.glowEnabled}
        groupId="glowSettingsGroup"
        groupOpacity={render.glowEnabled ? '1' : '0.5'}
      />
    </div>

    <div class="form-row">
      <ColorField id="glowColor" label="Glow Color" bind:value={render.glowColor} />

      <div class="form-group">
        <label for="autoScale">Dynamic Text Scaling</label>
        <div class="checkbox-inline">
          <CheckboxLabel id="autoScale" label="Auto-fit text to bubble" bind:checked={render.autoScale} />
        </div>
      </div>
    </div>
  </div>

  <div class="form-row">
    <SelectField id="textAlignment" label="Text Alignment" choices={TEXT_ALIGNMENT_CHOICES} bind:value={render.textAlignment} />
    <SelectField id="textDirection" label="Text Direction" choices={TEXT_DIRECTION_CHOICES} bind:value={render.textDirection} />
  </div>

  <div class="checkbox-group">
    <CheckboxLabel id="uppercase" label="Uppercase text" bind:checked={render.uppercase} />
  </div>
</div>
