import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { browser } from 'wxt/browser';
import App from '../../extension/entrypoints/popup/App.svelte';
import { PROGRESS_STALE_MS } from '../../extension/ui/popup/progress';
import { mockFetch } from '../helpers/fetch';

const defaultSettings = {
  provider: 'gemini',
  targetLang: 'English',
  fontSize: 14,
  fontFamily: 'sans-serif',
  autoTranslate: false,
  showProgress: true,
};

const TAB = { id: 1, url: 'https://example.com/chapter-1' };

/** Let pending storage/message promises and Svelte effects settle. */
async function settle(): Promise<void> {
  for (let turn = 0; turn < 3; turn += 1) {
    if (vi.isFakeTimers()) {
      await vi.advanceTimersByTimeAsync(0);
    } else {
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    flushSync();
  }
}

function el<T extends HTMLElement = HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) throw new Error(`#${id} not found`);
  return element as T;
}

function toastText(): string | undefined {
  return document.querySelector('.toast')?.textContent ?? undefined;
}

async function click(id: string): Promise<void> {
  el(id).click();
  await settle();
}

async function change(id: string, value: string): Promise<void> {
  const field = el<HTMLSelectElement | HTMLInputElement>(id);
  field.value = value;
  field.dispatchEvent(new Event('change', { bubbles: true }));
  await settle();
}

async function setStorage({
  settings = defaultSettings as unknown,
  apiKeys = { apiKey: 'test-key' } as unknown,
  session = {},
} = {}): Promise<void> {
  await browser.storage.sync.set({ settings });
  await browser.storage.local.set({ apiKeys });
  await browser.storage.session.set(session);
}

async function storedSettings(): Promise<Record<string, unknown>> {
  return (await browser.storage.sync.get('settings')).settings as Record<string, unknown>;
}

let tabsSendMessage: ReturnType<
  typeof vi.fn<(tabId: number, message: unknown) => Promise<unknown>>
>;
let app: ReturnType<typeof mount> | undefined;

async function loadPopup(options: Parameters<typeof setStorage>[0] = {}): Promise<void> {
  await setStorage(options);
  const target = document.createElement('div');
  document.body.append(target);
  app = mount(App, { target });
  flushSync();
  await settle();
}

/** Answer every message sent to tab 1 with `handler`'s return value. */
function respondToTabMessages(handler: (message: unknown) => unknown): void {
  tabsSendMessage.mockImplementation(async (_tabId, message) => handler(message));
}

/** Deliver a runtime broadcast as if the content script of `tabId` sent it. */
async function broadcast(message: unknown, tabId: number): Promise<void> {
  const runtime = browser.runtime.onMessage as unknown as {
    trigger(message: unknown, sender: unknown): Promise<unknown>;
  };
  await runtime.trigger(message, { tab: { id: tabId } });
  await settle();
}

beforeEach(() => {
  tabsSendMessage = vi.fn(async (_tabId: number, _message: unknown): Promise<unknown> => undefined);
  Object.assign(browser.tabs, {
    query: vi.fn(async () => [TAB]),
    sendMessage: tabsSendMessage,
  });
  Object.assign(browser.runtime, { openOptionsPage: vi.fn(async () => undefined) });
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  // The status bar and health check are the only network use; default to "offline".
  mockFetch().mockRejectedValue(new Error('offline'));
});

afterEach(() => {
  vi.useRealTimers();
  if (app) unmount(app);
  app = undefined;
});

