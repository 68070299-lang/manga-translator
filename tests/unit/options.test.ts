import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { browser } from 'wxt/browser';
import App from '../../extension/entrypoints/options/App.svelte';
import { BUNDLED_FONTS } from '../../extension/lib/font-registry';
import { DEFAULT_SETTINGS, SETTINGS_VERSION } from '../../extension/lib/settings';
import type { ApiKeys, PublicSettings } from '../../extension/shared/types';
import { mockFetch } from '../helpers/fetch';

let app: ReturnType<typeof mount> | undefined;

/** Let pending storage/fetch promises and Svelte effects settle. */
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

type Field = HTMLInputElement | HTMLSelectElement;

function el<T extends HTMLElement = HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) throw new Error(`#${id} not found`);
  return element as T;
}

function field(id: string): Field {
  return el<Field>(id);
}

/** Edit a control the way a user would: set it, then fire the event its binding listens to. */
function setField(id: string, value: string | boolean): void {
  const control = field(id);
  if (control instanceof HTMLInputElement && control.type === 'checkbox') {
    control.checked = Boolean(value);
    control.dispatchEvent(new Event('change', { bubbles: true }));
    return;
  }
  control.value = String(value);
  control.dispatchEvent(
    new Event(control instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }),
  );
}

async function click(id: string): Promise<void> {
  el(id).click();
  await settle();
}

async function loadOptions({
  settings,
  apiKeys,
}: {
  settings?: unknown;
  apiKeys?: unknown;
} = {}): Promise<void> {
  if (settings !== undefined) await browser.storage.sync.set({ settings });
  if (apiKeys !== undefined) await browser.storage.local.set({ apiKeys });
  const target = document.createElement('div');
  document.body.append(target);
  app = mount(App, { target });
  flushSync();
  await settle();
}

/** Stored settings, including the legacy key fields a test may expect to be absent. */
type StoredSettings = Omit<PublicSettings, 'localLlm' | 'localBackend'> & {
  apiKey?: string;
  localBackend: PublicSettings['localBackend'] & { authToken?: string };
  localLlm?: PublicSettings['localLlm'] & { apiKey?: string };
};

async function storedSettings(): Promise<StoredSettings> {
  return (await browser.storage.sync.get('settings')).settings as StoredSettings;
}

async function storedApiKeys(): Promise<ApiKeys> {
  return (await browser.storage.local.get('apiKeys')).apiKeys as ApiKeys;
}

function optionValues(selectId: string): string[] {
  return Array.from(el<HTMLSelectElement>(selectId).options, (option) => option.value);
}

function toastText(): string | undefined {
  return document.querySelector('.toast')?.textContent ?? undefined;
}

beforeEach(() => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  if (app) unmount(app);
  app = undefined;
});

