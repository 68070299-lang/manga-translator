<script lang="ts">
import { untrack } from 'svelte';

type Props = {
  id: string;
  label: string;
  /** The colour as `#rrggbb`; the hex box below is a text view of the same value. */
  value: string;
};

let { id, label, value = $bindable() }: Props = $props();

const HEX_COLOR = /^#[0-9A-Fa-f]{6}$/;

// What is typed in the hex box. It follows `value` unless the user is mid-edit
// on a string that does not (yet) describe a colour.
let hex = $state('');

$effect.pre(() => {
  const current = value;
  untrack(() => {
    if (hex.toLowerCase() !== current.toLowerCase()) hex = current;
  });
});

function onColorInput(event: Event & { currentTarget: HTMLInputElement }): void {
  value = event.currentTarget.value;
  hex = value;
}

function onHexInput(event: Event & { currentTarget: HTMLInputElement }): void {
  hex = event.currentTarget.value;
  if (HEX_COLOR.test(hex)) value = hex.toLowerCase();
}

function onHexBlur(): void {
  let candidate = hex.trim();
  if (!candidate.startsWith('#')) candidate = `#${candidate}`;
  if (HEX_COLOR.test(candidate)) {
    hex = candidate.toUpperCase();
    value = candidate.toLowerCase();
  } else {
    hex = value;
  }
}
</script>

<div class="form-group">
  <label for={id}>{label}</label>
  <div class="color-input-group">
    <input type="color" {id} {value} oninput={onColorInput} />
    <input
      type="text"
      id="{id}Hex"
      value={hex}
      maxlength="7"
      style="width: 80px;"
      oninput={onHexInput}
      onblur={onHexBlur}
    />
  </div>
</div>
