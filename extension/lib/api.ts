/**
 * Provider abstraction layer for Manga Translator.
 *
 * `LocalBackendProvider`: the local Python backend, returning a rendered image.
 */
import type { BackendHealthReason, RenderSettings } from '../shared/types';
import { isLoopbackHostname } from './settings';

const DEFAULT_LOCAL_BACKEND_TIMEOUT_MS = 600000;
const MIN_LOCAL_BACKEND_TIMEOUT_MS = 180000;
const MAX_LOCAL_BACKEND_TIMEOUT_MS = 600000;
const HEALTH_CHECK_TIMEOUT_MS = 5000;

const MAX_ERROR_BODY_CHARS = 300;

/** Clamp the backend request timeout to 3-10 minutes; invalid values use the default. */
export function normalizeLocalBackendTimeoutMs(value: unknown): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return DEFAULT_LOCAL_BACKEND_TIMEOUT_MS;
  }

  return Math.max(
    MIN_LOCAL_BACKEND_TIMEOUT_MS,
    Math.min(MAX_LOCAL_BACKEND_TIMEOUT_MS, Math.round(numeric)),
  );
}

/** Bound an upstream error body so it cannot flood logs or the UI. */
export function truncateErrorBody(text: unknown, maxChars = MAX_ERROR_BODY_CHARS): string {
  const body = typeof text === 'string' ? text : String(text ?? '');
  return body.length > maxChars ? `${body.slice(0, maxChars)}...` : body;
}

/**
 * Build a backend URL from a base URL and path. Throws unless the base is a
 * loopback `http://` URL.
 */
export function buildLocalBackendUrl(baseUrl: string | undefined, path: string): string {
  let parsed: URL;
  try {
    parsed = new URL(baseUrl || 'http://localhost:8001');
  } catch {
    throw new Error('Invalid local backend URL');
  }

  if (parsed.protocol !== 'http:' || !isLoopbackHostname(parsed.hostname)) {
    throw new Error('Local backend URL must be http://localhost or http://127.0.0.1');
  }

  return `${parsed.origin}${parsed.pathname.replace(/\/$/, '')}${path}`;
}

/** Translator precedence: backend setting, pipeline settings, provider, then `openrouter`. */
export function resolveLocalBackendTranslator(
  localBackend: { translator?: string } = {},
  pipelineSettings: { translator?: string } = {},
  provider: string | undefined = 'openrouter',
): string {
  return localBackend.translator || pipelineSettings.translator || provider || 'openrouter';
}

/** Loosely-typed pipeline settings accepted by the `pipelineSettings` config alias. */
export interface PipelineSettingsInput {
  detector?: string;
  ocr?: string;
  inpainter?: string;
  translator?: string;
  targetLang?: string;
  provider?: string;
  model?: string;
  requestTimeoutMs?: number | string;
}

/**
 * Constructor input for `LocalBackendProvider`. Besides the nested settings
 * objects it accepts flat aliases (`localBackendUrl`, `localLlmBaseUrl`, ...).
 */
export interface LocalBackendProviderConfig {
  baseUrl?: string;
  localBackendUrl?: string;
  localBackend?: {
    url?: string;
    authToken?: string;
    detector?: string;
    ocr?: string;
    inpainter?: string;
    translator?: string;
    requestTimeoutMs?: number | string;
  };
  pipelineSettings?: PipelineSettingsInput;
  apiKey?: string;
  authToken?: string;
  backendAuthToken?: string;
  provider?: string;
  model?: string;
  localLlm?: { baseUrl?: string; apiKey?: string; model?: string };
  localLlmBaseUrl?: string;
  localLlmApiKey?: string;
  localLlmModel?: string;
  targetLang?: string;
  requestTimeoutMs?: number | string;
  fontSize?: number | string;
  render?: Partial<RenderSettings>;
  lineSpacing?: number;
  borderSize?: number;
}

/** The rendered image returned by the backend, annotated with response metadata. */
export type BackendImageBlob = Blob & {
  requestId: string;
  inpainterUsed: string;
  /** True when the backend ran a different inpainter than requested. */
  inpainterFallback: boolean;
};

