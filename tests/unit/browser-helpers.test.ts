import { describe, expect, it, vi } from 'vitest';
import { browser } from 'wxt/browser';
import { handleRuntimeMessage } from '../../extension/background/message-router';
import { notifyPopup, sendToTab, sendToWorker } from '../../extension/shared/messages';
import { setDeclarativeNetRequest, stubWorkerBrowserApis } from '../helpers/browser-stubs';
import { mockFetch } from '../helpers/fetch';

// These cover the test helpers the other suites rely on (replacement for the
// removed Jest chrome-mock fidelity tests).

describe('fake browser storage helpers', () => {
  it('lists keys and measures bytes in use like Chrome', async () => {
    await browser.storage.local.set({ a: { x: 1 }, b: 'text' });

    const local = browser.storage.local as unknown as {
      getKeys(): Promise<string[]>;
      getBytesInUse(keys?: string | string[] | null): Promise<number>;
    };
    await expect(local.getKeys()).resolves.toEqual(['a', 'b']);
    await expect(local.getBytesInUse(['a'])).resolves.toBe(
      'a'.length + JSON.stringify({ x: 1 }).length,
    );
    await expect(local.getBytesInUse(['missing'])).resolves.toBe(0);
    await expect(local.getBytesInUse()).resolves.toBeGreaterThan(0);
  });

  it('starts every test with empty storage', async () => {
    await expect(browser.storage.local.get(null)).resolves.toEqual({});
    await expect(browser.storage.sync.get(null)).resolves.toEqual({});
  });

  it('stores cloned values', async () => {
    const original = { nested: { count: 1 } };
    await browser.storage.local.set({ item: original });
    original.nested.count = 2;

    const read = await browser.storage.local.get('item');
    expect((read.item as typeof original).nested.count).toBe(1);
  });
});

describe('mockFetch', () => {
  it('fills the Response members tests leave out', async () => {
    mockFetch().mockResolvedValue({ ok: true });

    const response = await fetch('https://example.test');

    expect(response.ok).toBe(true);
    expect(response.headers.get('x-request-id')).toBeNull();
    await expect(response.json()).rejects.toThrow();
    await expect(response.text()).resolves.toBe('');
  });

  it('keeps provided members and derives a failing status', async () => {
    mockFetch().mockResolvedValue({ ok: false, text: async () => 'nope' });

    const response = await fetch('https://example.test');

    expect(response.status).toBe(500);
    await expect(response.text()).resolves.toBe('nope');
  });
});

describe('worker browser stubs', () => {
  it('replaces the throwing fake-browser APIs and drops declarativeNetRequest', () => {
    const stubs = stubWorkerBrowserApis();

    expect(browser.declarativeNetRequest).toBeUndefined();
    browser.contextMenus.create({ id: 'x', title: 'x' });
    expect(stubs.contextMenusCreate).toHaveBeenCalledWith({ id: 'x', title: 'x' });

    const updateSessionRules = vi.fn();
    setDeclarativeNetRequest({ updateSessionRules });
    expect(browser.declarativeNetRequest.updateSessionRules).toBe(updateSessionRules);
    setDeclarativeNetRequest(undefined);
  });
});

describe('typed message helpers', () => {
  it('sendToWorker resolves with the worker handler response', async () => {
    browser.runtime.onMessage.addListener(handleRuntimeMessage);

    await expect(
      sendToWorker({ type: 'FETCH_IMAGE', url: 'ftp://example.test/a.png' }),
    ).resolves.toEqual({
      success: false,
      error: 'Unsupported URL scheme: ftp',
    });
  });

  it('sendToTab forwards the message to that tab', async () => {
    const { tabsSendMessage } = stubWorkerBrowserApis();
    tabsSendMessage.mockResolvedValue({ success: true, stopped: false });

    await expect(sendToTab(9, { type: 'STOP_TRANSLATION' })).resolves.toEqual({
      success: true,
      stopped: false,
    });
    expect(tabsSendMessage).toHaveBeenCalledWith(9, { type: 'STOP_TRANSLATION' });
  });

  it('notifyPopup resolves even when nothing is listening', async () => {
    vi.spyOn(browser.runtime, 'sendMessage').mockRejectedValue(
      new Error('Could not establish connection. Receiving end does not exist.'),
    );

    await expect(
      notifyPopup({ type: 'TRANSLATION_PROGRESS', status: 'x', percent: 1 }),
    ).resolves.toBeUndefined();
  });
});
