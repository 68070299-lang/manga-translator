const {
  DEFAULT_SETTINGS,
  migrateStoredSettings,
  applyStoredApiKeys,
  splitSettingsForStorage,
  translatorNeedsApiKey,
  isValidLocalBackendUrl
} = MangaTranslatorSettings;

// Pipeline mode is chosen in the popup; remember the stored value so the
// API key warnings only consider local-only overrides when they apply.
let currentPipelineMode = DEFAULT_SETTINGS.pipelineMode;

const elements = Object.fromEntries([
  'provider', 'model', 'apiKey', 'baseUrl', 'baseUrlGroup',
  'backendUrl', 'backendAuthToken', 'testConnectionBtn', 'connectionStatus',
  'localDetector', 'localOcr', 'localInpainter', 'localTranslator',
  'localLlmSettings', 'localLlmBaseUrl', 'localLlmModel', 'localLlmApiKey',
  'googleOcrApiKey', 'googleOcrLanguageHint',
  'targetLang', 'fontSize', 'fontSizeValue', 'fontFamily', 'textAlignment', 'textDirection', 'uppercase',
  'lineSpacing', 'lineSpacingValue', 'borderSize', 'borderSizeValue',
  'textColor', 'textColorHex', 'borderColor', 'borderColorHex', 'bgColor', 'bgColorHex',
  'bgOpacity', 'bgOpacityValue',
  'glowEnabled', 'glowSize', 'glowSizeValue', 'glowColor', 'glowColorHex', 'glowSettingsGroup', 'autoScale',
  'autoTranslate', 'showProgress',
  'saveBtn', 'resetBtn', 'clearCacheBtn',
  'cacheCount', 'cacheSize', 'cacheAge'
].map(id => [id, document.getElementById(id)]));

// Slider id -> how its value label is formatted.
const SLIDER_LABELS = {
  fontSize: value => value + 'px',
  lineSpacing: value => value,
  borderSize: value => value + 'px',
  bgOpacity: value => Math.round(value * 100) + '%',
  glowSize: value => value + 'px'
};

function setSlider(id, value) {
  elements[id].value = value;
  elements[id + 'Value'].textContent = SLIDER_LABELS[id](value);
}

function setColor(id, value) {
  elements[id].value = value;
  elements[id + 'Hex'].value = value;
}

function setSelectOrDefault(select, value, fallback) {
  select.value = value;
  if (!select.value) select.value = fallback;
}

async function init() {
  await loadSettings();
  setupEventListeners();
  updateCacheStats();
}

async function loadSettings() {
  try {
    const result = await chrome.storage.sync.get('settings');
    const migration = migrateStoredSettings(result.settings);
    if (migration.changed) {
      await chrome.storage.sync.set({ settings: migration.settings });
      result.settings = migration.settings;
    }
    const localResult = await chrome.storage.local.get('apiKeys');
    const settings = applyStoredApiKeys(result.settings || {}, localResult.apiKeys || {});
    const { localBackend, render } = settings;

    elements.provider.value = settings.provider;
    elements.model.value = settings.model;
    elements.apiKey.value = settings.apiKey;
    elements.baseUrl.value = settings.baseUrl;

    elements.backendUrl.value = localBackend.url;
    elements.backendAuthToken.value = localBackend.authToken || '';
    setSelectOrDefault(elements.localDetector, localBackend.detector, DEFAULT_SETTINGS.localBackend.detector);
    setSelectOrDefault(elements.localOcr, localBackend.ocr, DEFAULT_SETTINGS.localBackend.ocr);
    setSelectOrDefault(elements.localInpainter, localBackend.inpainter, DEFAULT_SETTINGS.localBackend.inpainter);
    const localTranslator = localBackend.translator ?? '';
    elements.localTranslator.value = localTranslator;

    elements.localLlmBaseUrl.value = settings.localLlm?.baseUrl || '';
    elements.localLlmModel.value = settings.localLlm?.model || '';
    elements.localLlmApiKey.value = settings.localLlm?.apiKey || '';
    updateLocalLlmVisibility(localTranslator === 'local');

    elements.googleOcrApiKey.value = settings.googleOcr.apiKey;
    elements.googleOcrLanguageHint.value = settings.googleOcr.languageHint;

    elements.targetLang.value = settings.targetLang;
    setSlider('fontSize', settings.fontSize);
    elements.fontFamily.value = settings.fontFamily;
    elements.textAlignment.value = render.textAlignment;
    elements.textDirection.value = render.textDirection;
    elements.uppercase.checked = render.uppercase;
    setSlider('lineSpacing', render.lineSpacing);
    setSlider('borderSize', render.borderSize);

    setColor('textColor', render.textColor);
    setColor('borderColor', render.borderColor);
    setColor('bgColor', render.bgColor);
    setSlider('bgOpacity', render.bgOpacity);

    elements.glowEnabled.checked = render.glowEnabled;
    setSlider('glowSize', render.glowSize);
    setColor('glowColor', render.glowColor);
    elements.autoScale.checked = render.autoScale;
    updateGlowSettingsVisibility(render.glowEnabled);

    elements.autoTranslate.checked = settings.autoTranslate;
    elements.showProgress.checked = settings.showProgress;

    currentPipelineMode = settings.pipelineMode;

    // The custom provider was removed, so its base URL is never editable.
    elements.baseUrlGroup.style.display = 'none';
    updateApiKeyWarning();
  } catch (error) {
    console.error('Error loading settings:', error);
    showToast('Error loading settings', 'error');
  }
}