describe('popup shell', () => {
  it('renders the controls the e2e and smoke tests query', async () => {
    await loadPopup();

    for (const id of [
      'settings-btn',
      'site-toggle-btn',
      'site-toggle-label',
      'connection-indicator',
      'api-warning-section',
      'api-warning-text',
      'api-warning-link',
      'target-lang',
      'font-size',
      'font-size-value',
      'font-family',
      'auto-translate',
      'show-progress',
      'status-section',
      'status-text',
      'progress-fill',
      'translate-btn',
      'translate-visible-btn',
      'translate-region-btn',
      'clear-page-btn',
      'stop-btn',
      'clear-cache-btn',
      'cache-count',
    ]) {
      expect(document.getElementById(id), id).not.toBeNull();
    }
    expect(document.querySelector('[data-testid="brand-mark"]')).not.toBeNull();
    expect(document.querySelector('.manga-strip')).not.toBeNull();
    expect(document.querySelector('.header-title')?.textContent).toBe('Manga Translator');
    expect(Array.from(document.querySelectorAll('.btn span'), (s) => s.textContent)).toEqual([
      'All',
      'Visible',
      'Region',
      'Clear Page',
      'Stop',
      'Clear Cache',
    ]);
    expect(
      Array.from(document.querySelectorAll('#target-lang option'), (o) => o.textContent),
    ).toEqual([
      'English',
      'Spanish',
      'French',
      'German',
      'Portuguese',
      'Italian',
      'Russian',
      'Chinese',
      'Korean',
      'Vietnamese',
      'Thai',
      'Indonesian',
    ]);
  });

  it('shows the saved quick settings', async () => {
    await loadPopup({
      settings: {
        ...defaultSettings,
        targetLang: 'Thai',
        fontSize: 20,
        fontFamily: "'Comic Sans MS', cursive",
        autoTranslate: true,
        showProgress: false,
      },
    });

    expect(el<HTMLSelectElement>('target-lang').value).toBe('Thai');
    expect(el<HTMLInputElement>('font-size').value).toBe('20');
    expect(el('font-size-value').textContent).toBe('20');
    expect(el<HTMLSelectElement>('font-family').value).toBe("'Comic Sans MS', cursive");
    expect(el<HTMLInputElement>('auto-translate').checked).toBe(true);
    expect(el<HTMLInputElement>('show-progress').checked).toBe(false);
  });

  it('opens the options page from the settings button', async () => {
    await loadPopup();

    await click('settings-btn');

    expect(browser.runtime.openOptionsPage).toHaveBeenCalledTimes(1);
  });
});

describe('popup quick settings', () => {
  it('preserves private API keys outside sync storage and notifies the page', async () => {
    const syncSet = vi.spyOn(browser.storage.sync, 'set');
    await loadPopup({
      settings: {
        provider: 'openrouter',
        apiKey: 'legacy-sync-key',
        targetLang: 'English',
        localBackend: { translator: 'none' },
      },
      apiKeys: {},
    });
    syncSet.mockClear();

    await change('target-lang', 'Thai');

    const stored = await storedSettings();
    const local = (await browser.storage.local.get('apiKeys')).apiKeys as { apiKey?: string };
    expect(stored.targetLang).toBe('Thai');
    expect(stored.apiKey).toBeUndefined();
    expect(local.apiKey).toBe('legacy-sync-key');

    expect(tabsSendMessage).toHaveBeenCalledWith(1, {
      type: 'SETTINGS_UPDATED',
      settings: stored,
    });
    expect(JSON.stringify(tabsSendMessage.mock.calls)).not.toContain('legacy-sync-key');
  });

  it('saves font size on change and updates the label while dragging', async () => {
    await loadPopup();

    const slider = el<HTMLInputElement>('font-size');
    slider.value = '18';
    slider.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(el('font-size-value').textContent).toBe('18');

    slider.dispatchEvent(new Event('change', { bubbles: true }));
    await settle();

    expect((await storedSettings()).fontSize).toBe(18);
  });

  it.each([
    ['auto-translate', 'autoTranslate'],
    ['show-progress', 'showProgress'],
  ])('saves the %s checkbox', async (id, key) => {
    await loadPopup();
    const box = el<HTMLInputElement>(id);
    const next = !box.checked;

    box.checked = next;
    box.dispatchEvent(new Event('change', { bubbles: true }));
    await settle();

    expect((await storedSettings())[key]).toBe(next);
  });
});

