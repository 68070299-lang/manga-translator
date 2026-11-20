import { afterEach, beforeEach } from 'vitest';
import { fakeBrowser } from 'wxt/testing/fake-browser';

type StorageAreaExtras = {
  getKeys?: () => Promise<string[]>;
  getBytesInUse?: (keys?: string | string[] | null) => Promise<number>;
};

/**
 * fake-browser stubs `storage.local.getKeys` and `getBytesInUse` so they throw.
 * Real Chrome (130+) implements both, so give the fake working versions.
 * Tests simulating an older browser can `delete` them.
 */
function installStorageExtras(): void {
  const area = fakeBrowser.storage.local as unknown as StorageAreaExtras;
  area.getKeys = async () => Object.keys(await fakeBrowser.storage.local.get(null));
  area.getBytesInUse = async (keys?: string | string[] | null) => {
    const all = await fakeBrowser.storage.local.get(null);
    const names = keys == null ? Object.keys(all) : Array.isArray(keys) ? keys : [keys];
    return names.reduce(
      (total, name) =>
        name in all ? total + name.length + JSON.stringify(all[name] ?? null).length : total,
      0,
    );
  };
}

// Each test starts with empty fake extension storage/listeners.
beforeEach(() => {
  fakeBrowser.reset();
  installStorageExtras();
});

afterEach(() => {
  document.body.innerHTML = '';
  document.head.innerHTML = '';
});
