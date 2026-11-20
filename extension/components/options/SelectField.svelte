<script lang="ts">
import type { Choice } from '../../ui/choices';

type Props = {
  id: string;
  label: string;
  choices: readonly Choice[];
  value: string;
};

let { id, label, choices, value = $bindable() }: Props = $props();
</script>

<div class="form-group">
  <label for={id}>{label}</label>
  <select
    {id}
    {value}
    onchange={(event) => {
      value = event.currentTarget.value;
    }}
  >
    {#each choices as choice (choice.value)}
      <option value={choice.value}>{choice.label}</option>
    {/each}
  </select>
</div>