describe('popup API key warning', () => {
  const isVisible = () => el('api-warning-section').style.display !== 'none';

  it('names the local translator when a key is missing and clears once a key is stored', async () => {
    await loadPopup({
      settings: { provider: 'openrouter', localBackend: { translator: '' } },
      apiKeys: {},
    });

    expect(isVisible()).toBe(true);
    expect(el('api-warning-text').textContent).toContain('Local backend is using openrouter');
    expect(el('api-warning-link').textContent).toBe('Open Settings');

    unmount(app as ReturnType<typeof mount>);
    app = undefined;
    document.body.innerHTML = '';
    await loadPopup({
      settings: { provider: 'openrouter', localBackend: { translator: '' } },
      apiKeys: { apiKey: 'sk-test' },
    });
    expect(isVisible()).toBe(false);
  });

  it('opens the options page from the warning link', async () => {
    await loadPopup({
      settings: { provider: 'openrouter' },
      apiKeys: {},
    });

    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    el('api-warning-link').dispatchEvent(event);
    await settle();

    expect(event.defaultPrevented).toBe(true);
    expect(browser.runtime.openOptionsPage).toHaveBeenCalledTimes(1);
  });
});

describe('popup command workflow', () => {
  it('blocks local translation when the backend URL is missing', async () => {
    await loadPopup({
      settings: {
        ...defaultSettings,
        localBackend: { url: '', translator: 'none' },
      },
      apiKeys: {},
    });

    await click('translate-btn');

    expect(tabsSendMessage).not.toHaveBeenCalled();
    expect(toastText()).toBe('Please configure backend URL in Settings');
    expect(el<HTMLButtonElement>('translate-btn').disabled).toBe(false);
  });

  it('routes TRANSLATE_PAGE to the active tab and recovers the button', async () => {
    respondToTabMessages(() => ({ success: true }));
    await loadPopup();

    await click('translate-btn');

    expect(browser.tabs.query).toHaveBeenCalledWith({ active: true, currentWindow: true });
    expect(tabsSendMessage).toHaveBeenCalledWith(1, { type: 'TRANSLATE_PAGE' });
    expect(toastText()).toBe('Page translation started');
    expect(el<HTMLButtonElement>('translate-btn').disabled).toBe(false);
    expect(el('status-text').textContent).toBe('Starting page translation...');
    expect(el('status-section').style.display).toBe('block');
  });

  it('routes TRANSLATE_VISIBLE_IMAGES to the active tab', async () => {
    respondToTabMessages(() => ({ success: true }));
    await loadPopup();

    await click('translate-visible-btn');

    expect(tabsSendMessage).toHaveBeenCalledWith(1, { type: 'TRANSLATE_VISIBLE_IMAGES' });
    expect(toastText()).toBe('Visible image translation started');
    expect(el('status-text').textContent).toBe('Starting visible image translation...');
  });

  it('starts region selection and closes the popup', async () => {
    const close = vi.spyOn(window, 'close').mockImplementation(() => {});
    respondToTabMessages(() => ({ success: true }));
    await loadPopup();

    await click('translate-region-btn');

    expect(tabsSendMessage).toHaveBeenCalledWith(1, { type: 'START_REGION_SELECTION' });
    expect(toastText()).toBe('Drag over the area to translate');
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('reports a site where translation is disabled', async () => {
    respondToTabMessages(() => ({ success: false, error: 'Translation is disabled on this site' }));
    await loadPopup();

    await click('translate-btn');

    expect(toastText()).toBe('Translation failed: Translation is disabled on this site');
    expect(el<HTMLButtonElement>('translate-btn').disabled).toBe(false);
  });

  it('surfaces a missing content receiver and recovers the button', async () => {
    tabsSendMessage.mockRejectedValueOnce(
      new Error('Could not establish connection. Receiving end does not exist.'),
    );
    await loadPopup();

    await click('translate-btn');

    expect(tabsSendMessage).toHaveBeenCalledWith(1, { type: 'TRANSLATE_PAGE' });
    expect(toastText()).toBe(
      'Translation failed: Could not connect to page. Try refreshing the tab.',
    );
    expect(el<HTMLButtonElement>('translate-btn').disabled).toBe(false);
  });

  it('gives up on a content script that never answers', async () => {
    tabsSendMessage.mockImplementation(() => new Promise(() => {}));
    await loadPopup();
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });

    el('translate-btn').click();
    await vi.advanceTimersByTimeAsync(8000);
    flushSync();

    expect(toastText()).toBe(
      'Translation failed: Could not connect to page. Try refreshing the tab.',
    );
    expect(el<HTMLButtonElement>('translate-btn').disabled).toBe(false);
  });

  it('reports a missing active tab', async () => {
    Object.assign(browser.tabs, { query: vi.fn(async () => []) });
    await loadPopup();

    await click('translate-btn');

    expect(tabsSendMessage).not.toHaveBeenCalled();
    expect(toastText()).toBe('Translation failed: No active tab found');
  });

  it('clears page translations and removes persisted progress', async () => {
    respondToTabMessages((message) => {
      expect(message).toEqual({ type: 'CLEAR_PAGE_TRANSLATIONS' });
      return { cleared: 3 };
    });
    await loadPopup({
      session: {
        translationProgress: {
          status: 'Translating',
          percent: 45,
          tabId: 1,
          timestamp: Date.now(),
        },
      },
    });
    const remove = vi.spyOn(browser.storage.session, 'remove');

    await click('clear-page-btn');

    expect(tabsSendMessage).toHaveBeenCalledWith(1, { type: 'CLEAR_PAGE_TRANSLATIONS' });
    expect(toastText()).toBe('Cleared 3 page translations');
    expect(remove).toHaveBeenCalledWith('translationProgress');
    expect(await browser.storage.session.get('translationProgress')).toEqual({});
    expect(el('status-section').style.display).toBe('none');
    expect(el<HTMLButtonElement>('clear-page-btn').disabled).toBe(false);
  });

  it('routes STOP_TRANSLATION and reports the response', async () => {
    respondToTabMessages((message) => {
      expect(message).toEqual({ type: 'STOP_TRANSLATION' });
      return { stopped: true };
    });
    await loadPopup();

    await click('stop-btn');

    expect(tabsSendMessage).toHaveBeenCalledWith(1, { type: 'STOP_TRANSLATION' });
    expect(toastText()).toBe('Stopping translation');
    expect(el('status-text').textContent).toBe('Stopping after current image...');
    expect(el<HTMLButtonElement>('stop-btn').disabled).toBe(false);
  });

  it('says so when there is nothing to stop', async () => {
    respondToTabMessages(() => ({ stopped: false }));
    await loadPopup();

    await click('stop-btn');

    expect(toastText()).toBe('No translation is running');
  });
});

