/**
 * Shared domain types for the extension: settings, API keys, translation
 * results, cache statistics, and the small structures passed between contexts.
 *
 * Nothing here has runtime behaviour. Defaults and normalization live in
 * `lib/settings.ts`; message shapes live in `shared/messages.ts`.
 */

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

export type PipelineMode = 'local' | 'cloud';

/** Cloud OCR/translation providers selectable in the options page. */
export type Provider = 'gemini' | 'openai' | 'openrouter' | 'custom' | 'local';

/**
 * Translator sent to the local backend. `''` in settings means "use the
 * provider setting"; `none` runs OCR only.
 */
export type Translator = Provider | 'none';

export type Detector = 'default' | 'rtdetr' | 'animetext-yolo';
export type Inpainter = 'flux2_klein_lanpaint' | 'flux2_klein_q4' | 'lama_large' | 'none';
export type OcrEngine = 'manga-ocr' | 'paddle-ocr' | 'google-ocr';
export type TextAlignment = 'left' | 'center' | 'right';
export type TextDirection = 'auto' | 'horizontal' | 'vertical';

export interface LocalBackendSettings {
  /** Loopback HTTP base URL of the Python backend. */
  url: string;
  /** Backend access token. Private: stored in `apiKeys`, never synced. */
  authToken: string;
  detector: Detector;
  ocr: OcrEngine;
  inpainter: Inpainter;
  /** `''` falls back to the provider setting. */
  translator: Translator | '';
  requestTimeoutMs: number;
}

export interface GoogleOcrSettings {
  /** Private: stored in `apiKeys`, never synced. */
  apiKey: string;
  languageHint: string;
}

export interface RenderSettings {
  textAlignment: TextAlignment;
  textDirection: TextDirection;
  uppercase: boolean;
  lineSpacing: number;
  borderSize: number;
  textColor: string;
  borderColor: string;
  bgColor: string;
  bgOpacity: number;
  glowEnabled: boolean;
  glowSize: number;
  glowColor: string;
  autoScale: boolean;
}

export interface LocalLlmSettings {
  baseUrl: string;
  model: string;
  /** Private: stored in `apiKeys`, never synced. */
  apiKey: string;
}

/** Full in-memory settings, including the private key material. */
export interface Settings {
  settingsVersion: number;
  provider: Provider;
  /** Private: stored in `apiKeys`, never synced. */
  apiKey: string;
  model: string;
  baseUrl: string;
  targetLang: string;
  fontSize: number;
  fontFamily: string;
  autoTranslate: boolean;
  showProgress: boolean;
  pipelineMode: PipelineMode;
  localBackend: LocalBackendSettings;
  googleOcr: GoogleOcrSettings;
  render: RenderSettings;
  localLlm: LocalLlmSettings;
  debug: boolean;
}

/** Settings as persisted in `chrome.storage.sync` under `settings`: no keys. */
export type PublicSettings = Omit<
  Settings,
  'apiKey' | 'localBackend' | 'googleOcr' | 'localLlm'
> & {
  localBackend: Omit<LocalBackendSettings, 'authToken'>;
  googleOcr: Omit<GoogleOcrSettings, 'apiKey'>;
  localLlm: Omit<LocalLlmSettings, 'apiKey'>;
};

/** Partial, untrusted settings (storage contents, messages) accepted by `mergeSettings`. */
export interface SettingsInput
  extends Partial<Omit<Settings, 'localBackend' | 'googleOcr' | 'render' | 'localLlm'>> {
  localBackend?: Partial<LocalBackendSettings> | null;
  googleOcr?: Partial<GoogleOcrSettings> | null;
  render?: Partial<RenderSettings> | null;
  localLlm?: Partial<LocalLlmSettings> | null;
}

/** `chrome.storage.local` `apiKeys` record. Missing members mean "not stored". */
export interface ApiKeys {
  apiKey?: string;
  googleOcrApiKey?: string;
  localLlmApiKey?: string;
  backendAuthToken?: string;
}

// ---------------------------------------------------------------------------
// Translation results
// ---------------------------------------------------------------------------

export type BoundingBox = [left: number, top: number, right: number, bottom: number];

/** One normalized text block returned by a cloud provider (percent coordinates). */
export interface TranslationBlock {
  translated: string;
  bbox: BoundingBox;
  bubble_bbox?: BoundingBox;
  bubble_center?: [x: number, y: number];
}

/** Cloud pipeline result: text blocks the content script draws as an SVG overlay. */
export interface CloudTranslationResult {
  mode: 'cloud';
  blocks: TranslationBlock[];
}

/** Local pipeline result: the backend returned a fully rendered image. */
export interface LocalTranslationResult {
  mode: 'local';
  /** Base64 (no data: prefix) of the rendered image. */
  translatedImage: string;
  mimeType: string;
  inpainterUsed: string;
  /** True when the backend ran a different inpainter than requested; never cached. */
  inpainterFallback: boolean;
  translationFailed?: boolean;
}

/**
 * Result of a TRANSLATE_IMAGE request. A bare block array is the legacy cache
 * format and must still be accepted when read back.
 */
export type TranslationResult =
  | CloudTranslationResult
  | LocalTranslationResult
  | TranslationBlock[];

// ---------------------------------------------------------------------------
// Cache and progress
// ---------------------------------------------------------------------------

export interface CacheEntry<T = unknown> {
  timestamp: number;
  lastAccessed?: number;
  data: T;
}

export interface CacheStats {
  count: number;
  sizeBytes: number;
  sizeKB: string;
  oldestEntry?: string | null;
  newestEntry?: string | null;
}

/** Value mirrored by the popup into `chrome.storage.session` under `translationProgress`. */
export interface TranslationProgress {
  status: string;
  percent: number;
  tabId: number | null;
  timestamp: number;
}

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------

export interface Size {
  width: number;
  height: number;
}

/** A rectangle in CSS pixels relative to the viewport. */
export interface ViewportRect extends Size {
  left: number;
  top: number;
}

/** A rectangle in whole screenshot pixels. */
export interface PixelRegion extends Size {
  x: number;
  y: number;
}

export interface Point {
  x: number;
  y: number;
}

// ---------------------------------------------------------------------------
// Backend health
// ---------------------------------------------------------------------------

export type BackendHealthReason =
  | 'ok'
  | 'unreachable'
  | 'unauthorized'
  | 'auth_unconfigured'
  | 'loading'
  | 'degraded'
  | 'error';

// ---------------------------------------------------------------------------
// Site activation
// ---------------------------------------------------------------------------

export interface SiteActivationState {
  activeUrls: string[];
  disabledUrls: string[];
}

export interface SiteState extends SiteActivationState {
  hostname: string;
  enabled: boolean;
  supported: boolean;
}