describe('options page layout', () => {
  it('renders every control the e2e tests query', async () => {
    await loadOptions();

    for (const id of [
      'saveBtn',
      'resetBtn',
      'clearCacheBtn',
      'testConnectionBtn',
      'connectionStatus',
      'localBackendSection',
      'backendUrl',
      'backendAuthToken',
      'localDetector',
      'localOcr',
      'localInpainter',
      'localTranslator',
      'localLlmSettings',
      'localLlmBaseUrl',
      'localLlmModel',
      'localLlmApiKey',
      'provider',
      'model',
      'apiKey',
      'apiKeyWarning',
      'baseUrlGroup',
      'baseUrl',
      'targetLang',
      'fontFamily',
      'fontSize',
      'fontSizeValue',
      'lineSpacing',
      'lineSpacingValue',
      'borderSize',
      'borderSizeValue',
      'textColor',
      'textColorHex',
      'borderColor',
      'borderColorHex',
      'bgColor',
      'bgColorHex',
      'bgOpacity',
      'bgOpacityValue',
      'glowEnabled',
      'glowSize',
      'glowSizeValue',
      'glowColor',
      'glowColorHex',
      'glowSettingsGroup',
      'autoScale',
      'textAlignment',
      'textDirection',
      'uppercase',
      'autoTranslate',
      'showProgress',
      'cacheCount',
      'cacheSize',
      'cacheAge',
      'pipeline',
      'translate-api',
      'render',
      'behavior',
      'cache',
    ]) {
      expect(document.getElementById(id), id).not.toBeNull();
    }
    expect(document.querySelectorAll('.eye-btn')).toHaveLength(1);
    expect(
      Array.from(document.querySelectorAll('.nav-link'), (a) => a.getAttribute('data-section')),
    ).toEqual(['pipeline', 'translate-api', 'render', 'behavior', 'cache']);
    expect(el('baseUrlGroup').style.display).toBe('none');
  });

  it('only offers backend-supported local pipeline choices', async () => {
    await loadOptions();

    expect({
      detectors: optionValues('localDetector'),
      ocr: optionValues('localOcr'),
      inpainters: optionValues('localInpainter'),
      translators: optionValues('localTranslator'),
    }).toEqual({
      detectors: ['default', 'rtdetr', 'animetext-yolo'],
      ocr: ['manga-ocr', 'paddle-ocr'],
      inpainters: ['flux2_klein_lanpaint', 'flux2_klein_q4', 'lama_large', 'none'],
      translators: ['', 'none', 'gemini', 'openai', 'openrouter', 'local'],
    });
  });

  it('offers the provider, target language, and text direction choices', async () => {
    await loadOptions();

    expect(optionValues('provider')).toEqual(['gemini', 'openai', 'openrouter', 'local']);
    expect(optionValues('textAlignment')).toEqual(['left', 'center', 'right']);
    expect(optionValues('textDirection')).toEqual(['auto', 'horizontal', 'vertical']);
    expect(optionValues('targetLang')).toHaveLength(12);
  });

  it('offers every bundled font in the font family select', async () => {
    await loadOptions();

    const values = optionValues('fontFamily');
    for (const font of BUNDLED_FONTS) {
      expect(values).toContain(font.cssValue);
    }
  });

  it('keeps a stored custom font selectable instead of dropping it', async () => {
    await loadOptions({ settings: { fontFamily: 'Georgia, serif', targetLang: 'Japanese' } });

    expect(field('fontFamily').value).toBe('Georgia, serif');
    expect(field('targetLang').value).toBe('Japanese');

    await click('saveBtn');
    expect((await storedSettings()).fontFamily).toBe('Georgia, serif');
    expect((await storedSettings()).targetLang).toBe('Japanese');
  });

  it('shows the defaults on first run', async () => {
    await loadOptions();

    expect(field('provider').value).toBe(DEFAULT_SETTINGS.provider);
    expect(field('backendUrl').value).toBe('http://localhost:8001');
    expect(field('localDetector').value).toBe('default');
    expect(field('localOcr').value).toBe('manga-ocr');
    expect(field('localInpainter').value).toBe('flux2_klein_lanpaint');
    expect(field('localTranslator').value).toBe('');
    expect(field('targetLang').value).toBe('English');
    expect(field('fontFamily').value).toBe("'CC Wild Words', sans-serif");
    expect(field('fontSize').value).toBe('16');
    expect(el('fontSizeValue').textContent).toBe('16px');
    expect(el('lineSpacingValue').textContent).toBe('1.2');
    expect(el('borderSizeValue').textContent).toBe('2px');
    expect(el('bgOpacityValue').textContent).toBe('0%');
    expect(el('glowSizeValue').textContent).toBe('3px');
    expect(field('textAlignment').value).toBe('center');
    expect(field('textColor').value).toBe('#000000');
    expect(field('textColorHex').value).toBe('#000000');
    expect((field('autoScale') as HTMLInputElement).checked).toBe(true);
    expect((field('showProgress') as HTMLInputElement).checked).toBe(true);
    expect((field('autoTranslate') as HTMLInputElement).checked).toBe(false);
  });
});

