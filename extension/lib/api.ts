/**
 * Provider abstraction layer for Manga Translator.
 *
 * - `TranslationProvider`: direct cloud OCR+translation (Gemini, OpenAI,
 *   OpenRouter, custom OpenAI-compatible endpoint) returning text blocks.
 * - `LocalBackendProvider`: the local Python backend, returning a rendered image.
 */
import type {
  BackendHealthReason,
  BoundingBox,
  RenderSettings,
  TranslationBlock,
} from '../shared/types';
import { isLoopbackHostname } from './settings';

const DEFAULT_LOCAL_BACKEND_TIMEOUT_MS = 600000;
const MIN_LOCAL_BACKEND_TIMEOUT_MS = 180000;
const MAX_LOCAL_BACKEND_TIMEOUT_MS = 600000;
const CLOUD_REQUEST_TIMEOUT_MS = 30000;
const HEALTH_CHECK_TIMEOUT_MS = 5000;

const MAX_ERROR_BODY_CHARS = 300;
const GEMINI_BLOCKED_FINISH_REASONS = new Set(['SAFETY', 'BLOCKLIST', 'PROHIBITED_CONTENT']);

function toNumbers(value: unknown): number[] | null {
  return Array.isArray(value) ? value.map(Number) : null;
}

/**
 * Keep provider output at the small contract consumed by the overlay renderer.
 * Providers frequently include `original`, `text`, or malformed boxes; those
 * fields must never leak into the rendering path.
 */
export function normalizeTranslationBlocks(blocks: unknown): TranslationBlock[] {
  if (!Array.isArray(blocks)) return [];

  return blocks.flatMap((block: unknown): TranslationBlock[] => {
    if (!block || typeof block !== 'object') return [];
    const raw = block as Record<string, unknown>;

    const translatedValue = raw.translated ?? raw.translated_text;
    if (typeof translatedValue !== 'string' || !translatedValue.trim()) return [];

    const bbox = toNumbers(raw.bbox);
    if (bbox?.length !== 4 || bbox.some((value) => !Number.isFinite(value))) return [];

    const [left, top, right, bottom] = bbox as BoundingBox;
    if (left < 0 || top < 0 || right < 0 || bottom < 0 || right <= left || bottom <= top) {
      return [];
    }

    const normalized: TranslationBlock = {
      translated: translatedValue.trim(),
      bbox: bbox as BoundingBox,
    };
    const bubbleBox = toNumbers(raw.bubble_bbox);
    if (
      bubbleBox?.length === 4 &&
      bubbleBox.every((value) => Number.isFinite(value)) &&
      (bubbleBox[0] as number) >= 0 &&
      (bubbleBox[1] as number) >= 0 &&
      (bubbleBox[2] as number) > (bubbleBox[0] as number) &&
      (bubbleBox[3] as number) > (bubbleBox[1] as number)
    ) {
      normalized.bubble_bbox = bubbleBox as BoundingBox;
    }
    const bubbleCenter = toNumbers(raw.bubble_center);
    if (bubbleCenter?.length === 2 && bubbleCenter.every((value) => Number.isFinite(value))) {
      normalized.bubble_center = bubbleCenter as [number, number];
    }
    return [normalized];
  });
}

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
 * Truncate an upstream error body and mask anything that looks like a credential.
 * Masking runs before truncation so a cut can never leave half a key behind.
 */
export function sanitizeErrorBody(
  text: unknown,
  secrets: ReadonlyArray<string | null | undefined> = [],
): string {
  let body = typeof text === 'string' ? text : String(text ?? '');

  for (const secret of secrets) {
    if (typeof secret === 'string' && secret.trim() !== '') {
      body = body.split(secret.trim()).join('[redacted]');
    }
  }
  body = body
    .replace(/Bearer\s+\S+/g, '[redacted]')
    .replace(/sk-[A-Za-z0-9_-]{8,}/g, '[redacted]')
    .replace(/AIza[0-9A-Za-z_-]{20,}/g, '[redacted]');

  return truncateErrorBody(body);
}

export interface Base64ImageInput {
  data: string;
  mimeType: string;
}

/** Split a raw base64 string or `data:` URL into payload and MIME type. */
export function normalizeBase64ImageInput(
  base64Image: unknown,
  defaultMimeType = 'image/png',
): Base64ImageInput {
  const input = typeof base64Image === 'string' ? base64Image.trim() : '';
  const dataUrlMatch = input.match(/^data:([^;,]+);base64,(.*)$/is);

  return dataUrlMatch
    ? { data: dataUrlMatch[2] ?? '', mimeType: dataUrlMatch[1] || defaultMimeType }
    : { data: input, mimeType: defaultMimeType };
}