describe('popup progress broadcasts', () => {
  async function startTranslation(): Promise<void> {
    respondToTabMessages(() => ({ success: true }));
    await loadPopup();
    await click('translate-btn');
  }

  it('accepts progress messages only from the active translation tab', async () => {
    await startTranslation();

    await broadcast({ type: 'TRANSLATION_PROGRESS', status: 'Wrong tab', percent: 90 }, 2);
    expect(el('status-text').textContent).toBe('Starting page translation...');

    await broadcast(
      { type: 'TRANSLATION_PROGRESS', status: 'Translating panel 2', percent: 64 },
      1,
    );

    expect(el('status-text').textContent).toBe('Translating panel 2');
    expect(el('progress-fill').style.width).toBe('64%');
    expect((await browser.storage.session.get('translationProgress')).translationProgress).toEqual(
      expect.objectContaining({ status: 'Translating panel 2', percent: 64, tabId: 1 }),
    );
  });

  it('ignores broadcasts when no translation is tracked', async () => {
    await loadPopup();

    await broadcast({ type: 'TRANSLATION_PROGRESS', status: 'Stray', percent: 50 }, 1);

    expect(el('status-text').textContent).toBe('Ready');
    expect(el('status-section').style.display).toBe('none');
  });

  it('displays terminal completion counts and status', async () => {
    await startTranslation();

    await broadcast(
      { type: 'TRANSLATION_COMPLETE', status: 'partial', total: 3, succeeded: 2, failed: 1 },
      1,
    );

    expect(el('status-text').textContent).toBe('Partial (2/3 images)');
    expect(el('progress-fill').style.width).toBe('100%');
    expect(await browser.storage.session.get('translationProgress')).toEqual({});
  });

  it('displays a complete run without counts', async () => {
    await startTranslation();

    await broadcast({ type: 'TRANSLATION_COMPLETE' }, 1);

    expect(el('status-text').textContent).toBe('Complete');
  });

  it('hides the status two seconds after a run ends', async () => {
    await startTranslation();
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });

    await broadcast(
      { type: 'TRANSLATION_COMPLETE', status: 'complete', total: 1, succeeded: 1 },
      1,
    );
    expect(el('status-section').style.display).toBe('block');

    await vi.advanceTimersByTimeAsync(2000);
    flushSync();

    expect(el('status-section').style.display).toBe('none');
  });

  it('displays stopped terminal state for the active tab', async () => {
    await startTranslation();

    await broadcast({ type: 'TRANSLATION_STOPPED', total: 2, succeeded: 1 }, 1);

    expect(el('status-text').textContent).toBe('Stopped (1/2 images)');
  });

  it('displays failed terminal status for the active tab', async () => {
    await startTranslation();

    await broadcast(
      { type: 'TRANSLATION_COMPLETE', status: 'error', total: 2, succeeded: 0, failed: 2 },
      1,
    );

    expect(el('status-text').textContent).toBe('Failed (0/2 images)');
  });

  it('shows a translation error and hides the status', async () => {
    await startTranslation();

    await broadcast({ type: 'TRANSLATION_ERROR', error: 'backend down' }, 1);

    expect(toastText()).toBe('Error: backend down');
    expect(el('status-section').style.display).toBe('none');
    expect(await browser.storage.session.get('translationProgress')).toEqual({});
  });

  it('refreshes the cache count after a completed run', async () => {
    await startTranslation();
    await browser.storage.local.set({ manga_cache_a: { timestamp: 1 } });

    await broadcast(
      { type: 'TRANSLATION_COMPLETE', status: 'complete', total: 1, succeeded: 1 },
      1,
    );

    expect(el('cache-count').textContent).toBe('1 cached translations');
  });
});