/** Error for a failed local backend request. */
export class LocalBackendError extends Error {
  requestId: string;
  /** HTTP status when the backend answered with an error. */
  backendStatus: number | undefined;

  constructor(message: string, requestId = '', backendStatus?: number) {
    super(message);
    this.name = 'LocalBackendError';
    this.requestId = requestId;
    this.backendStatus = backendStatus;
  }
}

/** Local Python backend: one /translate call runs detect, OCR, translate, inpaint, render. */
export class LocalBackendProvider {
  baseUrl: string;
  apiKey: string;
  authToken: string;
  provider: string;
  model: string;
  hasBrowserApiKey: boolean;
  localLlmBaseUrl: string;
  localLlmApiKey: string;
  localLlmModel: string;
  targetLang: string;
  requestTimeoutMs: number;
  lastRequestId = '';
  lastHealthReason: BackendHealthReason = 'unreachable';
  renderSettings: {
    preferredFontSize: number;
    lineSpacing: number;
    borderSize: number;
    textColor: string;
    borderColor: string;
    bgColor: string;
    bgOpacity: number;
    textAlignment: string;
    textDirection: string;
    uppercase: boolean;
    autoScale: boolean;
  };
  pipelineSettings: {
    detector: string;
    ocr: string;
    inpainter: string;
    translator: string;
    targetLang: string;
  };

  constructor(config: LocalBackendProviderConfig = {}) {
    const localBackend = config.localBackend || {};
    const pipelineSettings = config.pipelineSettings || {};

    this.baseUrl =
      config.baseUrl || localBackend.url || config.localBackendUrl || 'http://localhost:8001';
    this.apiKey = config.apiKey || '';
    this.authToken = config.authToken || config.backendAuthToken || localBackend.authToken || '';
    this.provider = config.provider || pipelineSettings.provider || 'gemini';
    this.model = config.model || pipelineSettings.model || '';
    this.hasBrowserApiKey = Boolean(this.apiKey?.trim());
    this.localLlmBaseUrl = config.localLlm?.baseUrl || config.localLlmBaseUrl || '';
    this.localLlmApiKey = config.localLlm?.apiKey || config.localLlmApiKey || '';
    this.localLlmModel = config.localLlm?.model || config.localLlmModel || '';
    this.targetLang = config.targetLang || pipelineSettings.targetLang || 'English';
    this.requestTimeoutMs = normalizeLocalBackendTimeoutMs(
      localBackend.requestTimeoutMs ?? pipelineSettings.requestTimeoutMs ?? config.requestTimeoutMs,
    );

    this.renderSettings = {
      preferredFontSize: Number.isInteger(Number(config.fontSize))
        ? Math.max(6, Math.min(96, Number(config.fontSize)))
        : 16,
      lineSpacing: config.render?.lineSpacing ?? config.lineSpacing ?? 1.2,
      borderSize: config.render?.borderSize ?? config.borderSize ?? 2.0,
      textColor: config.render?.textColor || '#000000',
      borderColor: config.render?.borderColor || '#ffffff',
      bgColor: config.render?.bgColor || '#ffffff',
      bgOpacity: config.render?.bgOpacity ?? 0.9,
      textAlignment: config.render?.textAlignment || 'center',
      textDirection: config.render?.textDirection || 'auto',
      uppercase: !!config.render?.uppercase,
      autoScale: config.render?.autoScale !== false,
    };

    this.pipelineSettings = {
      detector: localBackend.detector || pipelineSettings.detector || 'default',
      ocr: localBackend.ocr || pipelineSettings.ocr || 'manga-ocr',
      inpainter: localBackend.inpainter || pipelineSettings.inpainter || 'flux2_klein_lanpaint',
      translator: resolveLocalBackendTranslator(localBackend, pipelineSettings, config.provider),
      targetLang: config.targetLang || pipelineSettings.targetLang || 'English',
    };
  }

