const {
  applyStoredApiKeys,
  splitSettingsForStorage,
  resolveTranslator,
  translatorNeedsApiKey,
  buildLocalBackendUrl
} = MangaTranslatorSettings;
const siteActivation = globalThis.MangaTranslatorSiteActivation ||
  (typeof require === 'function' ? require('../lib/site-activation') : null);

let activeTranslationTabId = null;
let activeSiteState = null;
const CONTENT_MESSAGE_TIMEOUT_MS = 8000;
const BACKEND_HEALTH_TIMEOUT_MS = 5000;

const elements = Object.fromEntries([
  'settings-btn', 'target-lang', 'font-size', 'font-size-value', 'font-family', 'auto-translate', 'show-progress',
  'translate-btn', 'translate-visible-btn', 'translate-region-btn', 'clear-page-btn', 'stop-btn', 'clear-cache-btn',
  'status-section', 'status-text', 'progress-fill', 'cache-count',
  'mode-cloud', 'mode-local', 'connection-indicator', 'site-toggle-btn', 'site-toggle-label'
].map(id => [id.replace(/-(\w)/g, (_, letter) => letter.toUpperCase()), document.getElementById(id)]));

async function init() {
  await loadSettings();
  setupEventListeners();
  await refreshSiteActivationState();
  updateCacheStats();
  await restoreProgressState();
}

async function restoreProgressState() {
  try {
    const result = await chrome.storage.session.get('translationProgress');
    if (result.translationProgress) {
      const { status, percent, tabId, timestamp } = result.translationProgress;
      if (Date.now() - timestamp < 60000) {
        activeTranslationTabId = tabId;
        elements.statusSection.style.display = 'block';
        updateStatus(status, percent);
      } else {
        await chrome.storage.session.remove('translationProgress');
      }
    }
  } catch (error) {
    console.error('Error restoring progress state:', error);
  }
}

async function loadSettings() {
  try {
    const settings = await loadCurrentSettings();

    elements.targetLang.value = settings.targetLang;
    elements.fontSize.value = settings.fontSize;
    elements.fontSizeValue.textContent = settings.fontSize;
    elements.fontFamily.value = settings.fontFamily;
    elements.autoTranslate.checked = settings.autoTranslate;
    elements.showProgress.checked = settings.showProgress;

    updateApiKeyWarning(settings);

    if (settings.pipelineMode === 'local') {
      elements.modeLocal.checked = true;
      elements.connectionIndicator.style.display = 'flex';
      checkLocalBackendConnection(settings.localBackend.url, settings);
    } else {
      elements.modeCloud.checked = true;
      elements.connectionIndicator.style.display = 'none';
    }
  } catch (error) {
    console.error('Error loading settings:', error);
  }
}

function setBackendIndicator(state, text) {
  const indicator = elements.connectionIndicator;
  const dot = indicator.querySelector('.status-dot');
  dot.classList.remove('disconnected', 'pending', 'connected');
  dot.classList.add(state);
  indicator.querySelector('.status-label').textContent = text;
}

async function checkLocalBackendConnection(url, settings = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), BACKEND_HEALTH_TIMEOUT_MS);

  try {
    // Throws for non-loopback URLs so the auth token is never sent off-machine.
    const healthUrl = buildLocalBackendUrl(url, '/health');
    const authToken = settings.localBackend?.authToken || settings.backendAuthToken || '';
    const response = await fetch(healthUrl, {
      method: 'GET',
      ...(authToken ? { headers: { 'X-Manga-Translator-Token': authToken } } : {}),
      signal: controller.signal
    });

    if (response.ok) {
      const data = await response.json();
      if (data?.status === 'loading') {
        setBackendIndicator('pending', 'Backend loading models');
      } else if (data?.status === 'degraded' || data?.models_loaded === false) {
        setBackendIndicator('pending', 'Backend degraded');
      } else {
        setBackendIndicator('connected', 'Backend connected');
      }
      if (data?.models) {
        displayModelStatus(data.models);
      }
    } else if (response.status === 401 || response.status === 503) {
      // The server is up but refused the request: say why instead of "offline".
      setBackendIndicator('disconnected', response.status === 401 ? 'Backend rejected token' : 'Backend auth not configured');
      clearModelStatus();
    } else {
      throw new Error('Not OK');
    }
  } catch (error) {
    setBackendIndicator('disconnected', 'Backend offline');
    clearModelStatus();
  } finally {
    clearTimeout(timeout);
  }
}

const MODEL_NAMES = { detector: 'Detector', ocr: 'OCR', paddle_ocr: 'PaddleOCR', inpainter: 'Inpainter' };
const MODEL_STATUS_ICONS = { loaded: 'OK', loading: '..', error: '!', not_loaded: '-' };