describe('popup progress restore', () => {
  it('keeps fresh work and re-shows it', async () => {
    await loadPopup({
      session: {
        translationProgress: {
          status: 'Rendering...',
          percent: 70,
          tabId: 42,
          timestamp: Date.now(),
        },
      },
    });

    expect(el('status-section').style.display).toBe('block');
    expect(el('status-text').textContent).toBe('Rendering...');
    expect(el('progress-fill').getAttribute('style')).toMatch(/70%/);

    // The restored run keeps reporting for its tab.
    await broadcast({ type: 'TRANSLATION_PROGRESS', status: 'Resumed', percent: 80 }, 42);
    expect(el('status-text').textContent).toBe('Resumed');
  });

  it('drops stale session state', async () => {
    await loadPopup({
      session: {
        translationProgress: {
          status: 'Old work',
          percent: 50,
          tabId: 42,
          timestamp: Date.now() - PROGRESS_STALE_MS - 1000,
        },
      },
    });

    expect(el('status-section').style.display).toBe('none');
    expect(await browser.storage.session.get('translationProgress')).toEqual({});
  });
});

describe('popup site activation', () => {
  it('toggles translation for the active hostname', async () => {
    await loadPopup();
    const localSet = vi.spyOn(browser.storage.local, 'set');
    expect(el('site-toggle-label').textContent).toBe('On');
    expect(el('site-toggle-btn').getAttribute('aria-pressed')).toBe('true');
    expect(el('site-toggle-btn').title).toBe('Disable translation on example.com');

    await click('site-toggle-btn');
    expect(localSet).toHaveBeenCalledWith({ activeUrls: [], disabledUrls: ['example.com'] });
    expect(el('site-toggle-label').textContent).toBe('Off');
    expect(el('site-toggle-btn').getAttribute('aria-pressed')).toBe('false');
    expect(el('site-toggle-btn').title).toBe('Enable translation on example.com');
    expect(el('site-toggle-btn').getAttribute('aria-label')).toBe(
      'Enable translation on example.com',
    );
    expect(toastText()).toBe('Translation disabled on this site');
    expect(tabsSendMessage).toHaveBeenCalledWith(1, { type: 'SITE_ACTIVATION_CHANGED' });

    await click('site-toggle-btn');
    expect(localSet).toHaveBeenLastCalledWith({ activeUrls: [], disabledUrls: [] });
    expect(el('site-toggle-label').textContent).toBe('On');
    expect(toastText()).toBe('Translation enabled on this site');
  });

  it('shows a disabled site as off when the popup opens', async () => {
    await browser.storage.local.set({ disabledUrls: ['example.com'] });

    await loadPopup();

    expect(el('site-toggle-label').textContent).toBe('Off');
  });

  it('reports an unsupported tab URL', async () => {
    Object.assign(browser.tabs, {
      query: vi.fn(async () => [{ id: 1, url: 'chrome://extensions' }]),
    });
    await loadPopup();

    await click('site-toggle-btn');

    expect(toastText()).toContain('Site activation failed:');
    expect(el<HTMLButtonElement>('site-toggle-btn').disabled).toBe(false);
  });
});