  /** Translate an image with the local backend; resolves to the rendered image blob. */
  async translateImage(imageBlob: Blob): Promise<BackendImageBlob> {
    const { targetLang, detector, ocr, translator, inpainter } = this.pipelineSettings;
    const render = this.renderSettings;
    const fields: Record<string, string | number | boolean> = {
      target_lang: targetLang,
      detector,
      ocr,
      translator,
      inpainter,
      render: true,
      line_spacing: render.lineSpacing,
      border_size: render.borderSize,
      text_color: render.textColor,
      border_color: render.borderColor,
      bg_color: render.bgColor,
      bg_opacity: render.bgOpacity,
      text_alignment: render.textAlignment,
      text_direction: render.textDirection,
      uppercase: render.uppercase,
      auto_scale: render.autoScale,
      font_size: render.preferredFontSize,
    };

    if (translator === 'local') {
      fields.local_llm_base_url = this.localLlmBaseUrl;
      fields.local_llm_api_key = this.localLlmApiKey;
      fields.model = String(this.localLlmModel || '').trim();
    } else if (translator !== 'none' && this.hasBrowserApiKey) {
      fields.model = String(this.model || '').trim();
      fields.api_key = this.apiKey;
    }

    const formData = new FormData();
    formData.append('image', imageBlob, 'image.png');
    for (const [name, value] of Object.entries(fields)) {
      if (value !== '') formData.append(name, String(value));
    }

    this.lastRequestId = '';
    const response = await fetch(buildLocalBackendUrl(this.baseUrl, '/translate'), {
      method: 'POST',
      body: formData,
      ...this.authOptions(),
      signal: AbortSignal.timeout(this.requestTimeoutMs),
    });
    const requestId = response.headers.get('x-request-id') || '';
    const requestSuffix = requestId ? ` (request id: ${requestId})` : '';
    this.lastRequestId = requestId;

    if (!response.ok) {
      // The backend already redacts its error bodies; only bound the length here.
      const error = truncateErrorBody(await response.text());
      throw new LocalBackendError(
        `Local backend error: ${response.status} - ${error}${requestSuffix}`,
        requestId,
        response.status,
      );
    }

    const blob = await response.blob();
    const inpainterUsed =
      response.headers.get('x-inpainter-used') || this.pipelineSettings.inpainter;
    const contentType = response.headers.get('content-type') || blob.type || '';
    if (contentType && !contentType.toLowerCase().startsWith('image/')) {
      const text = await blob.text();
      throw new LocalBackendError(
        `Local backend returned ${contentType}: ${text.slice(0, 200)}${requestSuffix}`,
        requestId,
      );
    }

    return Object.assign(blob, {
      requestId,
      inpainterUsed,
      inpainterFallback: inpainterUsed !== this.pipelineSettings.inpainter,
    });
  }

  private authOptions(): RequestInit {
    return this.authToken ? { headers: { 'X-Manga-Translator-Token': this.authToken } } : {};
  }

  /**
   * Check that the backend is reachable and ready. The reason is left in
   * `lastHealthReason` (`ok`, `loading`, `degraded`, `unauthorized`,
   * `auth_unconfigured`, `error`, `unreachable`).
   */
  async checkHealth(): Promise<boolean> {
    this.lastHealthReason = 'unreachable';
    try {
      const response = await fetch(buildLocalBackendUrl(this.baseUrl, '/health'), {
        method: 'GET',
        ...this.authOptions(),
        signal: AbortSignal.timeout(HEALTH_CHECK_TIMEOUT_MS),
      });

      if (!response.ok) {
        // The server answered, so it is running; say why it refused.
        this.lastHealthReason =
          response.status === 401
            ? 'unauthorized'
            : response.status === 503
              ? 'auth_unconfigured'
              : 'error';
        return false;
      }
      const data = (await response.json().catch(() => null)) as {
        status?: string;
        models_loaded?: boolean;
      } | null;
      if (data?.status === 'loading') {
        this.lastHealthReason = 'loading';
        return false;
      }
      if (data?.models_loaded === false || data?.status === 'degraded') {
        this.lastHealthReason = 'degraded';
        return false;
      }
      this.lastHealthReason = 'ok';
      return true;
    } catch (error) {
      console.warn(
        'Local backend health check failed:',
        error instanceof Error ? error.message : error,
      );
      this.lastHealthReason = 'unreachable';
      return false;
    }
  }
}
