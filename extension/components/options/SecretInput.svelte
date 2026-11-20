<script lang="ts">
type Props = {
  id: string;
  value: string;
  placeholder?: string;
};

let { id, value = $bindable(), placeholder }: Props = $props();

let revealed = $state(false);
</script>

<div class="input-with-toggle">
  <input
    type={revealed ? 'text' : 'password'}
    {id}
    {placeholder}
    {value}
    oninput={(event) => {
      value = event.currentTarget.value;
    }}
  />
  <button
    type="button"
    class="eye-btn"
    data-target={id}
    aria-label="Toggle API key visibility"
    onclick={() => {
      revealed = !revealed;
    }}
  >
    <svg class="eye-open" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style:display={revealed ? 'none' : 'block'}>
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
      <circle cx="12" cy="12" r="3"></circle>
    </svg>
    <svg class="eye-closed" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style:display={revealed ? 'block' : 'none'}>
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"></path>
      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"></path>
      <line x1="1" y1="1" x2="23" y2="23"></line>
    </svg>
  </button>
</div>
