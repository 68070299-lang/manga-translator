/** Pure helpers behind the popup's progress display and setup checks. */
import { resolveTranslator, translatorNeedsApiKey } from '../../lib/settings';
import type { PopupMessage } from '../../shared/messages';
import type { Settings, TranslationProgress } from '../../shared/types';

export const PROGRESS_STORAGE_KEY = 'translationProgress';
/** Stored progress older than this is dropped instead of restored. */
export const PROGRESS_STALE_MS = 60_000;
export const CONTENT_MESSAGE_TIMEOUT_MS = 8000;
/** How long a finished run's status stays visible. */
export const STATUS_HIDE_DELAY_MS = 2000;

export function isProgressFresh(progress: TranslationProgress, now = Date.now()): boolean {
  return now - progress.timestamp < PROGRESS_STALE_MS;
}

type CompleteMessage = Extract<PopupMessage, { type: 'TRANSLATION_COMPLETE' }>;
type StoppedMessage = Extract<PopupMessage, { type: 'TRANSLATION_STOPPED' }>;

/** Status word for a finished run: `Complete`, `Partial` or `Failed`. */
export function completionLabel(status: CompleteMessage['status']): string {
  if (status === 'partial') return 'Partial';
  if (status === 'error') return 'Failed';
  return 'Complete';
}

/** ` (2/3 images)` when the run reported a total, otherwise nothing. */
export function formatRunCounts(message: CompleteMessage | StoppedMessage): string {
  return Number.isFinite(message.total)
    ? ` (${message.succeeded || 0}/${message.total} images)`
    : '';
}

/** Why translation cannot start with these settings; `''` when it can. */
export function getTranslationSetupError(settings: Settings): string {
  if (!settings.localBackend.url) {
    return 'Please configure backend URL in Settings';
  }
  return '';
}

export const MISSING_KEY_TEXT = 'No translate API key is configured. ';

export interface ApiKeyWarning {
  visible: boolean;
  text: string;
}

/** The "configure an API key" banner for these settings. */
export function getApiKeyWarning(settings: Settings): ApiKeyWarning {
  const translator = resolveTranslator(settings);
  if (!translatorNeedsApiKey(translator) || settings.apiKey) {
    return { visible: false, text: MISSING_KEY_TEXT };
  }
  const text = `Local backend is using ${translator}. Configure an API key in Settings, or make sure the backend .env provides it. `;
  return { visible: true, text };
}