function updateApiKeyWarning() {
  const isLocal = currentPipelineMode === 'local';
  const translator = (isLocal && elements.localTranslator.value) || elements.provider.value;
  const apiKey = elements.apiKey.value.trim();
  const needsKey = translatorNeedsApiKey(translator);
  const warning = document.getElementById('apiKeyWarning');
  if (warning) {
    warning.style.display = needsKey && !apiKey ? 'block' : 'none';
  }

  const needsGoogleKey = isLocal &&
    elements.localOcr.value === 'google-ocr' &&
    !elements.googleOcrApiKey.value.trim();
  const googleWarning = document.getElementById('googleOcrKeyWarning');
  if (googleWarning) {
    googleWarning.style.display = needsGoogleKey ? 'block' : 'none';
  }
}

function updateLocalLlmVisibility(show) {
  if (elements.localLlmSettings) {
    elements.localLlmSettings.style.display = show ? 'block' : 'none';
  }
}

function updateGlowSettingsVisibility(enabled) {
  if (elements.glowSettingsGroup) {
    elements.glowSettingsGroup.style.opacity = enabled ? '1' : '0.5';
    elements.glowSize.disabled = !enabled;
  }
}

async function saveSettings() {
  try {
    const backendUrl = elements.backendUrl.value.trim();
    if (backendUrl && !isValidLocalBackendUrl(backendUrl)) {
      showToast('Only http://localhost, http://127.0.0.1 or http://[::1] URLs are allowed', 'error');
      return;
    }
    const stored = await chrome.storage.sync.get('settings');
    const existingSettings = typeof stored.settings === 'object'
      ? { ...DEFAULT_SETTINGS, ...stored.settings, localBackend: { ...DEFAULT_SETTINGS.localBackend, ...(stored.settings.localBackend || {}) } }
      : {};
    const settings = {
      ...existingSettings,
      provider: elements.provider.value,
      model: elements.model.value.trim(),
      apiKey: elements.apiKey.value,
      baseUrl: elements.baseUrl.value,
      localBackend: {
        ...(existingSettings.localBackend || {}),
        url: backendUrl.replace(/\/+$/, ''),
        authToken: elements.backendAuthToken.value,
        detector: elements.localDetector.value,
        ocr: elements.localOcr.value,
        inpainter: elements.localInpainter.value,
        translator: elements.localTranslator.value || ''
      },
      localLlm: {
        baseUrl: elements.localLlmBaseUrl.value,
        model: elements.localLlmModel.value.trim(),
        apiKey: elements.localLlmApiKey.value
      },
      googleOcr: {
        apiKey: elements.googleOcrApiKey.value,
        languageHint: elements.googleOcrLanguageHint.value
      },
      targetLang: elements.targetLang.value,
      fontSize: parseInt(elements.fontSize.value),
      fontFamily: elements.fontFamily.value,
      render: {
        textAlignment: elements.textAlignment.value,
        textDirection: elements.textDirection.value,
        uppercase: elements.uppercase.checked,
        lineSpacing: parseFloat(elements.lineSpacing.value),
        borderSize: parseFloat(elements.borderSize.value),
        textColor: elements.textColor.value,
        borderColor: elements.borderColor.value,
        bgColor: elements.bgColor.value,
        bgOpacity: parseFloat(elements.bgOpacity.value),
        glowEnabled: elements.glowEnabled.checked,
        glowSize: parseInt(elements.glowSize.value),
        glowColor: elements.glowColor.value,
        autoScale: elements.autoScale.checked
      },
      autoTranslate: elements.autoTranslate.checked,
      showProgress: elements.showProgress.checked
    };

    const { syncSettings, apiKeys } = splitSettingsForStorage(settings);

    await chrome.storage.sync.set({ settings: syncSettings });
    await chrome.storage.local.set({ apiKeys });
    showToast('Settings saved successfully!', 'success');
  } catch (error) {
    console.error('Error saving settings:', error);
    showToast('Error saving settings', 'error');
  }
}