function displayModelStatus(models) {
  clearModelStatus();

  const modelList = document.createElement('div');
  modelList.className = 'model-status-list';

  for (const [key, info] of Object.entries(models)) {
    const item = document.createElement('div');
    item.className = `model-status-item ${info.status}`;

    const icon = document.createElement('span');
    icon.className = 'model-status-icon';
    icon.textContent = MODEL_STATUS_ICONS[info.status] || '-';

    const name = document.createElement('span');
    name.className = 'model-status-name';
    name.textContent = MODEL_NAMES[key] || key;

    item.append(icon, name);
    modelList.appendChild(item);
  }

  elements.connectionIndicator.appendChild(modelList);
}

function clearModelStatus() {
  document.querySelector('.model-status-list')?.remove();
}

async function saveSettings() {
  try {
    // Preserve private keys from local storage and migrate any legacy sync keys.
    const existingSettings = await loadCurrentSettings();

    const settings = {
      ...existingSettings,
      targetLang: elements.targetLang.value,
      fontSize: parseInt(elements.fontSize.value),
      fontFamily: elements.fontFamily.value,
      autoTranslate: elements.autoTranslate.checked,
      showProgress: elements.showProgress.checked,
      pipelineMode: elements.modeLocal.checked ? 'local' : 'cloud'
    };

    const { syncSettings, apiKeys } = splitSettingsForStorage(settings);

    await chrome.storage.sync.set({ settings: syncSettings });
    await chrome.storage.local.set({ apiKeys });

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab?.id) {
      chrome.tabs.sendMessage(tab.id, { type: 'SETTINGS_UPDATED', settings: syncSettings }).catch(err => console.warn('[Popup]', err));
    }
  } catch (error) {
    console.error('Error saving settings:', error);
  }
}

async function updateCacheStats() {
  try {
    const { manga_cache_metadata: metadata } = await chrome.storage.local.get('manga_cache_metadata');
    const metadataKeys = Array.isArray(metadata) ? metadata : [];
    const storageKeys = (await MangaTranslatorSettings.listLocalStorageKeys())
      .filter(key => key.startsWith('manga_cache_') && key !== 'manga_cache_metadata');
    const keys = Array.from(new Set([...metadataKeys, ...storageKeys]));
    elements.cacheCount.textContent = `${keys.length} cached translations`;
  } catch (error) {
    elements.cacheCount.textContent = '0 cached';
  }
}

function setupEventListeners() {
  elements.settingsBtn.addEventListener('click', () => {
    chrome.runtime.openOptionsPage();
  });

  elements.fontSize.addEventListener('input', () => {
    elements.fontSizeValue.textContent = elements.fontSize.value;
  });
  [
    elements.targetLang,
    elements.fontFamily,
    elements.fontSize,
    elements.autoTranslate,
    elements.showProgress
  ].forEach(input => input.addEventListener('change', saveSettings));

  elements.translateBtn.addEventListener('click', translatePage);
  elements.translateVisibleBtn.addEventListener('click', translateVisibleImages);
  elements.translateRegionBtn.addEventListener('click', translateRegion);
  elements.clearPageBtn.addEventListener('click', clearPageTranslations);
  elements.stopBtn.addEventListener('click', stopTranslation);
  elements.clearCacheBtn.addEventListener('click', clearCache);
  elements.siteToggleBtn?.addEventListener('click', toggleSiteActivation);

  elements.modeCloud.addEventListener('change', async () => {
    elements.connectionIndicator.style.display = 'none';
    await saveSettings();
    await refreshApiKeyWarning();
  });

  elements.modeLocal.addEventListener('change', async () => {
    elements.connectionIndicator.style.display = 'flex';
    await saveSettings();
    await refreshApiKeyWarning();
    const settings = await loadCurrentSettings();
    checkLocalBackendConnection(settings.localBackend.url, settings);
  });
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  if (!tab?.id) {
    throw new Error('No active tab found');
  }

  return tab;
}

async function refreshSiteActivationState() {
  if (!siteActivation?.getSiteState || !elements.siteToggleBtn) return null;

  try {
    const tab = await getActiveTab();
    activeSiteState = await siteActivation.getSiteState(tab.url || '');
    updateSiteActivationControl(activeSiteState);
    return activeSiteState;
  } catch (error) {
    console.warn('[Popup] Failed to read site activation:', error?.message || error);
    activeSiteState = null;
    return null;
  }
}

