/**
 * Typed message contract between the content script, popup/options pages and
 * the service worker.
 *
 * - `WorkerRequest`: messages the worker answers through `sendResponse`
 *   (`sendToWorker` gives each one a typed response).
 * - `TabMessage`: commands and notices sent to a tab's content script
 *   (`sendToTab`).
 * - `PopupMessage`: broadcasts from the content script/worker to the popup
 *   (`notifyPopup`).
 *
 * Message `type` strings and payload fields are a persisted contract: do not
 * rename them (see docs/RUNTIME_CONTRACT.md).
 */
import { browser } from 'wxt/browser';
import type {
  BackendHealthReason,
  CacheStats,
  PublicSettings,
  SettingsInput,
  Size,
  TranslationResult,
  ViewportRect,
} from './types';

// ---------------------------------------------------------------------------
// Requests handled by the service worker
// ---------------------------------------------------------------------------

/** Translate one image (base64 or data URL). `settings` includes API keys. */
export interface TranslateImageRequest {
  type: 'TRANSLATE_IMAGE';
  imageId: string;
  base64: string;
  settings: SettingsInput;
  /** Stable signature that replaces the image hash for canvas sources. */
  canvasSignature?: string;
  /** Bypass the cache and in-flight de-duplication. */
  forceRefresh?: boolean;
}

/** Fetch an http(s) image through the extension context (bypasses page CORS). */
export interface FetchImageRequest {
  type: 'FETCH_IMAGE';
  url: string;
}

export interface ClearCacheRequest {
  type: 'CLEAR_CACHE';
}

export interface GetCacheStatsRequest {
  type: 'GET_CACHE_STATS';
}

/** Check the local backend /health endpoint using these settings. */
export interface CheckBackendRequest {
  type: 'CHECK_BACKEND';
  settings: SettingsInput;
}

/** Top-frame content script asks for a screenshot crop (CSS pixel rect + viewport size). */
export interface CaptureRegionRequest {
  type: 'CAPTURE_REGION';
  rect: ViewportRect;
  viewport: Size;
}

/** Ask for the bytes of a bundled font (`fontId` from the font registry). */
export interface GetBundledFontRequest {
  type: 'GET_BUNDLED_FONT';
  fontId: string;
}

export type WorkerRequest =
  | TranslateImageRequest
  | FetchImageRequest
  | ClearCacheRequest
  | GetCacheStatsRequest
  | CheckBackendRequest
  | CaptureRegionRequest
  | GetBundledFontRequest;

export type WorkerRequestType = WorkerRequest['type'];

/** Message types the worker handles; any other type is ignored. */
export const WORKER_REQUEST_TYPES: readonly WorkerRequestType[] = [
  'TRANSLATE_IMAGE',
  'FETCH_IMAGE',
  'CLEAR_CACHE',
  'GET_CACHE_STATS',
  'CHECK_BACKEND',
  'CAPTURE_REGION',
  'GET_BUNDLED_FONT',
];

/** `{success: false, error}` reply used by every worker request on failure. */
export interface FailureResponse {
  success: false;
  error: string;
}

export interface TranslateImageSuccess {
  success: true;
  result: TranslationResult;
  /** Present (true) when the result came from the translation cache. */
  fromCache?: true;
}

export interface Base64Success {
  success: true;
  /** Base64 without a `data:` prefix. */
  base64: string;
}

export interface CaptureRegionSuccess extends Base64Success {
  /** Dimensions of the cropped PNG in screenshot pixels. */
  width: number;
  height: number;
}

/** CHECK_BACKEND reply: `success` is whether the backend is ready; `reason` says why not. */
export interface CheckBackendResponse {
  success: boolean;
  reason: BackendHealthReason;
}

/** Response type for each worker request, keyed by message type. */
export interface WorkerResponseMap {
  TRANSLATE_IMAGE: TranslateImageSuccess | FailureResponse;
  FETCH_IMAGE: Base64Success | FailureResponse;
  CLEAR_CACHE: { success: true; cleared: number };
  GET_CACHE_STATS: { success: true; stats: CacheStats };
  CHECK_BACKEND: CheckBackendResponse;
  CAPTURE_REGION: CaptureRegionSuccess | FailureResponse;
  GET_BUNDLED_FONT: Base64Success | FailureResponse;
}

export type ResponseOf<T extends WorkerRequest> = WorkerResponseMap[T['type']];

/** Narrow an unknown runtime message to a request the worker handles. */
export function isWorkerRequest(message: unknown): message is WorkerRequest {
  const type = (message as { type?: unknown } | null | undefined)?.type;
  return typeof type === 'string' && (WORKER_REQUEST_TYPES as readonly string[]).includes(type);
}

/** Send a request to the service worker and get its typed response. */
export function sendToWorker<T extends WorkerRequest>(message: T): Promise<ResponseOf<T>> {
  return browser.runtime.sendMessage(message) as Promise<ResponseOf<T>>;
}

// ---------------------------------------------------------------------------
// Messages to a tab's content script
// ---------------------------------------------------------------------------

/** Translate all images on the page. */
export interface TranslatePageMessage {
  type: 'TRANSLATE_PAGE';
}