describe('options page navigation', () => {
  it('scrolls to a section and marks its link active', async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView as unknown as Element['scrollIntoView'];
    await loadOptions();

    const link = document.querySelector<HTMLAnchorElement>('.nav-link[data-section="render"]');
    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    link?.dispatchEvent(event);
    flushSync();

    expect(event.defaultPrevented).toBe(true);
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });
    expect(
      Array.from(document.querySelectorAll('.nav-link.active'), (a) => a.textContent?.trim()),
    ).toEqual(['Render']);
  });

  it('starts on the pipeline section and follows the scroll position', async () => {
    await loadOptions();
    expect(document.querySelector('.nav-link.active')?.getAttribute('data-section')).toBe(
      'pipeline',
    );

    window.dispatchEvent(new Event('scroll'));
    flushSync();

    // No layout in the test DOM, so every section counts as scrolled past; the last one wins.
    expect(
      Array.from(document.querySelectorAll('.nav-link.active'), (a) =>
        a.getAttribute('data-section'),
      ),
    ).toEqual(['cache']);
  });
});

describe('options page settings persistence', () => {
  it('stamps the current settings version on a fresh save', async () => {
    await loadOptions({ apiKeys: {} });

    await click('saveBtn');

    expect((await storedSettings()).settingsVersion).toBe(SETTINGS_VERSION);
  });

  it('trims pasted whitespace from model ids before saving', async () => {
    await loadOptions({ apiKeys: {} });
    setField('model', '  google/gemini-2.0-flash \n');
    setField('localLlmModel', ' qwen2.5-7b-instruct\n');

    await click('saveBtn');

    const settings = await storedSettings();
    expect(settings.model).toBe('google/gemini-2.0-flash');
    expect(settings.localLlm?.model).toBe('qwen2.5-7b-instruct');
  });

  it('saves every field of the form', async () => {
    await loadOptions({ apiKeys: {} });
    setField('provider', 'gemini');
    setField('model', 'gemini-flash');
    setField('apiKey', 'sk-browser');
    setField('backendUrl', 'http://localhost:9000');
    setField('backendAuthToken', 'token-1');
    setField('localDetector', 'rtdetr');
    setField('localOcr', 'paddle-ocr');
    setField('localInpainter', 'none');
    setField('localTranslator', 'local');
    setField('localLlmBaseUrl', 'http://localhost:11434/v1');
    setField('localLlmApiKey', 'llm-key');
    setField('targetLang', 'Thai');
    setField('fontFamily', 'sans-serif');
    setField('fontSize', '20');
    setField('textAlignment', 'left');
    setField('textDirection', 'vertical');
    setField('uppercase', true);
    setField('lineSpacing', '1.5');
    setField('borderSize', '3.5');
    setField('textColor', '#112233');
    setField('borderColor', '#223344');
    setField('bgColor', '#334455');
    setField('bgOpacity', '0.7');
    setField('glowEnabled', true);
    setField('glowSize', '6');
    setField('glowColor', '#445566');
    setField('autoScale', false);
    setField('autoTranslate', true);
    setField('showProgress', false);

    await click('saveBtn');

    const settings = await storedSettings();
    expect(settings).toMatchObject({
      provider: 'gemini',
      model: 'gemini-flash',
      targetLang: 'Thai',
      fontSize: 20,
      fontFamily: 'sans-serif',
      autoTranslate: true,
      showProgress: false,
      localBackend: {
        url: 'http://localhost:9000',
        detector: 'rtdetr',
        ocr: 'paddle-ocr',
        inpainter: 'none',
        translator: 'local',
      },
      localLlm: { baseUrl: 'http://localhost:11434/v1' },
      render: {
        textAlignment: 'left',
        textDirection: 'vertical',
        uppercase: true,
        lineSpacing: 1.5,
        borderSize: 3.5,
        textColor: '#112233',
        borderColor: '#223344',
        bgColor: '#334455',
        bgOpacity: 0.7,
        glowEnabled: true,
        glowSize: 6,
        glowColor: '#445566',
        autoScale: false,
      },
    });
    expect(await storedApiKeys()).toEqual({
      apiKey: 'sk-browser',
      localLlmApiKey: 'llm-key',
      backendAuthToken: 'token-1',
    });
    // Keys and the backend token never reach sync storage.
    expect(JSON.stringify(settings)).not.toMatch(/sk-browser|llm-key|token-1/);
  });

  it('migrates legacy LaMa storage while loading the options page', async () => {
    await loadOptions({
      settings: {
        localBackend: { detector: 'animetext-yolo', inpainter: 'lama_large' },
      },
    });

    expect(await storedSettings()).toEqual({
      settingsVersion: SETTINGS_VERSION,
      fontSize: 16,
      localBackend: { detector: 'animetext-yolo', inpainter: 'flux2_klein_lanpaint' },
    });
    expect(field('localInpainter').value).toBe('flux2_klein_lanpaint');
  });

  it('rejects non-loopback backend URLs during save', async () => {
    await loadOptions({
      settings: { localBackend: { url: 'http://localhost:8001' } },
    });
    const syncSet = vi.spyOn(browser.storage.sync, 'set');
    const localSet = vi.spyOn(browser.storage.local, 'set');
    setField('backendUrl', 'https://example.com');

    await click('saveBtn');

    expect(syncSet).not.toHaveBeenCalled();
    expect(localSet).not.toHaveBeenCalled();
    expect(toastText()).toContain('Only http://localhost');
  });

  it('accepts an IPv6 loopback backend URL during save', async () => {
    await loadOptions({
      settings: { localBackend: { url: 'http://localhost:8001' } },
      apiKeys: {},
    });
    setField('backendUrl', 'http://[::1]:8001');

    await click('saveBtn');

    expect((await storedSettings()).localBackend.url).toBe('http://[::1]:8001');
  });

  it('persists the backend auth token privately when saving options', async () => {
    await loadOptions({
      settings: { localBackend: { url: 'http://localhost:8001' } },
      apiKeys: {},
    });
    setField('backendUrl', 'http://127.0.0.1:8001');
    setField('backendAuthToken', 'private-backend-token');

    await click('saveBtn');

    expect((await storedSettings()).localBackend.authToken).toBeUndefined();
    expect((await storedApiKeys()).backendAuthToken).toBe('private-backend-token');
  });

  it('loads the private backend auth token into the options form', async () => {
    await loadOptions({
      settings: { localBackend: { url: 'http://localhost:8001' } },
      apiKeys: { backendAuthToken: 'private-backend-token' },
    });

    expect(field('backendAuthToken').value).toBe('private-backend-token');
  });

  it('migrates legacy synced API keys into private local storage', async () => {
    await loadOptions({
      settings: {
        provider: 'openrouter',
        apiKey: 'legacy-sync-key',
        localLlm: { apiKey: 'legacy-local-key' },
      },
    });
    expect(field('apiKey').value).toBe('legacy-sync-key');
    expect(field('localLlmApiKey').value).toBe('legacy-local-key');

    await click('saveBtn');

    const settings = await storedSettings();
    expect(settings.apiKey).toBeUndefined();
    expect(settings.localLlm?.apiKey).toBeUndefined();
    expect(await storedApiKeys()).toEqual({
      apiKey: 'legacy-sync-key',
      localLlmApiKey: 'legacy-local-key',
    });
  });

  it('does not resurrect synced API keys after local keys are cleared', async () => {
    await loadOptions({
      settings: {
        provider: 'openrouter',
        apiKey: 'old-sync-key',
        localLlm: { apiKey: 'old-local-key' },
      },
      apiKeys: { apiKey: '', localLlmApiKey: '' },
    });

    expect(field('apiKey').value).toBe('');
    expect(field('localLlmApiKey').value).toBe('');
  });

  it('preserves blank local translator inheritance', async () => {
    await loadOptions({
      settings: { provider: 'openrouter', localBackend: { translator: '' } },
    });
    expect(field('localTranslator').value).toBe('');

    await click('saveBtn');

    expect((await storedSettings()).localBackend.translator).toBe('');
  });

  it('shows Distilled LanPaint instead of a blank selector for malformed storage', async () => {
    await loadOptions({ settings: { localBackend: { inpainter: 'removed-model' } } });

    expect(field('localInpainter').value).toBe('flux2_klein_lanpaint');
  });

  it('offers the plain FLUX.2 Klein Q4 inpainter and keeps it selected', async () => {
    expect(DEFAULT_SETTINGS.settingsVersion).toBe(SETTINGS_VERSION);
    await loadOptions({
      settings: {
        settingsVersion: SETTINGS_VERSION,
        localBackend: { inpainter: 'flux2_klein_q4' },
      },
    });
    expect(optionValues('localInpainter')).toContain('flux2_klein_q4');
    expect(field('localInpainter').value).toBe('flux2_klein_q4');

    await click('saveBtn');

    expect((await storedSettings()).localBackend.inpainter).toBe('flux2_klein_q4');
  });

  it('shows the default OCR instead of a blank selector for malformed storage', async () => {
    await loadOptions({ settings: { localBackend: { ocr: 'removed-ocr' } } });

    expect(field('localOcr').value).toBe('manga-ocr');
  });

  it('keeps a stored paddle-ocr selection when saving', async () => {
    await loadOptions({ settings: { localBackend: { ocr: 'paddle-ocr' } } });
    expect(field('localOcr').value).toBe('paddle-ocr');

    await click('saveBtn');

    expect((await storedSettings()).localBackend.ocr).toBe('paddle-ocr');
  });

  it('loads legacy AnimeText aliases as a visible detector selection', async () => {
    await loadOptions({ settings: { localBackend: { detector: 'anime-text-yolo' } } });

    expect(field('localDetector').value).toBe('animetext-yolo');
  });

  it('shows the default detector instead of a blank selector for malformed storage', async () => {
    await loadOptions({ settings: { localBackend: { detector: 'removed-detector' } } });

    expect(field('localDetector').value).toBe('default');
  });

  it('reports a storage failure while loading', async () => {
    vi.spyOn(browser.storage.sync, 'get').mockRejectedValueOnce(new Error('storage unavailable'));

    await loadOptions();

    expect(toastText()).toBe('Error loading settings');
  });

  it('reports a storage failure while saving', async () => {
    await loadOptions();
    vi.spyOn(browser.storage.sync, 'set').mockRejectedValueOnce(new Error('quota'));

    await click('saveBtn');

    expect(toastText()).toBe('Error saving settings');
  });

  it('resets to defaults after confirmation and clears stored keys', async () => {
    const confirmMock = vi.fn(() => true);
    vi.stubGlobal('confirm', confirmMock);
    await loadOptions({
      settings: { provider: 'gemini', targetLang: 'Thai', apiKey: 'x' },
      apiKeys: { apiKey: 'secret' },
    });
    expect(field('apiKey').value).toBe('secret');

    await click('resetBtn');

    expect(confirmMock).toHaveBeenCalledWith(
      'Are you sure you want to reset all settings to defaults?',
    );
    expect(field('provider').value).toBe(DEFAULT_SETTINGS.provider);
    expect(field('targetLang').value).toBe('English');
    expect(field('apiKey').value).toBe('');
    expect((await browser.storage.local.get('apiKeys')).apiKeys).toBeUndefined();
    expect((await storedSettings()).targetLang).toBe('English');
    expect(toastText()).toBe('Settings reset to defaults');
  });

  it('leaves settings alone when the reset is declined', async () => {
    vi.stubGlobal(
      'confirm',
      vi.fn(() => false),
    );
    await loadOptions({ settings: { provider: 'gemini' }, apiKeys: { apiKey: 'secret' } });

    await click('resetBtn');

    expect(field('provider').value).toBe('gemini');
    expect((await storedApiKeys()).apiKey).toBe('secret');
  });
});