function updateSiteActivationControl(state) {
  if (!state || !elements.siteToggleBtn) return;
  const enabled = Boolean(state.enabled);
  elements.siteToggleBtn.setAttribute('aria-pressed', String(enabled));
  elements.siteToggleBtn.title = enabled
    ? `Disable translation on ${state.hostname}`
    : `Enable translation on ${state.hostname}`;
  elements.siteToggleBtn.setAttribute('aria-label', elements.siteToggleBtn.title);
  if (elements.siteToggleLabel) elements.siteToggleLabel.textContent = enabled ? 'On' : 'Off';
}

async function toggleSiteActivation() {
  if (!siteActivation?.setSiteEnabled || !elements.siteToggleBtn) return;

  try {
    elements.siteToggleBtn.disabled = true;
    const tab = await getActiveTab();
    const current = await siteActivation.getSiteState(tab.url || '');
    activeSiteState = await siteActivation.setSiteEnabled(tab.url || '', !current.enabled);
    updateSiteActivationControl(activeSiteState);
    if (tab.id) {
      chrome.tabs.sendMessage(tab.id, { type: 'SITE_ACTIVATION_CHANGED' }).catch(() => {});
    }
    showToast(activeSiteState.enabled ? 'Translation enabled on this site' : 'Translation disabled on this site', 'success');
  } catch (error) {
    console.error('[Popup] Site activation error:', error);
    showToast('Site activation failed: ' + error.message, 'error');
  } finally {
    elements.siteToggleBtn.disabled = false;
  }
}

function getTranslationSetupError(settings) {
  const translator = resolveTranslator(settings);
  const needsApiKey = translatorNeedsApiKey(translator);

  if (settings.pipelineMode !== 'local' && needsApiKey && !settings.apiKey) {
    return 'Please configure a Translate API key in Settings';
  }

  if (settings.pipelineMode === 'local' && !settings.localBackend.url) {
    return 'Please configure backend URL in Settings';
  }

  return '';
}

async function loadCurrentSettings() {
  const result = await chrome.storage.sync.get('settings');
  const localResult = await chrome.storage.local.get('apiKeys');
  return applyStoredApiKeys(result.settings || {}, localResult.apiKeys || {});
}

async function sendTranslationCommand(messageType, button, startStatus, successMessage) {
  try {
    const settings = await loadCurrentSettings();
    const setupError = getTranslationSetupError(settings);
    if (setupError) {
      showToast(setupError, 'error');
      return;
    }

    button.disabled = true;
    elements.statusSection.style.display = 'block';
    updateStatus(startStatus, 10);

    const tab = await getActiveTab();

    activeTranslationTabId = tab.id;

    const response = await sendContentMessage(tab.id, { type: messageType });

    if (response?.success === false) {
      throw new Error(response.error || 'Translation is disabled on this site');
    }

    showToast(successMessage, 'success');

  } catch (error) {
    console.error('Translation error:', error);
    showToast('Translation failed: ' + error.message, 'error');
    activeTranslationTabId = null;
  } finally {
    button.disabled = false;
  }
}

async function translatePage() {
  await sendTranslationCommand('TRANSLATE_PAGE', elements.translateBtn, 'Starting page translation...', 'Page translation started');
}

async function translateVisibleImages() {
  await sendTranslationCommand('TRANSLATE_VISIBLE_IMAGES', elements.translateVisibleBtn, 'Starting visible image translation...', 'Visible image translation started');
}

async function translateRegion() {
  await sendTranslationCommand('START_REGION_SELECTION', elements.translateRegionBtn, 'Starting region selection...', 'Drag over the area to translate');
  // The open popup would take the first click of the drag.
  window.close();
}

function sendContentMessage(tabId, message) {
  return Promise.race([
    chrome.tabs.sendMessage(tabId, message),
    new Promise((_, reject) => {
      setTimeout(() => reject(new Error('Content script message timed out')), CONTENT_MESSAGE_TIMEOUT_MS);
    })
  ]).catch(() => {
    throw new Error('Could not connect to page. Try refreshing the tab.');
  });
}

async function clearPageTranslations() {
  try {
    elements.clearPageBtn.disabled = true;
    const tab = await getActiveTab();
    activeTranslationTabId = tab.id;

    const response = await sendContentMessage(tab.id, { type: 'CLEAR_PAGE_TRANSLATIONS' });

    const count = response?.cleared ?? 0;
    showToast(`Cleared ${count} page translations`, 'success');
    await chrome.storage.session.remove('translationProgress');
    elements.statusSection.style.display = 'none';
    activeTranslationTabId = null;
  } catch (error) {
    console.error('Clear page translation error:', error);
    showToast('Clear page failed: ' + error.message, 'error');
    activeTranslationTabId = null;
  } finally {
    elements.clearPageBtn.disabled = false;
  }
}