const PROVIDER_BASE_URLS: Record<string, string> = {
  gemini: 'https://generativelanguage.googleapis.com/v1beta',
  openrouter: 'https://openrouter.ai/api/v1',
  openai: 'https://api.openai.com/v1',
};

// Vision-capable defaults for direct cloud mode.
const PROVIDER_DEFAULT_MODELS: Record<string, string> = {
  gemini: 'gemini-2.0-flash',
  openai: 'gpt-4o-mini',
  openrouter: 'google/gemini-2.0-flash-exp:free',
};

export interface TranslationProviderConfig {
  provider?: string;
  apiKey?: string;
  model?: string;
  baseUrl?: string;
}

interface GeminiResponse {
  candidates?: Array<{
    finishReason?: string;
    content?: { parts?: Array<{ text?: unknown }> };
  }>;
}

interface OpenAiResponse {
  choices?: Array<{ message?: { content?: unknown } }>;
}

/** Direct cloud provider: one request OCRs and translates a whole image. */
export class TranslationProvider {
  provider: string;
  apiKey: string | undefined;
  model: string;
  baseUrl: string;

  constructor(config: TranslationProviderConfig) {
    this.provider = config.provider || 'gemini';
    this.apiKey = config.apiKey;
    this.model = config.model || PROVIDER_DEFAULT_MODELS[this.provider] || '';
    this.baseUrl = config.baseUrl || PROVIDER_BASE_URLS[this.provider] || '';
  }

  /** Auth headers for the provider. */
  getHeaders(): Record<string, string> {
    switch (this.provider) {
      case 'gemini':
        return {
          'Content-Type': 'application/json',
          'x-goog-api-key': this.apiKey as string,
        };
      case 'openrouter':
        return {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${this.apiKey}`,
          'HTTP-Referer': 'chrome-extension://manga-translator',
          'X-Title': 'Manga Translator',
        };
      case 'openai':
      case 'custom':
        return {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${this.apiKey}`,
        };
      default:
        return { 'Content-Type': 'application/json' };
    }
  }

  validateApiKey(): void {
    if (!this.apiKey || typeof this.apiKey !== 'string' || this.apiKey.trim() === '') {
      throw new Error('API key is missing or invalid');
    }
  }

  /** OCR and translate an image in one call; returns normalized text blocks. */
  async translateImage(base64Image: string, targetLang = 'English'): Promise<TranslationBlock[]> {
    const prompt = this.buildTranslationPrompt(targetLang);

    switch (this.provider) {
      case 'gemini':
        return await this.callGemini(base64Image, prompt);
      case 'openai':
      case 'openrouter':
      case 'custom':
        return await this.callOpenAICompatible(base64Image, prompt);
      default:
        throw new Error(`Unknown provider: ${this.provider}`);
    }
  }

  buildTranslationPrompt(targetLang: string): string {
    return `You are a manga translator. Analyze this manga panel and:
1. Extract all Japanese/Korean/Chinese text (speech bubbles, narration, sound effects)
2. Translate each text to ${targetLang}
3. Return the result as a JSON array

Return format:
[{
  "bbox": [x1, y1, x2, y2],
  "original": "original text",
  "translated": "translated text",
  "type": "bubble|narration|sfx"
}]

Where:
- bbox: Bounding box as percentages [left, top, right, bottom] (0-100)
- original: Original text from the image
- translated: Translation in ${targetLang}
- type: Text type (bubble/narration/sfx)

Guidelines:
- Preserve character voice and tone
- Keep translations concise to fit speech bubbles
- For sound effects, include both transliteration and meaning if applicable
- Return ONLY the JSON array, no markdown or explanation`;
  }

  async callGemini(base64Image: string, prompt: string): Promise<TranslationBlock[]> {
    this.validateApiKey();
    const url = `${this.baseUrl}/models/${this.model}:generateContent`;
    const imageInput = normalizeBase64ImageInput(base64Image);

    const body = {
      contents: [
        {
          parts: [
            { text: prompt },
            {
              inline_data: {
                mime_type: imageInput.mimeType,
                data: imageInput.data,
              },
            },
          ],
        },
      ],
      generationConfig: {
        temperature: 0.1,
        topP: 0.95,
        maxOutputTokens: 4096,
      },
    };

    return this.parseGeminiResponse(await this.postJson(url, body, 'Gemini API error'));
  }