describe('options page cache', () => {
  it('counts cache entries even when metadata is missing or stale', async () => {
    await browser.storage.local.set({
      manga_cache_metadata: ['manga_cache_a'],
      manga_cache_b: { value: 'cached' },
      apiKeys: { apiKey: 'not-a-cache-entry' },
    });

    await loadOptions();

    expect(el('cacheCount').textContent).toBe('2');
    expect(el('cacheSize').textContent).toBe('—');
    expect(el('cacheAge').textContent).toBe('—');
  });

  it('clears the cache after confirmation and keeps other storage', async () => {
    const confirmMock = vi.fn(() => true);
    vi.stubGlobal('confirm', confirmMock);
    await browser.storage.local.set({
      manga_cache_a: { timestamp: 1 },
      manga_cache_b: { timestamp: 1 },
      manga_cache_metadata: ['manga_cache_a', 'manga_cache_b'],
      disabledUrls: ['example.com'],
    });
    await loadOptions();
    expect(el('cacheCount').textContent).toBe('2');

    await click('clearCacheBtn');

    expect(confirmMock).toHaveBeenCalledWith(
      'Are you sure you want to clear all cached translations?',
    );
    expect(el('cacheCount').textContent).toBe('0');
    expect(toastText()).toBe('Cleared 2 cached translations');
    expect(await browser.storage.local.get(null)).toEqual({ disabledUrls: ['example.com'] });
  });

  it('keeps the cache when the clear is declined', async () => {
    vi.stubGlobal(
      'confirm',
      vi.fn(() => false),
    );
    await browser.storage.local.set({ manga_cache_a: { timestamp: 1 } });
    await loadOptions();

    await click('clearCacheBtn');

    expect(el('cacheCount').textContent).toBe('1');
    expect((await browser.storage.local.get('manga_cache_a')).manga_cache_a).toBeDefined();
  });
});

