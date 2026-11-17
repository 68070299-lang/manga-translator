/**
 * Per-host activation state for the personal-use extension.
 *
 * An empty activeUrls list means all supported hosts are enabled for backwards
 * compatibility. Explicit disabledUrls entries are always respected.
 */
import { browser } from 'wxt/browser';
import type { SiteActivationState, SiteState } from '../shared/types';

export const ACTIVE_URLS_KEY = 'activeUrls';
export const DISABLED_URLS_KEY = 'disabledUrls';

/** Lower-case hostname from a URL or bare hostname; `''` when unusable. */
export function normalizeHostname(value: unknown): string {
  if (typeof value !== 'string') return '';
  const trimmed = value.trim().toLowerCase();
  if (!trimmed) return '';

  try {
    const candidate = trimmed.includes('://') ? trimmed : `https://${trimmed}`;
    return new URL(candidate).hostname.toLowerCase();
  } catch {
    return trimmed.replace(/^\.+|\.+$/g, '').split('/')[0] ?? '';
  }
}

export function hostnameFromUrl(url: string): string {
  try {
    return normalizeHostname(new URL(url).hostname);
  } catch {
    return normalizeHostname(url);
  }
}

/** Deduplicated, sorted list of normalized hostnames; non-arrays become `[]`. */
export function normalizeHostnameList(values: unknown): string[] {
  if (!Array.isArray(values)) return [];
  return Array.from(new Set(values.map(normalizeHostname).filter(Boolean))).sort();
}

export function normalizeState(stored: Record<string, unknown> = {}): SiteActivationState {
  return {
    activeUrls: normalizeHostnameList(stored[ACTIVE_URLS_KEY]),
    disabledUrls: normalizeHostnameList(stored[DISABLED_URLS_KEY]),
  };
}

export function isSiteEnabledFromState(hostname: string, state: SiteActivationState): boolean {
  const normalizedHostname = normalizeHostname(hostname);
  if (!normalizedHostname) return false;

  if (state.disabledUrls.includes(normalizedHostname)) return false;
  return state.activeUrls.length === 0 || state.activeUrls.includes(normalizedHostname);
}

/** Read the stored activation lists from `chrome.storage.local`. */
export async function readState(): Promise<SiteActivationState> {
  const stored = await browser.storage.local.get([ACTIVE_URLS_KEY, DISABLED_URLS_KEY]);
  return normalizeState(stored);
}

export function isSupportedUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

/** An http(s) URL, or a bare hostname-looking string. */
export function isSupportedTarget(value: string): boolean {
  if (isSupportedUrl(value)) return true;
  return typeof value === 'string' && !value.includes('://') && /^[a-z0-9.-]+$/i.test(value.trim());
}

/** Activation state of the site for a URL or hostname. */
export async function getSiteState(urlOrHostname: string): Promise<SiteState> {
  const hostname = hostnameFromUrl(urlOrHostname);
  const state = await readState();
  const supported = isSupportedTarget(urlOrHostname);
  return {
    hostname,
    enabled: supported && isSiteEnabledFromState(hostname, state),
    supported,
    ...state,
  };
}

/** Enable or disable one hostname. Throws for unsupported URLs. */
export async function setSiteEnabled(
  urlOrHostname: string,
  enabled: boolean,
): Promise<SiteActivationState & { hostname: string; enabled: boolean }> {
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

  const nextState: SiteActivationState = {
    activeUrls: Array.from(activeUrls).sort(),
    disabledUrls: Array.from(disabledUrls).sort(),
  };

  await browser.storage.local.set({ ...nextState });
  return { hostname, enabled: Boolean(enabled), ...nextState };
}

export async function toggleSite(
  urlOrHostname: string,
): Promise<SiteActivationState & { hostname: string; enabled: boolean }> {
  const current = await getSiteState(urlOrHostname);
  return setSiteEnabled(urlOrHostname, !current.enabled);
}