/** Translate images currently in view. */
export interface TranslateVisibleImagesMessage {
  type: 'TRANSLATE_VISIBLE_IMAGES';
}

/** Translate the right-clicked image (context menu). */
export interface TranslateImageBySrcMessage {
  type: 'TRANSLATE_IMAGE_BY_SRC';
  src: string | undefined;
}

/** Start the drag selector for region translation. */
export interface StartRegionSelectionMessage {
  type: 'START_REGION_SELECTION';
}

/** Remove translated overlays on the page. */
export interface ClearPageTranslationsMessage {
  type: 'CLEAR_PAGE_TRANSLATIONS';
}

/** Cancel the running page translation. */
export interface StopTranslationMessage {
  type: 'STOP_TRANSLATION';
}

/** Per-hostname activation changed: the content script re-reads it. */
export interface SiteActivationChangedMessage {
  type: 'SITE_ACTIVATION_CHANGED';
}

/** Public (synced) settings changed; never carries keys. */
export interface SettingsUpdatedMessage {
  type: 'SETTINGS_UPDATED';
  settings: PublicSettings;
}

/**
 * A translation error. Sent worker -> tab with the failing `imageId`, and
 * content -> popup without one.
 */
export interface TranslationErrorMessage {
  type: 'TRANSLATION_ERROR';
  error: string;
  imageId?: string;
}

/** Progress for one image, sent worker -> tab; also content -> popup (see `PopupMessage`). */
export interface TranslationProgressMessage {
  type: 'TRANSLATION_PROGRESS';
  status: string;
  percent: number;
}

export type TabMessage =
  | TranslatePageMessage
  | TranslateVisibleImagesMessage
  | TranslateImageBySrcMessage
  | StartRegionSelectionMessage
  | ClearPageTranslationsMessage
  | StopTranslationMessage
  | SiteActivationChangedMessage
  | SettingsUpdatedMessage
  | TranslationErrorMessage
  | TranslationProgressMessage;

export type TabMessageType = TabMessage['type'];

/** Commands the content script rejects on sites where the extension is disabled. */
export const SITE_GATED_COMMAND_TYPES: readonly TabMessageType[] = [
  'TRANSLATE_PAGE',
  'TRANSLATE_VISIBLE_IMAGES',
  'TRANSLATE_IMAGE_BY_SRC',
  'START_REGION_SELECTION',
];

/** Response from the content script to each tab message, keyed by message type. */
export interface TabResponseMap {
  TRANSLATE_PAGE: { success: boolean; error?: string };
  TRANSLATE_VISIBLE_IMAGES: { success: boolean; error?: string };
  TRANSLATE_IMAGE_BY_SRC: { success: boolean; error?: string; stale?: boolean };
  START_REGION_SELECTION: { success: boolean; error?: string };
  CLEAR_PAGE_TRANSLATIONS: { success: boolean; cleared?: number; error?: string };
  STOP_TRANSLATION: { success: boolean; stopped?: boolean; error?: string };
  SITE_ACTIVATION_CHANGED: { success: boolean; enabled?: boolean; error?: string };
  SETTINGS_UPDATED: { success: boolean; error?: string };
  TRANSLATION_ERROR: { success: boolean };
  TRANSLATION_PROGRESS: undefined;
}

export type TabResponseOf<T extends TabMessage> = TabResponseMap[T['type']];

/** Send a message to the content script(s) of a tab and get its typed response. */
export function sendToTab<T extends TabMessage>(
  tabId: number,
  message: T,
): Promise<TabResponseOf<T>> {
  return browser.tabs.sendMessage(tabId, message) as Promise<TabResponseOf<T>>;
}

// ---------------------------------------------------------------------------
// Broadcasts to the popup
// ---------------------------------------------------------------------------

export type RunStatus = 'complete' | 'partial' | 'error';

/**
 * End of a translate run. Counts are absent for the notice sent after
 * clearing page translations.
 */
export interface TranslationCompleteMessage {
  type: 'TRANSLATION_COMPLETE';
  status?: RunStatus;
  total?: number;
  succeeded?: number;
  failed?: number;
  /** First failure reason when nothing succeeded. */
  error?: string;
}

/** A run was stopped by the user. */
export interface TranslationStoppedMessage {
  type: 'TRANSLATION_STOPPED';
  status: RunStatus;
  total: number;
  succeeded: number;
  failed: number;
}

export type PopupMessage =
  | TranslationProgressMessage
  | TranslationErrorMessage
  | TranslationCompleteMessage
  | TranslationStoppedMessage;

export type PopupMessageType = PopupMessage['type'];

/** Every message that travels between extension contexts. */
export type ExtensionMessage = WorkerRequest | TabMessage | PopupMessage;

/**
 * Broadcast a message to extension pages (the popup). Resolves quietly when no
 * page is listening.
 */
export function notifyPopup(message: PopupMessage): Promise<void> {
  return Promise.resolve(browser.runtime.sendMessage(message)).then(
    () => undefined,
    () => undefined,
  );
}

/** Type guard: does an unknown runtime message have this `type`? */
export function isMessageOfType<T extends ExtensionMessage['type']>(
  message: unknown,
  type: T,
): message is Extract<ExtensionMessage, { type: T }> {
  return (message as { type?: unknown } | null | undefined)?.type === type;
}
