<script lang="ts">
import type { ToastState } from '../ui/toast.svelte';

let { toast }: { toast: ToastState } = $props();
</script>

{#if toast.current}
  {#key toast.current.id}
    <div class="toast {toast.current.type}" class:show={toast.visible}>{toast.current.message}</div>
  {/key}
{/if}
