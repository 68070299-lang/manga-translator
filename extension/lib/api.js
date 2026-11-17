/**
 * Provider Abstraction Layer for Manga Translator
 * Supports: Gemini, OpenRouter, Custom providers
 */

const DEFAULT_LOCAL_BACKEND_TIMEOUT_MS = 600000;
const MIN_LOCAL_BACKEND_TIMEOUT_MS = 180000;
const MAX_LOCAL_BACKEND_TIMEOUT_MS = 600000;

/**
 * Keep provider output at the small contract consumed by the overlay renderer.
 * Providers frequently include `original`, `text`, or malformed boxes; those
 * fields must never leak into the rendering path.
 */
function normalizeTranslationBlocks(blocks) {
  if (!Array.isArray(blocks)) return [];

  return blocks.flatMap(block => {
    if (!block || typeof block !== 'object') return [];

    const translatedValue = block.translated ?? block.translated_text;
    if (typeof translatedValue !== 'string' || !translatedValue.trim()) return [];

    const bbox = Array.isArray(block.bbox) ? block.bbox.map(Number) : null;
    if (!bbox || bbox.length !== 4 || bbox.some(value => !Number.isFinite(value))) return [];

    const [left, top, right, bottom] = bbox;
    if (left < 0 || top < 0 || right < 0 || bottom < 0 || right <= left || bottom <= top) {
      return [];
    }

    const normalized = {
      translated: translatedValue.trim(),
      bbox
    };
    const bubbleBox = Array.isArray(block.bubble_bbox)
      ? block.bubble_bbox.map(Number)
      : null;
    if (
      bubbleBox?.length === 4 &&
      bubbleBox.every(value => Number.isFinite(value)) &&
      bubbleBox[0] >= 0 && bubbleBox[1] >= 0 &&
      bubbleBox[2] > bubbleBox[0] && bubbleBox[3] > bubbleBox[1]
    ) {
      normalized.bubble_bbox = bubbleBox;
    }
    const bubbleCenter = Array.isArray(block.bubble_center)
      ? block.bubble_center.map(Number)
      : null;
    if (
      bubbleCenter?.length === 2 &&
      bubbleCenter.every(value => Number.isFinite(value))
    ) {
      normalized.bubble_center = bubbleCenter;
    }
    return [normalized];
  });
}

if (typeof globalThis !== 'undefined') {
  globalThis.normalizeTranslationBlocks = normalizeTranslationBlocks;
}

function normalizeLocalBackendTimeoutMs(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return DEFAULT_LOCAL_BACKEND_TIMEOUT_MS;
  }

  return Math.max(
    MIN_LOCAL_BACKEND_TIMEOUT_MS,
    Math.min(MAX_LOCAL_BACKEND_TIMEOUT_MS, Math.round(numeric))
  );
}

const MAX_ERROR_BODY_CHARS = 300;
const GEMINI_BLOCKED_FINISH_REASONS = new Set(['SAFETY', 'BLOCKLIST', 'PROHIBITED_CONTENT']);

/**
 * Bound an upstream error body so it cannot flood logs or the UI.
 */
function truncateErrorBody(text, maxChars = MAX_ERROR_BODY_CHARS) {
  const body = typeof text === 'string' ? text : String(text ?? '');
  return body.length > maxChars ? `${body.slice(0, maxChars)}...` : body;
}

/**
 * Truncate an upstream error body and mask anything that looks like a credential.
 * Masking runs before truncation so a cut can never leave half a key behind.
 */
