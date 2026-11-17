import { afterEach, beforeEach } from 'vitest';
import { fakeBrowser } from 'wxt/testing/fake-browser';

// Each test starts with empty fake extension storage/listeners.
beforeEach(() => {
  fakeBrowser.reset();
});

afterEach(() => {
  document.body.innerHTML = '';
  document.head.innerHTML = '';
});