describe('options page backend connection test', () => {
  const statusText = () => el('connectionStatus').textContent;
  const statusClass = () => el('connectionStatus').className;

  it('reports a degraded backend as not ready', async () => {
    mockFetch().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'degraded', models_loaded: false, device: 'cpu' }),
    });
    await loadOptions();
    setField('backendUrl', 'http://localhost:8001');

    await click('testConnectionBtn');

    expect(statusText()).toBe('Backend models are not ready');
    expect(statusClass()).toContain('error');
  });

  it('reports a loading backend as still loading', async () => {
    mockFetch().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'loading', models_loaded: false }),
    });
    await loadOptions();

    await click('testConnectionBtn');

    expect(statusText()).toContain('still loading');
  });

  it.each([
    [401, 'rejected the access token'],
    [503, 'no access token configured'],
  ])('explains a %i health response as a token problem', async (status, expected) => {
    mockFetch().mockResolvedValue({ ok: false, status });
    await loadOptions();

    await click('testConnectionBtn');

    expect(statusText()).toContain(expected);
    expect(statusClass()).toContain('error');
  });

  it('reports other HTTP failures by status', async () => {
    mockFetch().mockResolvedValue({ ok: false, status: 500 });
    await loadOptions();

    await click('testConnectionBtn');

    expect(statusText()).toBe('Server returned 500');
  });

  it('does not send the auth token to a non-loopback backend when testing', async () => {
    const fetchMock = mockFetch();
    await loadOptions();
    setField('backendUrl', 'http://example.com:8001');
    setField('backendAuthToken', 'private-backend-token');

    await click('testConnectionBtn');

    expect(fetchMock).not.toHaveBeenCalled();
    expect(statusClass()).toContain('error');
    expect(statusText()).toContain('Only http://localhost');
  });

  it('asks for a URL when the field is empty', async () => {
    const fetchMock = mockFetch();
    await loadOptions();
    setField('backendUrl', '  ');

    await click('testConnectionBtn');

    expect(fetchMock).not.toHaveBeenCalled();
    expect(statusText()).toBe('Please enter a URL');
  });

  it('uses the private backend auth token when testing a protected backend', async () => {
    const fetchMock = mockFetch().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok', models_loaded: true, device: 'cpu' }),
    });
    await loadOptions();
    setField('backendUrl', 'http://localhost:8001/');
    setField('backendAuthToken', 'private-backend-token');

    await click('testConnectionBtn');

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8001/health',
      expect.objectContaining({
        method: 'GET',
        headers: { 'X-Manga-Translator-Token': 'private-backend-token' },
      }),
    );
    expect(statusClass()).toContain('success');
    expect(statusText()).toBe('Connected - cpu');
  });

  it('shows Testing... and disables the button while the request is pending', async () => {
    let finish: (value: unknown) => void = () => {};
    mockFetch().mockReturnValue(new Promise((resolve) => (finish = resolve)));
    await loadOptions();

    el('testConnectionBtn').click();
    await settle();
    expect(statusText()).toBe('Testing...');
    expect(statusClass()).toContain('testing');
    expect(el<HTMLButtonElement>('testConnectionBtn').disabled).toBe(true);

    finish({ ok: true, json: async () => ({ status: 'ok', device: 'cuda' }) });
    await settle();
    expect(statusText()).toBe('Connected - cuda');
    expect(el<HTMLButtonElement>('testConnectionBtn').disabled).toBe(false);
  });

  it('reports a failed connection and a timeout', async () => {
    const fetchMock = mockFetch().mockRejectedValue(new Error('refused'));
    await loadOptions();

    await click('testConnectionBtn');
    expect(statusText()).toBe('Connection failed');

    fetchMock.mockRejectedValue(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    await click('testConnectionBtn');
    expect(statusText()).toBe('Connection timeout');
  });
});