describe('popup cache', () => {
  it('counts cache entries in storage, ignoring unrelated keys', async () => {
    await browser.storage.local.set({
      manga_cache_a: { timestamp: 1 },
      manga_cache_b: { timestamp: 1 },
      manga_cache_c: { timestamp: 1 },
      unrelated: { timestamp: 1 },
    });

    await loadPopup();

    expect(el('cache-count').textContent).toBe('3 cached translations');
  });

  it('starts from an empty cache', async () => {
    await loadPopup();

    expect(el('cache-count').textContent).toBe('0 cached translations');
  });

  it('clears every cached translation and the metadata list', async () => {
    await browser.storage.local.set({
      manga_cache_a: { timestamp: 1 },
      manga_cache_b: { timestamp: 1 },
      manga_cache_metadata: ['manga_cache_a', 'manga_cache_b'],
      apiKeys: { apiKey: 'keep-me' },
    });
    await loadPopup({ apiKeys: { apiKey: 'keep-me' } });

    await click('clear-cache-btn');

    expect(toastText()).toBe('Cleared 2 cached translations');
    expect(el('cache-count').textContent).toBe('0 cached translations');
    const left = await browser.storage.local.get(null);
    expect(Object.keys(left).filter((key) => key.startsWith('manga_cache_'))).toEqual([]);
    expect(left.apiKeys).toEqual({ apiKey: 'keep-me' });
  });
});