async function resetSettings() {
  if (!confirm('Are you sure you want to reset all settings to defaults?')) {
    return;
  }

  try {
    const { syncSettings } = splitSettingsForStorage(DEFAULT_SETTINGS);
    await chrome.storage.sync.set({ settings: syncSettings });
    await chrome.storage.local.remove('apiKeys');
    await loadSettings();
    showToast('Settings reset to defaults', 'success');
  } catch (error) {
    console.error('Error resetting settings:', error);
    showToast('Error resetting settings', 'error');
  }
}

async function clearCache() {
  if (!confirm('Are you sure you want to clear all cached translations?')) {
    return;
  }

  try {
    const cacheKeys = (await MangaTranslatorSettings.listLocalStorageKeys())
      .filter(k => k.startsWith('manga_cache_'));

    if (cacheKeys.length > 0) {
      await chrome.storage.local.remove(cacheKeys);
    }

    await chrome.storage.local.remove('manga_cache_metadata');

    await updateCacheStats();
    showToast(`Cleared ${cacheKeys.length} cached translations`, 'success');
  } catch (error) {
    console.error('Error clearing cache:', error);
    showToast('Error clearing cache', 'error');
  }
}

async function updateCacheStats() {
  try {
    const { manga_cache_metadata: stored } = await chrome.storage.local.get('manga_cache_metadata');
    const metadata = Array.isArray(stored) ? stored : [];
    const keys = new Set([
      ...metadata.filter(key => typeof key === 'string'),
      ...(await MangaTranslatorSettings.listLocalStorageKeys())
        .filter(key => key.startsWith('manga_cache_') && key !== 'manga_cache_metadata')
    ]);

    elements.cacheCount.textContent = keys.size;

    // Size and age would require reading every entry.
    elements.cacheSize.textContent = '—';
    elements.cacheAge.textContent = '—';
  } catch (error) {
    console.error('Error getting cache stats:', error);
    elements.cacheCount.textContent = '0';
  }
}

function setupEventListeners() {
  const navLinks = document.querySelectorAll('.nav-link');
  navLinks.forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const target = document.getElementById(link.getAttribute('data-section'));
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        navLinks.forEach(l => l.classList.remove('active'));
        link.classList.add('active');
      }
    });
  });

  window.addEventListener('scroll', () => {
    const sections = ['pipeline', 'translate-api', 'google-ocr', 'render', 'behavior', 'cache'];
    let current = '';
    sections.forEach(id => {
      const section = document.getElementById(id);
      if (section && section.offsetTop - 100 <= window.scrollY) {
        current = id;
      }
    });
    navLinks.forEach(link => {
      link.classList.toggle('active', link.getAttribute('data-section') === current);
    });
  });

  elements.provider.addEventListener('change', updateApiKeyWarning);
  elements.apiKey.addEventListener('input', updateApiKeyWarning);
  elements.localOcr.addEventListener('change', updateApiKeyWarning);
  elements.googleOcrApiKey.addEventListener('input', updateApiKeyWarning);

  elements.localTranslator.addEventListener('change', () => {
    updateLocalLlmVisibility(elements.localTranslator.value === 'local');
    updateApiKeyWarning();
  });

  Object.keys(SLIDER_LABELS).forEach(id => {
    elements[id].addEventListener('input', () => {
      elements[id + 'Value'].textContent = SLIDER_LABELS[id](elements[id].value);
    });
  });

  ['textColor', 'borderColor', 'bgColor', 'glowColor'].forEach(id => {
    setupColorSync(elements[id], elements[id + 'Hex']);
  });

  elements.glowEnabled.addEventListener('change', () => {
    updateGlowSettingsVisibility(elements.glowEnabled.checked);
  });

  elements.testConnectionBtn.addEventListener('click', testConnection);
  elements.saveBtn.addEventListener('click', saveSettings);
  elements.resetBtn.addEventListener('click', resetSettings);
  elements.clearCacheBtn.addEventListener('click', clearCache);

  document.querySelectorAll('.eye-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const input = document.getElementById(btn.getAttribute('data-target'));
      const reveal = input.type === 'password';
      input.type = reveal ? 'text' : 'password';
      btn.querySelector('.eye-open').style.display = reveal ? 'none' : 'block';
      btn.querySelector('.eye-closed').style.display = reveal ? 'block' : 'none';
    });
  });
}