describe('options page API key warnings', () => {
  const isVisible = (id: string) => el(id).style.display !== 'none';

  it('warns when the provider needs a key and none is configured', async () => {
    await loadOptions({
      settings: { provider: 'openrouter', localBackend: { translator: '' } },
    });

    expect(isVisible('apiKeyWarning')).toBe(true);
  });

  it('hides the translator warning when the local translator is none or local', async () => {
    await loadOptions({
      settings: { provider: 'openrouter', localBackend: { translator: '' } },
    });
    expect(isVisible('apiKeyWarning')).toBe(true);

    setField('localTranslator', 'none');
    flushSync();
    expect(isVisible('apiKeyWarning')).toBe(false);

    setField('localTranslator', 'gemini');
    flushSync();
    expect(isVisible('apiKeyWarning')).toBe(true);

    setField('localTranslator', 'local');
    flushSync();
    expect(isVisible('apiKeyWarning')).toBe(false);

    setField('localTranslator', '');
    flushSync();
    expect(isVisible('apiKeyWarning')).toBe(true);
  });

  it('hides the translator warning once an API key is typed', async () => {
    await loadOptions({ settings: { provider: 'openrouter' } });
    expect(isVisible('apiKeyWarning')).toBe(true);

    setField('apiKey', 'typed-key');
    flushSync();

    expect(isVisible('apiKeyWarning')).toBe(false);
  });

  it('follows the provider selection', async () => {
    await loadOptions({ settings: { provider: 'openrouter' } });
    expect(isVisible('apiKeyWarning')).toBe(true);

    setField('provider', 'local');
    flushSync();

    expect(isVisible('apiKeyWarning')).toBe(false);
  });
});

