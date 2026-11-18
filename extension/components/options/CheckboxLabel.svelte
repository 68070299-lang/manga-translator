<script lang="ts">
type Props = {
  id: string;
  label: string;
  checked: boolean;
};

let { id, label, checked = $bindable() }: Props = $props();
</script>

<label class="checkbox-label">
  <input type="checkbox" {id} bind:checked />
  <span>{label}</span>
</label>