async function stopTranslation() {
  try {
    elements.stopBtn.disabled = true;
    const tab = await getActiveTab();
    activeTranslationTabId = tab.id;

    elements.statusSection.style.display = 'block';
    updateStatus('Stopping after current image...', 96);

    const response = await sendContentMessage(tab.id, { type: 'STOP_TRANSLATION' });

    showToast(response?.stopped ? 'Stopping translation' : 'No translation is running', 'success');
  } catch (error) {
    console.error('Stop translation error:', error);
    showToast('Stop failed: ' + error.message, 'error');
    activeTranslationTabId = null;
  } finally {
    elements.stopBtn.disabled = false;
  }
}

async function clearCache() {
  try {
    const cacheKeys = (await MangaTranslatorSettings.listLocalStorageKeys())
      .filter(k => k.startsWith('manga_cache_'));

    if (cacheKeys.length > 0) {
      await chrome.storage.local.remove(cacheKeys);
    }

    await chrome.storage.local.remove('manga_cache_metadata');

    updateCacheStats();
    showToast(`Cleared ${cacheKeys.length} cached translations`, 'success');
  } catch (error) {
    console.error('Clear cache error:', error);
    showToast('Failed to clear cache', 'error');
  }
}

function updateStatus(text, percent) {
  elements.statusText.textContent = text;
  elements.progressFill.style.width = `${percent}%`;
  if (activeTranslationTabId !== null) {
    chrome.storage.session.set({
      translationProgress: { status: text, percent, tabId: activeTranslationTabId, timestamp: Date.now() }
    }).catch(err => console.warn('[Popup]', err));
  }
}

function showToast(message, type = 'info') {
  document.querySelector('.toast')?.remove();

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

function endTranslationRun(hideStatusLater) {
  chrome.storage.session.remove('translationProgress').catch(err => console.warn('[Popup]', err));
  if (hideStatusLater) {
    setTimeout(() => {
      elements.statusSection.style.display = 'none';
    }, 2000);
  }
  activeTranslationTabId = null;
}

function formatRunCounts(message) {
  return Number.isFinite(message.total)
    ? ` (${message.succeeded || 0}/${message.total} images)`
    : '';
}

// Listen for status updates from background
chrome.runtime.onMessage.addListener((message, sender, _sendResponse) => {
  if (activeTranslationTabId === null || sender?.tab?.id !== activeTranslationTabId) {
    return;
  }

  if (message.type === 'TRANSLATION_PROGRESS') {
    elements.statusSection.style.display = 'block';
    updateStatus(message.status, message.percent);
  } else if (message.type === 'TRANSLATION_COMPLETE') {
    const label = message.status === 'partial' ? 'Partial' : message.status === 'error' ? 'Failed' : 'Complete';
    updateStatus(`${label}${formatRunCounts(message)}`, 100);
    updateCacheStats();
    endTranslationRun(true);
  } else if (message.type === 'TRANSLATION_STOPPED') {
    updateStatus(`Stopped${formatRunCounts(message)}`, 100);
    endTranslationRun(true);
  } else if (message.type === 'TRANSLATION_ERROR') {
    showToast('Error: ' + message.error, 'error');
    elements.statusSection.style.display = 'none';
    endTranslationRun(false);
  }
});

function updateApiKeyWarning(settings) {
  const section = document.getElementById('api-warning-section');
  const link = document.getElementById('api-warning-link');
  const message = section?.querySelector('p');
  if (!section) return;

  const translator = resolveTranslator(settings);
  const apiKey = settings.apiKey || '';
  const needsKey = translatorNeedsApiKey(translator);

  if (needsKey && !apiKey) {
    section.style.display = 'block';
    if (message) {
      const warningText = settings.pipelineMode === 'local'
        ? `Local backend is using ${translator}. Configure an API key in Settings, or make sure the backend .env provides it. `
        : 'No translate API key is configured. ';
      const textNode = Array.from(message.childNodes).find(node => node.nodeType === Node.TEXT_NODE);
      if (textNode) {
        textNode.textContent = warningText;
      } else {
        message.insertBefore(document.createTextNode(warningText), message.firstChild);
      }
    }
    if (link) {
      link.onclick = (e) => {
        e.preventDefault();
        chrome.runtime.openOptionsPage();
      };
    }
  } else {
    section.style.display = 'none';
  }
}

async function refreshApiKeyWarning() {
  try {
    updateApiKeyWarning(await loadCurrentSettings());
  } catch (error) {
    console.error('Error refreshing API key warning:', error);
  }
}

document.addEventListener('DOMContentLoaded', init);