  /** OpenAI, OpenRouter and custom OpenAI-compatible endpoints. */
  async callOpenAICompatible(base64Image: string, prompt: string): Promise<TranslationBlock[]> {
    this.validateApiKey();
    const url = `${this.baseUrl}/chat/completions`;
    const imageInput = normalizeBase64ImageInput(base64Image);

    const body = {
      model: this.model,
      messages: [
        {
          role: 'user',
          content: [
            { type: 'text', text: prompt },
            {
              type: 'image_url',
              image_url: {
                url: `data:${imageInput.mimeType};base64,${imageInput.data}`,
              },
            },
          ],
        },
      ],
      temperature: 0.1,
      max_tokens: 4096,
    };

    return this.parseOpenAIResponse(await this.postJson(url, body, 'API error'));
  }

  private async postJson(url: string, body: unknown, errorLabel: string): Promise<unknown> {
    const response = await fetch(url, {
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(CLOUD_REQUEST_TIMEOUT_MS),
    });

    if (!response.ok) {
      const error = sanitizeErrorBody(await response.text(), [this.apiKey]);
      throw new Error(`${errorLabel}: ${response.status} - ${error}`);
    }

    return response.json();
  }

  parseGeminiResponse(data: unknown): TranslationBlock[] {
    const candidate = (data as GeminiResponse | null | undefined)?.candidates?.[0];
    const finishReason = candidate?.finishReason;
    return this.parseBlocks(
      candidate?.content?.parts?.[0]?.text,
      'Gemini',
      finishReason && GEMINI_BLOCKED_FINISH_REASONS.has(finishReason)
        ? `Gemini returned no text (${finishReason})`
        : 'Gemini returned no parseable JSON array',
    );
  }

  parseOpenAIResponse(data: unknown): TranslationBlock[] {
    return this.parseBlocks(
      (data as OpenAiResponse | null | undefined)?.choices?.[0]?.message?.content,
      'API',
      'API returned no parseable JSON array',
    );
  }

  private parseBlocks(text: unknown, label: string, emptyMessage: string): TranslationBlock[] {
    try {
      const blocks = typeof text === 'string' && text.trim() ? this.extractJSON(text) : null;
      if (!blocks) {
        throw new Error(emptyMessage);
      }
      return normalizeTranslationBlocks(blocks);
    } catch (error) {
      console.error(`Error parsing ${label} response:`, error);
      throw new Error('Failed to parse translation response');
    }
  }

  /**
   * Find the first JSON array in `text` that normalizes to blocks. Returns
   * `null` (not `[]`) when nothing parseable is found, so a bad reply is never
   * mistaken for a legitimately empty page and cached.
   */
  extractJSON(text: unknown): TranslationBlock[] | null {
    const source = typeof text === 'string' ? text : '';

    for (let start = source.indexOf('['); start !== -1; start = source.indexOf('[', start + 1)) {
      let depth = 0;
      let inString = false;
      let escaped = false;

      for (let i = start; i < source.length; i++) {
        const char = source[i];

        if (inString) {
          if (escaped) {
            escaped = false;
          } else if (char === '\\') {
            escaped = true;
          } else if (char === '"') {
            inString = false;
          }
          continue;
        }

        if (char === '"') {
          inString = true;
          continue;
        }
        if (char === '[') {
          depth += 1;
          continue;
        }
        if (char !== ']') {
          continue;
        }

        depth -= 1;
        if (depth === 0) {
          const candidate = source.slice(start, i + 1);
          try {
            const parsed: unknown = JSON.parse(candidate);
            const normalized = normalizeTranslationBlocks(parsed);
            // A literal [] is a legitimately empty page. A non-empty array that
            // normalises to nothing (wrong keys, [1], prose brackets) is not.
            if (normalized.length > 0 || (Array.isArray(parsed) && parsed.length === 0)) {
              return normalized;
            }
          } catch {
            // Not JSON; try the next '[' candidate.
          }
          break;
        }
      }
    }

    return null;
  }
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
 * objects it accepts flat aliases (`localBackendUrl`, `googleOcrApiKey`, ...).
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
  googleOcr?: { apiKey?: string; languageHint?: string };
  googleOcrApiKey?: string;
  googleOcrLanguageHint?: string;
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
  googleOcrApiKey: string;
  googleOcrLanguageHint: string;
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
    this.googleOcrApiKey = config.googleOcr?.apiKey || config.googleOcrApiKey || '';
    this.googleOcrLanguageHint =
      config.googleOcr?.languageHint || config.googleOcrLanguageHint || '';
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

    if (this.googleOcrApiKey && ocr === 'google-ocr') {
      fields.google_ocr_api_key = this.googleOcrApiKey;
      fields.google_ocr_language_hint = this.googleOcrLanguageHint;
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
