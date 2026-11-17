/**
 * Messages the content script answers: commands from the popup and the
 * worker's context menus, settings and site-activation updates, and
 * per-image error reports from the worker.
 */
import { mergeSettings } from '../lib/settings';
import { SITE_GATED_COMMAND_TYPES, type TabMessage } from '../shared/messages';
import type { SettingsInput } from '../shared/types';
import { clearPageTranslations, requestStopTranslation } from './clear';
import { LOG_PREFIX } from './constants';
import { resolveImageDataBySource } from './discovery';
import { startRegionSelection } from './region';
import { refreshSiteActivation } from './runtime';
import { loadSettings } from './settings-store';
import { state } from './state';
import { handleTranslationError, translateImageData, translatePage } from './translate';
import { getErrorMessage } from './utils';

type SendResponse = (response?: unknown) => void;

function isTabMessage(message: unknown): message is TabMessage {
  return (
    typeof message === 'object' &&
    message !== null &&
    typeof (message as { type?: unknown }).type === 'string'
  );
}

/**
 * `runtime.onMessage` listener. Returns true when it answers (possibly
 * asynchronously) through `sendResponse`, false for messages it does not own.
 */
export function handleMessage(
  message: unknown,
  _sender: unknown,
  sendResponse: SendResponse,
): boolean {
  if (!isTabMessage(message)) return false;

  if (!state.siteEnabled && SITE_GATED_COMMAND_TYPES.includes(message.type)) {
    sendResponse({ success: false, error: 'Manga Translator is disabled on this site' });
    return true;
  }

  switch (message.type) {
    case 'TRANSLATE_PAGE':
      sendResponse({ success: true });
      setTimeout(() => void translatePage(), 0);
      return true;
    case 'TRANSLATE_VISIBLE_IMAGES':
      sendResponse({ success: true });
      setTimeout(() => void translatePage({ visibleOnly: true }), 0);
      return true;
    case 'START_REGION_SELECTION':
      sendResponse({ success: true });
      setTimeout(() => startRegionSelection(), 0);
      return true;
    case 'CLEAR_PAGE_TRANSLATIONS':
      sendResponse(clearPageTranslations());
      return true;
    case 'STOP_TRANSLATION':
      sendResponse(requestStopTranslation());
      return true;
    case 'TRANSLATE_IMAGE_BY_SRC':
      translateImageData(resolveImageDataBySource(message.src), { bypassRetryBlock: true })
        .then(sendResponse)
        .catch((error: unknown) => {
          console.error(`${LOG_PREFIX} Error translating selected image:`, error);
          sendResponse({ success: false, error: getErrorMessage(error) });
        });
      return true;
    case 'SETTINGS_UPDATED':
      loadSettings()
        .then(() => sendResponse({ success: true }))
        .catch((error: unknown) => {
          console.error(`${LOG_PREFIX} Error refreshing settings:`, error);
          state.settings = mergeSettings({
            ...(state.settings || {}),
            ...(message.settings || {}),
          } as SettingsInput);
          sendResponse({ success: false, error: getErrorMessage(error) });
        });
      return true;
    case 'SITE_ACTIVATION_CHANGED':
      refreshSiteActivation()
        .then((enabled) => sendResponse({ success: true, enabled }))
        .catch((error: unknown) => sendResponse({ success: false, error: getErrorMessage(error) }));
      return true;
    case 'TRANSLATION_ERROR':
      handleTranslationError(message.imageId, message.error);
      sendResponse({ success: true });
      return true;
    default:
      return false;
  }
}
