/**
 * Provider paths used by translation jobs: the local backend, plus the backend
 * health check.
 */
import { LocalBackendProvider, type LocalBackendProviderConfig } from '../lib/api';
import { mergeSettings } from '../lib/settings';
import type { ProgressNotifier } from '../lib/translation-queue';
import { base64ToBlob, blobToBase64 } from '../lib/utils';
import type { CheckBackendResponse } from '../shared/messages';
import type { LocalTranslationResult, Settings, SettingsInput } from '../shared/types';

/** Build a `LocalBackendProvider` from normalized settings (plus per-call extras). */
function createLocalBackendProvider(
  settings: Settings,
  extra: LocalBackendProviderConfig = {},
): LocalBackendProvider {
  return new LocalBackendProvider({
    localBackend: settings.localBackend,
    apiKey: settings.apiKey,
    authToken: settings.localBackend?.authToken,
    provider: settings.provider,
    model: settings.model,
    targetLang: settings.targetLang,
    ...extra,
  });
}

/** Check that the local backend is reachable and ready; `reason` says why not. */
export async function handleCheckBackend(
  settings: SettingsInput | undefined,
): Promise<CheckBackendResponse> {
  const provider = createLocalBackendProvider(mergeSettings(settings || {}));

  const ok = await provider.checkHealth();
  return { success: ok, reason: provider.lastHealthReason || (ok ? 'ok' : 'unreachable') };
}

function getLocalBackendRequestSuffix(error: unknown, provider: LocalBackendProvider): string {
  const requestId =
    (error as { requestId?: string } | null)?.requestId || provider?.lastRequestId || '';
  if (!requestId || String((error as Error)?.message || '').includes(requestId)) return '';
  return ` (request id: ${requestId})`;
}

function isTimeoutError(error: unknown): boolean {
  const message = String((error as Error)?.message || error || '').toLowerCase();
  const name = String((error as Error)?.name || '').toLowerCase();
  return (
    name === 'timeouterror' ||
    name === 'aborterror' ||
    message.includes('timeout') ||
    message.includes('timed out')
  );
}

function isLocalBackendNetworkError(error: unknown): boolean {
  const message = String((error as Error)?.message || error || '').toLowerCase();
  const name = String((error as Error)?.name || '').toLowerCase();
  return (
    name === 'networkerror' ||
    message.includes('failed to fetch') ||
    message.includes('networkerror') ||
    message.includes('network error') ||
    message.includes('network error in worker')
  );
}

/**
 * Translate with the local backend, which returns a fully rendered image.
 * Failures are rewritten into actionable messages.
 */
export async function handleLocalBackendTranslation(
  base64: string,
  settings: Settings,
  notify: ProgressNotifier,
): Promise<LocalTranslationResult> {
  const normalizedSettings = mergeSettings(settings || {});
  const provider = createLocalBackendProvider(normalizedSettings, {
    fontSize: normalizedSettings.fontSize,
    render: normalizedSettings.render,
    localLlm: normalizedSettings.localLlm,
  });

  const backendUrl = normalizedSettings.localBackend.url;

  try {
    notify('Detecting text...', 10);

    const imageBlob = base64ToBlob(base64);

    notify('Extracting text...', 30);
    notify('Translating...', 50);
    notify('Cleaning image...', 70);

    // All pipeline steps happen on the server.
    const translatedBlob = await provider.translateImage(imageBlob);

    notify('Rendering...', 90);

    const translatedBase64 = await blobToBase64(translatedBlob);
    if (!translatedBase64 || translatedBase64.length < 100) {
      throw new Error('Local backend returned an empty translated image');
    }

    return {
      translatedImage: translatedBase64,
      mimeType: translatedBlob.type || 'image/png',
      mode: 'local',
      inpainterUsed: translatedBlob.inpainterUsed || normalizedSettings.localBackend.inpainter,
      inpainterFallback: Boolean(translatedBlob.inpainterFallback),
    };
  } catch (error) {
    const requestSuffix = getLocalBackendRequestSuffix(error, provider);
    const message = (error as Error)?.message ?? String(error);
    // The backend answered with an HTTP error: its own detail (including a
    // provider "timed out" 504) is the real cause, so do not reinterpret it as
    // a local request timeout or connection failure.
    if ((error as { backendStatus?: number } | null)?.backendStatus) {
      throw new Error(`${message}${requestSuffix}`);
    }
    if (isTimeoutError(error)) {
      const timeoutSeconds = Math.round(provider.requestTimeoutMs / 1000);
      throw new Error(
        `Local backend request timed out after ${timeoutSeconds}s. The image may be too large or the server is busy.${requestSuffix}`,
      );
    }
    if (isLocalBackendNetworkError(error)) {
      throw new Error(
        `Cannot connect to local backend at ${backendUrl}. Worker fetch failed: ${message}${requestSuffix}`,
      );
    }
    if (message.includes('ECONNREFUSED') || message.includes('Connection refused')) {
      throw new Error(
        `Connection refused to ${backendUrl}. Please start the local translation server.${requestSuffix}`,
      );
    }
    throw new Error(`Local backend error: ${message}${requestSuffix}`);
  }
}
