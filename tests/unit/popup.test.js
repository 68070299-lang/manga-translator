const fs = require('fs');
const path = require('path');

const popupHtml = fs.readFileSync(
  path.resolve(__dirname, '../../extension/popup/popup.html'),
  'utf8'
);

const defaultSettings = {
  pipelineMode: 'cloud',
  provider: 'gemini',
  targetLang: 'English',
  fontSize: 14,
  fontFamily: 'sans-serif',
  autoTranslate: false,
  showProgress: true
};

function renderPopup() {
  const parsed = new DOMParser().parseFromString(popupHtml, 'text/html');
  document.body.innerHTML = parsed.body.innerHTML;
}

async function flushAsync(turns = 8) {
  for (let turn = 0; turn < turns; turn += 1) {
    await Promise.resolve();
  }
}

async function loadPopup({
  settings = defaultSettings,
  apiKeys = { apiKey: 'test-key' },
  session = {}
} = {}) {
  chrome.storage.sync._setData({ settings });
  chrome.storage.local._setData({ apiKeys });
  chrome.storage.session._setData(session);
  renderPopup();

  global.MangaTranslatorSettings = require('../../extension/lib/settings');
  require('../../extension/popup/popup.js');
  document.dispatchEvent(new Event('DOMContentLoaded'));
  await flushAsync();
}

async function click(buttonId) {
  document.getElementById(buttonId).click();
  await flushAsync();
}

function respondToTabMessages(handler) {
  chrome.tabs._addMessageListener(1, (message, sender, sendResponse) => {
    sendResponse(handler(message, sender));
    return false;
  });
}