function sanitizeErrorBody(text, secrets = []) {
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

function normalizeBase64ImageInput(base64Image, defaultMimeType = 'image/png') {
  const input = typeof base64Image === 'string' ? base64Image.trim() : '';
  const dataUrlMatch = input.match(/^data:([^;,]+);base64,(.*)$/is);

  return dataUrlMatch
    ? { data: dataUrlMatch[2], mimeType: dataUrlMatch[1] || defaultMimeType }
    : { data: input, mimeType: defaultMimeType };
}

const PROVIDER_BASE_URLS = {
  gemini: 'https://generativelanguage.googleapis.com/v1beta',
  openrouter: 'https://openrouter.ai/api/v1',
  openai: 'https://api.openai.com/v1'
};

// Vision-capable defaults for direct cloud mode.
const PROVIDER_DEFAULT_MODELS = {
  gemini: 'gemini-2.0-flash',
  openai: 'gpt-4o-mini',
  openrouter: 'google/gemini-2.0-flash-exp:free'
};

class TranslationProvider {
  constructor(config) {
    this.provider = config.provider || 'gemini';
    this.apiKey = config.apiKey;
    this.model = config.model || PROVIDER_DEFAULT_MODELS[this.provider] || '';
    this.baseUrl = config.baseUrl || PROVIDER_BASE_URLS[this.provider] || '';
  }

  /**
   * Get auth headers for provider
   */
  getHeaders() {
    switch (this.provider) {
      case 'gemini':
        return {
          'Content-Type': 'application/json',
          'x-goog-api-key': this.apiKey
        };
      case 'openrouter':
        return {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.apiKey}`,
          'HTTP-Referer': 'chrome-extension://manga-translator',
          'X-Title': 'Manga Translator'
        };
      case 'openai':
      case 'custom':
        return {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.apiKey}`
        };
      default:
        return { 'Content-Type': 'application/json' };
    }
  }

  validateApiKey() {
    if (!this.apiKey || typeof this.apiKey !== 'string' || this.apiKey.trim() === '') {
      throw new Error('API key is missing or invalid');
    }
  }

  /**
   * OCR and translate image in one call
   * @param {string} base64Image - Base64 encoded image
   * @param {string} targetLang - Target language
   * @returns {Promise<Object>} - Translation result with text data
   */
  async translateImage(base64Image, targetLang = 'English') {
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

  /**
   * Build translation prompt
   */
  buildTranslationPrompt(targetLang) {
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

  /**
   * Call Gemini API
   */
  async callGemini(base64Image, prompt) {
    this.validateApiKey();
    const url = `${this.baseUrl}/models/${this.model}:generateContent`;
    const imageInput = normalizeBase64ImageInput(base64Image);
    
    const body = {
      contents: [{
        parts: [
          { text: prompt },
          {
            inline_data: {
              mime_type: imageInput.mimeType,
              data: imageInput.data
            }
          }
        ]
      }],
      generationConfig: {
        temperature: 0.1,
        topP: 0.95,
        maxOutputTokens: 4096
      }
    };

    return this.parseGeminiResponse(await this.postJson(url, body, 'Gemini API error'));
  }

  /**
   * Call OpenAI-compatible API (OpenRouter, Custom)
   */
  async callOpenAICompatible(base64Image, prompt) {
    this.validateApiKey();
    const url = `${this.baseUrl}/chat/completions`;
    const imageInput = normalizeBase64ImageInput(base64Image);
    
    const body = {
      model: this.model,
      messages: [{
        role: 'user',
        content: [
          { type: 'text', text: prompt },
          {
            type: 'image_url',
            image_url: {
              url: `data:${imageInput.mimeType};base64,${imageInput.data}`
            }
          }
        ]
      }],
      temperature: 0.1,
      max_tokens: 4096
    };

    return this.parseOpenAIResponse(await this.postJson(url, body, 'API error'));
  }

  async postJson(url, body, errorLabel) {
    const response = await fetch(url, {
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30000)
    });

    if (!response.ok) {
      const error = sanitizeErrorBody(await response.text(), [this.apiKey]);
      throw new Error(`${errorLabel}: ${response.status} - ${error}`);
    }

    return response.json();
  }

  /**
   * Parse Gemini API response
   */
  parseGeminiResponse(data) {
    const candidate = data?.candidates?.[0];
    const finishReason = candidate?.finishReason;
    return this.parseBlocks(
      candidate?.content?.parts?.[0]?.text,
      'Gemini',
      GEMINI_BLOCKED_FINISH_REASONS.has(finishReason)
        ? `Gemini returned no text (${finishReason})`
        : 'Gemini returned no parseable JSON array'
    );
  }

  /**
   * Parse OpenAI-compatible API response
   */
  parseOpenAIResponse(data) {
    return this.parseBlocks(
      data?.choices?.[0]?.message?.content,
      'API',
      'API returned no parseable JSON array'
    );
  }

  parseBlocks(text, label, emptyMessage) {
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
   * Extract JSON from response text
   */
  extractJSON(text) {
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
            const parsed = JSON.parse(candidate);
            const normalized = normalizeTranslationBlocks(parsed);
            // A literal [] is a legitimately empty page. A non-empty array that
            // normalises to nothing (wrong keys, [1], prose brackets) is not.
            if (normalized.length > 0 || (Array.isArray(parsed) && parsed.length === 0)) {
              return normalized;
            }
          } catch (_error) {
            // Not JSON; try the next '[' candidate.
          }
          break;
        }
      }
    }
    
    // null (not []) tells callers nothing parseable was found, so a bad reply
    // is never mistaken for a legitimately empty page and cached.
    return null;
  }
}

function isLoopbackHostname(hostname) {
  return ['localhost', '127.0.0.1', '::1', '[::1]'].includes(hostname);
}

function buildLocalBackendUrl(baseUrl, path) {
  let parsed;
  try {
    parsed = new URL(baseUrl || 'http://localhost:8001');
  } catch (_error) {
    throw new Error('Invalid local backend URL');
  }

  if (parsed.protocol !== 'http:' || !isLoopbackHostname(parsed.hostname)) {
    throw new Error('Local backend URL must be http://localhost or http://127.0.0.1');
  }

  return `${parsed.origin}${parsed.pathname.replace(/\/$/, '')}${path}`;
}

function resolveLocalBackendTranslator(localBackend = {}, pipelineSettings = {}, provider = 'openrouter') {
  return localBackend.translator || pipelineSettings.translator || provider || 'openrouter';
}

