<script lang="ts">
type Props = {
  id: string;
  label: string;
  min: number;
  max: number;
  step?: number;
  value: number;
  /** Text shown next to the slider for the current value. */
  format: (value: number) => string;
  disabled?: boolean;
  /** Optional id and opacity for the wrapping `.form-group`. */
  groupId?: string;
  groupOpacity?: string;
};

let {
  id,
  label,
  min,
  max,
  step = 1,
  value = $bindable(),
  format,
  disabled = false,
  groupId,
  groupOpacity,
}: Props = $props();
</script>

<div class="form-group" id={groupId} style:opacity={groupOpacity}>
  <label for={id}>{label}</label>
  <div class="slider-container">
    <input type="range" {id} {min} {max} {step} {disabled} bind:value />
    <span class="slider-value" id="{id}Value">{format(value)}</span>
  </div>
</div>