describe('popup command workflow', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    jest.resetModules();
    jest.useFakeTimers();
    jest.spyOn(console, 'error').mockImplementation(() => {});
    jest.spyOn(console, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    jest.clearAllTimers();
    jest.useRealTimers();
    jest.restoreAllMocks();
    delete global.MangaTranslatorSettings;
    global.fetch = originalFetch;
    document.body.innerHTML = '';
  });

  it('blocks local translation when the backend URL is missing', async () => {
    await loadPopup({
      settings: {
        ...defaultSettings,
        pipelineMode: 'local',
        localBackend: {
          url: '',
          translator: 'none'
        }
      },
      apiKeys: {}
    });

    await click('translate-btn');

    expect(chrome.tabs.sendMessage).not.toHaveBeenCalled();
    expect(document.querySelector('.toast').textContent)
      .toBe('Please configure backend URL in Settings');
    expect(document.getElementById('translate-btn').disabled).toBe(false);
  });

  describe('local backend health check', () => {
    const localSettings = url => ({
      ...defaultSettings,
      pipelineMode: 'local',
      localBackend: { url, translator: 'none' }
    });

    function backendLabel() {
      return document.querySelector('#connection-indicator .status-label').textContent;
    }

    it('checks a loopback backend with the normalized health URL and token', async () => {
      global.fetch = jest.fn(async () => ({ ok: true, json: async () => ({}) }));

      await loadPopup({
        settings: localSettings('http://127.0.0.1:8001/'),
        apiKeys: { backendAuthToken: 'private-backend-token' }
      });

      expect(global.fetch).toHaveBeenCalledWith(
        'http://127.0.0.1:8001/health',
        expect.objectContaining({
          method: 'GET',
          headers: { 'X-Manga-Translator-Token': 'private-backend-token' }
        })
      );
      expect(backendLabel()).toBe('Backend connected');
    });

    it('accepts an IPv6 loopback backend URL', async () => {
      global.fetch = jest.fn(async () => ({ ok: true, json: async () => ({}) }));

      await loadPopup({ settings: localSettings('http://[::1]:8001') });

      expect(global.fetch).toHaveBeenCalledWith(
        'http://[::1]:8001/health',
        expect.objectContaining({ method: 'GET' })
      );
      expect(backendLabel()).toBe('Backend connected');
    });

    it.each([
      [{ ok: true, json: async () => ({ status: 'loading', models_loaded: false }) }, 'Backend loading models', 'pending'],
      [{ ok: true, json: async () => ({ status: 'degraded', models_loaded: false }) }, 'Backend degraded', 'pending'],
      [{ ok: false, status: 401 }, 'Backend rejected token', 'disconnected'],
      [{ ok: false, status: 503 }, 'Backend auth not configured', 'disconnected'],
      [{ ok: false, status: 500 }, 'Backend offline', 'disconnected']
    ])('labels a %j health response as "%s"', async (response, label, dotClass) => {
      global.fetch = jest.fn(async () => response);

      await loadPopup({ settings: localSettings('http://127.0.0.1:8001') });

      expect(backendLabel()).toBe(label);
      const dot = document.querySelector('#connection-indicator .status-dot').classList;
      expect(dot).toContain(dotClass);
      expect(dot).not.toContain('connected');
    });

    it('never sends the auth token to a non-loopback backend URL', async () => {
      global.fetch = jest.fn(async () => ({ ok: true, json: async () => ({}) }));

      await loadPopup({
        settings: localSettings('http://example.com:8001'),
        apiKeys: { backendAuthToken: 'private-backend-token' }
      });

      expect(global.fetch).not.toHaveBeenCalled();
      expect(backendLabel()).toBe('Backend offline');
      expect(document.querySelector('#connection-indicator .status-dot').classList)
        .toContain('disconnected');
    });
  });

  it('toggles translation for the active hostname', async () => {
    await loadPopup();

    await click('site-toggle-btn');
    expect(chrome.storage.local.set).toHaveBeenCalledWith({
      activeUrls: [],
      disabledUrls: ['example.com']
    });
    expect(document.getElementById('site-toggle-label').textContent).toBe('Off');

    await click('site-toggle-btn');
    expect(chrome.storage.local.set).toHaveBeenLastCalledWith({
      activeUrls: [],
      disabledUrls: []
    });
    expect(document.getElementById('site-toggle-label').textContent).toBe('On');
  });

  it('routes TRANSLATE_PAGE to the active tab and recovers the button', async () => {
    respondToTabMessages(() => ({ success: true }));
    await loadPopup();

    await click('translate-btn');

    expect(chrome.tabs.query).toHaveBeenCalledWith({ active: true, currentWindow: true });
    expect(chrome.tabs.sendMessage).toHaveBeenCalledWith(1, { type: 'TRANSLATE_PAGE' });
    expect(document.querySelector('.toast').textContent).toBe('Page translation started');
    expect(document.getElementById('translate-btn').disabled).toBe(false);
    expect(document.getElementById('status-text').textContent).toBe('Starting page translation...');
  });

  it('surfaces a missing content receiver and recovers the button', async () => {
    chrome.tabs.sendMessage.mockRejectedValueOnce(
      new Error('Could not establish connection. Receiving end does not exist.')
    );
    await loadPopup();

    await click('translate-btn');

    expect(chrome.tabs.sendMessage).toHaveBeenCalledWith(1, { type: 'TRANSLATE_PAGE' });
    expect(document.querySelector('.toast').textContent)
      .toBe('Translation failed: Could not connect to page. Try refreshing the tab.');
    expect(document.getElementById('translate-btn').disabled).toBe(false);
  });

  it('clears page translations and removes persisted progress', async () => {
    respondToTabMessages(message => {
      expect(message).toEqual({ type: 'CLEAR_PAGE_TRANSLATIONS' });
      return { cleared: 3 };
    });
    await loadPopup({
      session: {
        translationProgress: {
          status: 'Translating',
          percent: 45,
          tabId: 1,
          timestamp: Date.now()
        }
      }
    });

    await click('clear-page-btn');

    expect(chrome.tabs.sendMessage).toHaveBeenCalledWith(1, { type: 'CLEAR_PAGE_TRANSLATIONS' });
    expect(document.querySelector('.toast').textContent).toBe('Cleared 3 page translations');
    expect(chrome.storage.session.remove).toHaveBeenCalledWith('translationProgress');
    await expect(chrome.storage.session.get('translationProgress'))
      .resolves.toEqual({ translationProgress: undefined });
    expect(document.getElementById('status-section').style.display).toBe('none');
    expect(document.getElementById('clear-page-btn').disabled).toBe(false);
  });

  it('routes STOP_TRANSLATION and reports the response', async () => {
    respondToTabMessages(message => {
      expect(message).toEqual({ type: 'STOP_TRANSLATION' });
      return { stopped: true };
    });
    await loadPopup();

    await click('stop-btn');

    expect(chrome.tabs.sendMessage).toHaveBeenCalledWith(1, { type: 'STOP_TRANSLATION' });
    expect(document.querySelector('.toast').textContent).toBe('Stopping translation');
    expect(document.getElementById('status-text').textContent)
      .toBe('Stopping after current image...');
    expect(document.getElementById('stop-btn').disabled).toBe(false);
  });

  it('accepts progress messages only from the active translation tab', async () => {
    respondToTabMessages(() => ({ success: true }));
    await loadPopup();
    await click('translate-btn');

    await chrome.runtime._dispatchOnMessage(
      { type: 'TRANSLATION_PROGRESS', status: 'Wrong tab', percent: 90 },
      { tab: { id: 2 } }
    );
    expect(document.getElementById('status-text').textContent)
      .toBe('Starting page translation...');

    await chrome.runtime._dispatchOnMessage(
      { type: 'TRANSLATION_PROGRESS', status: 'Translating panel 2', percent: 64 },
      { tab: { id: 1 } }
    );
    await flushAsync();

    expect(document.getElementById('status-text').textContent).toBe('Translating panel 2');
    expect(document.getElementById('progress-fill').style.width).toBe('64%');
    await expect(chrome.storage.session.get('translationProgress')).resolves.toEqual({
      translationProgress: expect.objectContaining({
        status: 'Translating panel 2',
        percent: 64,
        tabId: 1
      })
    });
  });

  it('displays terminal completion counts and status', async () => {
    respondToTabMessages(() => ({ success: true }));
    await loadPopup();
    await click('translate-btn');

    await chrome.runtime._dispatchOnMessage(
      { type: 'TRANSLATION_COMPLETE', status: 'partial', total: 3, succeeded: 2, failed: 1 },
      { tab: { id: 1 } }
    );
    await flushAsync();

    expect(document.getElementById('status-text').textContent).toBe('Partial (2/3 images)');
    expect(document.getElementById('progress-fill').style.width).toBe('100%');
  });

  it('displays stopped and failed terminal states for the active tab', async () => {
    respondToTabMessages(() => ({ success: true }));
    await loadPopup();
    await click('translate-btn');

    await chrome.runtime._dispatchOnMessage(
      { type: 'TRANSLATION_STOPPED', total: 2, succeeded: 1 },
      { tab: { id: 1 } }
    );
    await flushAsync();
    expect(document.getElementById('status-text').textContent).toBe('Stopped (1/2 images)');
  });

  it('displays failed terminal status for the active tab', async () => {
    respondToTabMessages(() => ({ success: true }));
    await loadPopup();
    await click('translate-btn');

    await chrome.runtime._dispatchOnMessage(
      { type: 'TRANSLATION_COMPLETE', status: 'error', total: 2, succeeded: 0, failed: 2 },
      { tab: { id: 1 } }
    );
    await flushAsync();
    expect(document.getElementById('status-text').textContent).toBe('Failed (0/2 images)');
  });
});