describe('popup local backend health check', () => {
  const localSettings = (url: string) => ({
    ...defaultSettings,
    localBackend: { url, translator: 'none' },
  });

  const backendLabel = () =>
    document.querySelector('#connection-indicator .status-label')?.textContent;
  const dotClasses = () => document.querySelector('#connection-indicator .status-dot')?.classList;

  it('checks a loopback backend with the normalized health URL and token', async () => {
    const fetchMock = mockFetch().mockResolvedValue({ ok: true, json: async () => ({}) });

    await loadPopup({
      settings: localSettings('http://127.0.0.1:8001/'),
      apiKeys: { backendAuthToken: 'private-backend-token' },
    });

    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8001/health',
      expect.objectContaining({
        method: 'GET',
        headers: { 'X-Manga-Translator-Token': 'private-backend-token' },
      }),
    );
    expect(backendLabel()).toBe('Backend connected');
    expect(dotClasses()).toContain('connected');
  });

  it('sends no token header when none is configured', async () => {
    const fetchMock = mockFetch().mockResolvedValue({ ok: true, json: async () => ({}) });

    await loadPopup({ settings: localSettings('http://localhost:8001'), apiKeys: {} });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.headers).toBeUndefined();
  });

  it('accepts an IPv6 loopback backend URL', async () => {
    const fetchMock = mockFetch().mockResolvedValue({ ok: true, json: async () => ({}) });

    await loadPopup({ settings: localSettings('http://[::1]:8001') });

    expect(fetchMock).toHaveBeenCalledWith(
      'http://[::1]:8001/health',
      expect.objectContaining({ method: 'GET' }),
    );
    expect(backendLabel()).toBe('Backend connected');
  });

  it.each([
    [
      { ok: true, json: async () => ({ status: 'loading', models_loaded: false }) },
      'Backend loading models',
      'pending',
    ],
    [
      { ok: true, json: async () => ({ status: 'degraded', models_loaded: false }) },
      'Backend degraded',
      'pending',
    ],
    [{ ok: false, status: 401 }, 'Backend rejected token', 'disconnected'],
    [{ ok: false, status: 503 }, 'Backend auth not configured', 'disconnected'],
    [{ ok: false, status: 500 }, 'Backend offline', 'disconnected'],
  ])('labels a %j health response as "%s"', async (response, label, dotClass) => {
    mockFetch().mockResolvedValue(response);

    await loadPopup({ settings: localSettings('http://127.0.0.1:8001') });

    expect(backendLabel()).toBe(label);
    expect(dotClasses()).toContain(dotClass);
    expect(dotClasses()).not.toContain('connected');
  });

  it('never sends the auth token to a non-loopback backend URL', async () => {
    const fetchMock = mockFetch().mockResolvedValue({ ok: true, json: async () => ({}) });

    await loadPopup({
      settings: localSettings('http://example.com:8001'),
      apiKeys: { backendAuthToken: 'private-backend-token' },
    });

    expect(fetchMock).not.toHaveBeenCalled();
    expect(backendLabel()).toBe('Backend offline');
    expect(dotClasses()).toContain('disconnected');
  });

  it('renders per-model load states and clears them when the backend goes away', async () => {
    mockFetch().mockResolvedValue({
      ok: true,
      json: async () => ({
        models: {
          detector: { status: 'loaded' },
          ocr: { status: 'loading' },
          paddle_ocr: { status: 'not_loaded' },
          inpainter: { status: 'error' },
        },
      }),
    });

    await loadPopup({ settings: localSettings('http://localhost:8001') });

    expect(document.querySelectorAll('.model-status-list .model-status-item')).toHaveLength(4);
    expect(document.querySelector('.model-status-item.loaded')?.textContent).toContain('Detector');
    expect(document.querySelector('.model-status-item.loading')?.textContent).toContain('OCR');
    expect(document.querySelector('.model-status-item.error')?.textContent).toContain('Inpainter');
    expect(
      Array.from(document.querySelectorAll('.model-status-item'), (item) =>
        Array.from(item.children, (child) => `${child.className}=${child.textContent}`),
      ),
    ).toEqual([
      ['model-status-icon=OK', 'model-status-name=Detector'],
      ['model-status-icon=..', 'model-status-name=OCR'],
      ['model-status-icon=-', 'model-status-name=PaddleOCR'],
      ['model-status-icon=!', 'model-status-name=Inpainter'],
    ]);

    // An offline backend drops the stale list on the next popup load.
    unmount(app as ReturnType<typeof mount>);
    app = undefined;
    document.body.innerHTML = '';
    mockFetch().mockRejectedValue(new Error('offline'));
    await loadPopup({ settings: localSettings('http://localhost:8001') });

    expect(backendLabel()).toBe('Backend offline');
    expect(document.querySelectorAll('.model-status-list')).toHaveLength(0);
  });

  it('renders the offline state when the request fails', async () => {
    mockFetch().mockRejectedValue(new Error('timedout'));

    await loadPopup({ settings: localSettings('http://localhost:8001') });

    expect(backendLabel()).toBe('Backend offline');
    expect(document.querySelectorAll('.model-status-list')).toHaveLength(0);
  });
});