describe('options page form behaviour', () => {
  it('shows the local LLM settings only for the local translator', async () => {
    await loadOptions({ settings: { localBackend: { translator: 'local' } } });
    expect(el('localLlmSettings').style.display).toBe('block');

    setField('localTranslator', 'gemini');
    flushSync();
    expect(el('localLlmSettings').style.display).toBe('none');

    setField('localTranslator', 'local');
    flushSync();
    expect(el('localLlmSettings').style.display).toBe('block');
  });

  it('formats slider labels as the sliders move', async () => {
    await loadOptions();

    setField('fontSize', '18');
    setField('lineSpacing', '1.7');
    setField('borderSize', '3.5');
    setField('bgOpacity', '0.6');
    setField('glowSize', '8');
    flushSync();

    expect(el('fontSizeValue').textContent).toBe('18px');
    expect(el('lineSpacingValue').textContent).toBe('1.7');
    expect(el('borderSizeValue').textContent).toBe('3.5px');
    expect(el('bgOpacityValue').textContent).toBe('60%');
    expect(el('glowSizeValue').textContent).toBe('8px');
  });

  it('dims and disables the glow size until glow is enabled', async () => {
    await loadOptions();
    expect(el('glowSettingsGroup').style.opacity).toBe('0.5');
    expect(el<HTMLInputElement>('glowSize').disabled).toBe(true);

    setField('glowEnabled', true);
    flushSync();

    expect(el('glowSettingsGroup').style.opacity).toBe('1');
    expect(el<HTMLInputElement>('glowSize').disabled).toBe(false);
  });

  it('toggles API key visibility from the eye button', async () => {
    await loadOptions();
    const button = document.querySelector<HTMLButtonElement>('.eye-btn[data-target="apiKey"]');
    expect(button).not.toBeNull();
    expect(field('apiKey').type).toBe('password');
    const open = button?.querySelector<SVGElement>('.eye-open');
    const closed = button?.querySelector<SVGElement>('.eye-closed');
    expect(open?.style.display).toBe('block');
    expect(closed?.style.display).toBe('none');

    button?.click();
    flushSync();
    expect(field('apiKey').type).toBe('text');
    expect(open?.style.display).toBe('none');
    expect(closed?.style.display).toBe('block');

    button?.click();
    flushSync();
    expect(field('apiKey').type).toBe('password');
  });

  it('keeps the colour picker and hex box in sync', async () => {
    await loadOptions();
    const hex = field('textColorHex');
    const picker = field('textColor');

    setField('textColor', '#123456');
    flushSync();
    expect(hex.value).toBe('#123456');

    setField('textColorHex', '#ABCDEF');
    flushSync();
    expect(picker.value).toBe('#abcdef');
    expect(hex.value).toBe('#ABCDEF');

    // A half-typed colour is left alone until it is complete.
    setField('textColorHex', '#12');
    flushSync();
    expect(picker.value).toBe('#abcdef');
    expect(hex.value).toBe('#12');

    // Leaving the box with a bare hex string normalizes it.
    setField('textColorHex', 'ff0000');
    hex.dispatchEvent(new FocusEvent('blur'));
    flushSync();
    expect(hex.value).toBe('#FF0000');
    expect(picker.value).toBe('#ff0000');

    // An invalid string reverts to the current colour.
    setField('textColorHex', 'nonsense');
    hex.dispatchEvent(new FocusEvent('blur'));
    flushSync();
    expect(hex.value).toBe('#ff0000');
  });

  it('saves the colour picked through the hex box', async () => {
    await loadOptions({ apiKeys: {} });

    setField('glowColorHex', '#0A0B0C');
    await click('saveBtn');

    expect((await storedSettings()).render.glowColor).toBe('#0a0b0c');
  });
});
