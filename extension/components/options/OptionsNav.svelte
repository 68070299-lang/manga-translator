<script lang="ts">
type Props = {
  onsave: () => void;
};

let { onsave }: Props = $props();

const SECTIONS = [
  { id: 'pipeline', label: 'Pipeline' },
  { id: 'translate-api', label: 'Translate API' },
  { id: 'google-ocr', label: 'Google OCR' },
  { id: 'render', label: 'Render' },
  { id: 'behavior', label: 'Behavior' },
  { id: 'cache', label: 'Cache' },
] as const;

let activeSection = $state<string>('pipeline');

function goTo(event: MouseEvent, id: string): void {
  event.preventDefault();
  const target = document.getElementById(id);
  if (target) {
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    activeSection = id;
  }
}

/** The last section whose top has scrolled past the navbar is the active one. */
function trackScroll(): void {
  let current = '';
  for (const { id } of SECTIONS) {
    const section = document.getElementById(id);
    if (section && section.offsetTop - 100 <= window.scrollY) {
      current = id;
    }
  }
  activeSection = current;
}
</script>

<svelte:window onscroll={trackScroll} />

<nav class="navbar">
  <!-- svelte-ignore a11y_invalid_attribute -->
  <a href="#" class="navbar-brand">
    <span class="navbar-brand-mark" aria-hidden="true">
      <svg viewBox="0 0 40 40">
        <path d="M20 5l3.3 4.3 5.4-1.1-1.1 5.3 4.4 3.2-5 2.4.5 5.6-5-2.3-4.6 3.2-.2-5.6-4.4-3.4 5-2.1L20 5z"></path>
        <path d="M15.3 18.7c1.2-1.8 2.8-2.8 4.7-2.8s3.5 1 4.7 2.8c1.1 1.7 1.1 3.8.1 5.7-1 1.8-2.8 3.3-4.8 4.6-2-1.3-3.8-2.8-4.8-4.6-1-1.9-1-4 .1-5.7z"></path>
        <path d="M17.1 20.8h.1M22.8 20.8h.1M18.3 24.4c1 .7 2.4.7 3.4 0"></path>
      </svg>
    </span>
    Manga Translator
  </a>
  <div class="navbar-nav">
    {#each SECTIONS as section (section.id)}
      <a
        href="#{section.id}"
        class="nav-link"
        class:active={activeSection === section.id}
        data-section={section.id}
        onclick={(event) => goTo(event, section.id)}
      >
        {section.label}
      </a>
    {/each}
  </div>
  <div class="navbar-end">
    <button class="btn btn-primary" id="saveBtn" onclick={onsave}>Save Settings</button>
  </div>
</nav>