function setupColorSync(colorInput, hexInput) {
  if (!colorInput || !hexInput) return;

  colorInput.addEventListener('input', () => {
    hexInput.value = colorInput.value;
  });

  hexInput.addEventListener('input', () => {
    const hex = hexInput.value;
    if (/^#[0-9A-Fa-f]{6}$/.test(hex)) {
      colorInput.value = hex;
    }
  });

  hexInput.addEventListener('blur', () => {
    let hex = hexInput.value.trim();
    if (!hex.startsWith('#')) hex = '#' + hex;
    if (/^#[0-9A-Fa-f]{6}$/.test(hex)) {
      hexInput.value = hex.toUpperCase();
      colorInput.value = hex;
    } else {
      hexInput.value = colorInput.value;
    }
  });
}

async function testConnection() {
  const url = elements.backendUrl.value.trim();
  if (!url) {
    showConnectionStatus('Please enter a URL', 'error');
    return;
  }

  if (!isValidLocalBackendUrl(url)) {
    showConnectionStatus('Only http://localhost, http://127.0.0.1 or http://[::1] URLs are allowed', 'error');
    return;
  }

  showConnectionStatus('Testing...', 'testing');
  elements.testConnectionBtn.disabled = true;

  let timeout;
  try {
    const controller = new AbortController();
    timeout = setTimeout(() => controller.abort(), 5000);

    const authToken = elements.backendAuthToken.value.trim();
    const headers = authToken ? { 'X-Manga-Translator-Token': authToken } : undefined;
    const response = await fetch(url.replace(/\/$/, '') + '/health', {
      method: 'GET',
      headers,
      signal: controller.signal
    });

    if (response.ok) {
      const data = await response.json();
      if (data.status === 'loading') {
        showConnectionStatus('Backend is still loading models; try again shortly', 'error');
      } else if (data.status !== 'ok' || data.models_loaded === false) {
        showConnectionStatus('Backend models are not ready', 'error');
      } else {
        showConnectionStatus(`Connected - ${data.device || 'ready'}`, 'success');
      }
    } else if (response.status === 401) {
      showConnectionStatus('Backend rejected the access token (401); check the Backend Access Token', 'error');
    } else if (response.status === 503) {
      showConnectionStatus('Backend has no access token configured (503); set BACKEND_AUTH_TOKEN and restart it', 'error');
    } else {
      showConnectionStatus(`Server returned ${response.status}`, 'error');
    }
  } catch (error) {
    showConnectionStatus(error.name === 'AbortError' ? 'Connection timeout' : 'Connection failed', 'error');
  } finally {
    clearTimeout(timeout);
    elements.testConnectionBtn.disabled = false;
  }
}

function showConnectionStatus(message, type) {
  elements.connectionStatus.textContent = message;
  elements.connectionStatus.className = 'connection-status ' + type;
}

function showToast(message, type = 'info') {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);

  setTimeout(() => toast.classList.add('show'), 10);

  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

document.addEventListener('DOMContentLoaded', init);

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    init,
    loadSettings,
    saveSettings,
    resetSettings,
    clearCache,
    updateCacheStats,
    testConnection,
    updateApiKeyWarning,
    showConnectionStatus
  };
}
