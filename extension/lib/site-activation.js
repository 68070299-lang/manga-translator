/**
 * Per-host activation state for the personal-use extension.
 *
 * An empty activeUrls list means all supported hosts are enabled for backwards
 * compatibility. Explicit disabledUrls entries are always respected.
 */
(function(root) {
  'use strict';

  const ACTIVE_URLS_KEY = 'activeUrls';
  const DISABLED_URLS_KEY = 'disabledUrls';

  function normalizeHostname(value) {
    if (typeof value !== 'string') return '';
    const trimmed = value.trim().toLowerCase();
    if (!trimmed) return '';

    try {
      const candidate = trimmed.includes('://') ? trimmed : `https://${trimmed}`;
      return new URL(candidate).hostname.toLowerCase();
    } catch (_error) {
      return trimmed.replace(/^\.+|\.+$/g, '').split('/')[0];
    }
  }

  function hostnameFromUrl(url) {
    try {
      return normalizeHostname(new URL(url).hostname);
    } catch (_error) {
      return normalizeHostname(url);
    }
  }

  function normalizeHostnameList(values) {
    if (!Array.isArray(values)) return [];
    return Array.from(new Set(values.map(normalizeHostname).filter(Boolean))).sort();
  }

  function normalizeState(stored = {}) {
    return {
      activeUrls: normalizeHostnameList(stored[ACTIVE_URLS_KEY]),
      disabledUrls: normalizeHostnameList(stored[DISABLED_URLS_KEY])
    };
  }

  function isSiteEnabledFromState(hostname, state) {
    const normalizedHostname = normalizeHostname(hostname);
    if (!normalizedHostname) return false;

    if (state.disabledUrls.includes(normalizedHostname)) return false;
    return state.activeUrls.length === 0 || state.activeUrls.includes(normalizedHostname);
  }

  async function readState() {
    const stored = await chrome.storage.local.get([ACTIVE_URLS_KEY, DISABLED_URLS_KEY]);
    return normalizeState(stored);
  }

  async function getSiteState(urlOrHostname) {
    const hostname = hostnameFromUrl(urlOrHostname);
    const state = await readState();
    const supported = isSupportedTarget(urlOrHostname);
    return {
      hostname,
      enabled: supported && isSiteEnabledFromState(hostname, state),
      supported,
      ...state
    };
  }

  async function setSiteEnabled(urlOrHostname, enabled) {
    const hostname = hostnameFromUrl(urlOrHostname);
    if (!hostname || !isSupportedTarget(urlOrHostname)) {
      throw new Error('Cannot change activation for an unsupported URL');
    }

    const state = await readState();
    const activeUrls = new Set(state.activeUrls);
    const disabledUrls = new Set(state.disabledUrls);

    if (enabled) {
      disabledUrls.delete(hostname);
      if (activeUrls.size > 0) activeUrls.add(hostname);
    } else {
      disabledUrls.add(hostname);
    }

    const nextState = {
      activeUrls: Array.from(activeUrls).sort(),
      disabledUrls: Array.from(disabledUrls).sort()
    };

    await chrome.storage.local.set(nextState);
    return {
      hostname,
      enabled: Boolean(enabled),
      ...nextState
    };
  }

  async function toggleSite(urlOrHostname) {
    const current = await getSiteState(urlOrHostname);
    return setSiteEnabled(urlOrHostname, !current.enabled);
  }

  function isSupportedUrl(url) {
    try {
      const parsed = new URL(url);
      return parsed.protocol === 'http:' || parsed.protocol === 'https:';
    } catch (_error) {
      return false;
    }
  }

  function isSupportedTarget(value) {
    if (isSupportedUrl(value)) return true;
    return typeof value === 'string' &&
      !value.includes('://') &&
      /^[a-z0-9.-]+$/i.test(value.trim());
  }

  const exported = {
    ACTIVE_URLS_KEY,
    DISABLED_URLS_KEY,
    normalizeHostname,
    hostnameFromUrl,
    normalizeHostnameList,
    normalizeState,
    isSiteEnabledFromState,
    readState,
    getSiteState,
    setSiteEnabled,
    toggleSite,
    isSupportedUrl,
    isSupportedTarget
  };

  if (root) root.MangaTranslatorSiteActivation = exported;
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
})(typeof globalThis !== 'undefined' ? globalThis : this);