/**
 * Local Backend Provider for manga-image-translator
 * Communicates with a local translation server
 */
class LocalBackendProvider {
  constructor(config = {}) {
    const localBackend = config.localBackend || {};
    const pipelineSettings = config.pipelineSettings || {};

    this.baseUrl = config.baseUrl
      || localBackend.url
      || config.localBackendUrl
      || 'http://localhost:8001';
    this.apiKey = config.apiKey || '';
    this.authToken = config.authToken || config.backendAuthToken || localBackend.authToken || '';
    this.googleOcrApiKey = config.googleOcr?.apiKey || config.googleOcrApiKey || '';
    this.googleOcrLanguageHint = config.googleOcr?.languageHint || config.googleOcrLanguageHint || '';
    this.provider = config.provider || pipelineSettings.provider || 'gemini';
    this.model = config.model || pipelineSettings.model || '';
    this.hasBrowserApiKey = Boolean(this.apiKey && this.apiKey.trim());
    this.localLlmBaseUrl = config.localLlm?.baseUrl || config.localLlmBaseUrl || '';
    this.localLlmApiKey = config.localLlm?.apiKey || config.localLlmApiKey || '';
    this.localLlmModel = config.localLlm?.model || config.localLlmModel || '';
    this.targetLang = config.targetLang || pipelineSettings.targetLang || 'English';
    this.requestTimeoutMs = normalizeLocalBackendTimeoutMs(
      localBackend.requestTimeoutMs ?? pipelineSettings.requestTimeoutMs ?? config.requestTimeoutMs
    );
    this.lastRequestId = '';
    
    // Render settings
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
      targetLang: config.targetLang || pipelineSettings.targetLang || 'English'
    };
  }

  /**
   * Translate image using local backend
   * @param {Blob} imageBlob - Image blob to translate
   * @returns {Promise<Blob>} - Translated image blob
   */
  async translateImage(imageBlob) {
    const { targetLang, detector, ocr, translator, inpainter } = this.pipelineSettings;
    const render = this.renderSettings;
    const fields = {
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
      font_size: render.preferredFontSize
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
      signal: AbortSignal.timeout(this.requestTimeoutMs)
    });
    const requestId = response.headers?.get?.('x-request-id') || response.headers?.get?.('X-Request-ID') || '';
    const requestSuffix = requestId ? ` (request id: ${requestId})` : '';
    this.lastRequestId = requestId;

    if (!response.ok) {
      // The backend already redacts its error bodies; only bound the length here.
      const error = truncateErrorBody(await response.text());
      const backendError = new Error(`Local backend error: ${response.status} - ${error}${requestSuffix}`);
      backendError.requestId = requestId;
      backendError.backendStatus = response.status;
      throw backendError;
    }

    const blob = await response.blob();
    const inpainterUsed = response.headers?.get?.('x-inpainter-used')
      || response.headers?.get?.('X-Inpainter-Used')
      || this.pipelineSettings.inpainter;
    const contentType = response.headers?.get?.('content-type') || blob.type || '';
    if (contentType && !contentType.toLowerCase().startsWith('image/')) {
      const text = typeof blob.text === 'function' ? await blob.text() : '';
      const backendError = new Error(`Local backend returned ${contentType || 'non-image response'}: ${text.slice(0, 200)}${requestSuffix}`);
      backendError.requestId = requestId;
      throw backendError;
    }

    blob.requestId = requestId;
    blob.inpainterUsed = inpainterUsed;
    blob.inpainterFallback = inpainterUsed !== this.pipelineSettings.inpainter;
    return blob;
  }

  authOptions() {
    return this.authToken ? { headers: { 'X-Manga-Translator-Token': this.authToken } } : {};
  }

  /**
   * Check if local backend server is reachable
   * @returns {Promise<boolean>} - True if server is reachable
   */
  async checkHealth() {
    this.lastHealthReason = 'unreachable';
    try {
      const response = await fetch(buildLocalBackendUrl(this.baseUrl, '/health'), {
        method: 'GET',
        ...this.authOptions(),
        signal: AbortSignal.timeout(5000)
      });

      if (!response.ok) {
        // The server answered, so it is running; say why it refused.
        this.lastHealthReason = response.status === 401
          ? 'unauthorized'
          : response.status === 503
            ? 'auth_unconfigured'
            : 'error';
        return false;
      }
      if (typeof response.json !== 'function') {
        this.lastHealthReason = 'ok';
        return true;
      }
      const data = await response.json().catch(() => null);
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
      console.warn('Local backend health check failed:', error.message);
      this.lastHealthReason = 'unreachable';
      return false;
    }
  }
}

// Export for module use
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    TranslationProvider,
    LocalBackendProvider,
    buildLocalBackendUrl,
    resolveLocalBackendTranslator,
    normalizeBase64ImageInput,
    normalizeTranslationBlocks,
    sanitizeErrorBody
  };
}
