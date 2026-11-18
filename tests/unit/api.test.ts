import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  buildLocalBackendUrl,
  LocalBackendProvider,
  normalizeBase64ImageInput,
  normalizeTranslationBlocks,
  resolveLocalBackendTranslator,
  sanitizeErrorBody,
  TranslationProvider,
} from '../../extension/lib/api';
import { type FetchMock, mockFetch, type PartialResponse, rejection } from '../helpers/fetch';

let fetchMock: FetchMock;

const formOf = (options?: RequestInit): FormData => options?.body as FormData;

describe('TranslationProvider', () => {
  beforeEach(() => {
    fetchMock = mockFetch();
  });

  it('uses default baseUrl for gemini', () => {
    const provider = new TranslationProvider({ provider: 'gemini' });
    expect(provider.baseUrl).toBe('https://generativelanguage.googleapis.com/v1beta');
    expect(provider.model).toBe('gemini-2.0-flash');
  });

  it('supports direct OpenAI cloud provider defaults', () => {
    const provider = new TranslationProvider({ provider: 'openai', apiKey: 'test-key' });
    expect(provider.baseUrl).toBe('https://api.openai.com/v1');
    expect(provider.model).toBe('gpt-4o-mini');
    expect(provider.getHeaders()).toEqual({
      'Content-Type': 'application/json',
      Authorization: 'Bearer test-key',
    });
  });

  it('parses gemini response in translateImage', async () => {
    const provider = new TranslationProvider({
      provider: 'gemini',
      apiKey: 'test-key',
      model: 'gemini-2.0-flash',
    });

    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        candidates: [
          {
            content: {
              parts: [
                {
                  text: '[{"bbox":[0,0,1,1],"original":"a","translated":"b","type":"bubble"}]',
                },
              ],
            },
          },
        ],
      }),
    });

    const result = await provider.translateImage('base64', 'English');
    expect(Array.isArray(result)).toBe(true);
    expect(result[0]?.translated).toBe('b');
  });

  it('normalizes raw base64 and data URL image inputs', () => {
    expect(normalizeBase64ImageInput('abc123')).toEqual({
      data: 'abc123',
      mimeType: 'image/png',
    });
    expect(normalizeBase64ImageInput('data:image/jpeg;base64,abc123')).toEqual({
      data: 'abc123',
      mimeType: 'image/jpeg',
    });
  });

  it('strips data URL prefixes for Gemini inline data', async () => {
    const provider = new TranslationProvider({
      provider: 'gemini',
      apiKey: 'test-key',
      model: 'gemini-2.0-flash',
    });

    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        candidates: [
          {
            content: {
              parts: [{ text: '[]' }],
            },
          },
        ],
      }),
    });

    await provider.translateImage('data:image/webp;base64,webp-data', 'English');

    const body = JSON.parse(fetchMock.mock.calls[0]?.[1]?.body as string);
    const inlineData = body.contents[0].parts[1].inline_data;
    expect(inlineData).toEqual({
      mime_type: 'image/webp',
      data: 'webp-data',
    });
  });

  it('preserves data URL mime type for OpenAI-compatible image requests', async () => {
    const provider = new TranslationProvider({
      provider: 'openrouter',
      apiKey: 'test-key',
      model: 'google/gemini-2.0-flash',
    });

    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        choices: [
          {
            message: { content: '[]' },
          },
        ],
      }),
    });

    await provider.translateImage('data:image/jpeg;base64,jpeg-data', 'English');

    const body = JSON.parse(fetchMock.mock.calls[0]?.[1]?.body as string);
    const imageUrl = body.messages[0].content[1].image_url.url;
    expect(imageUrl).toBe('data:image/jpeg;base64,jpeg-data');
  });

  it('extracts the first valid JSON array from prose-wrapped responses', () => {
    const provider = new TranslationProvider({ provider: 'gemini', apiKey: 'test-key' });

    const result = provider.extractJSON(`
      Coordinates use [left, top, right, bottom].
      \`\`\`json
      [{"bbox":[0,0,10,10],"translated":"A [bracketed] line"}]
      \`\`\`
      Done [not json].
    `);

    expect(result).toEqual([
      {
        bbox: [0, 0, 10, 10],
        translated: 'A [bracketed] line',
      },
    ]);
  });

  it('returns null when no valid JSON array is present', () => {
    const provider = new TranslationProvider({ provider: 'gemini', apiKey: 'test-key' });

    expect(provider.extractJSON('No structured output [not json]')).toBeNull();
    expect(provider.extractJSON('')).toBeNull();
    expect(provider.extractJSON('{"translated":"object, not an array"}')).toBeNull();
  });

  it('still returns an empty array for a literal empty JSON array', () => {
    const provider = new TranslationProvider({ provider: 'gemini', apiKey: 'test-key' });

    expect(provider.extractJSON('[]')).toEqual([]);
    expect(provider.extractJSON('No text found: []')).toEqual([]);
  });

  it('does not treat a non-empty array that normalises to nothing as an empty page', () => {
    const provider = new TranslationProvider({ provider: 'gemini', apiKey: 'test-key' });
    const valid = '[{"bbox":[0,0,10,10],"translated":"Hello"}]';

    // Wrong key, or bbox given as [x, y, w, h] that fails right > left.
    expect(provider.extractJSON('[{"bbox":[10,10,40,30],"translation":"Hello"}]')).toBeNull();
    expect(provider.extractJSON('[{"bbox":[50,10,40,30],"translated":"Hello"}]')).toBeNull();
    // A prose bracket must not end the scan before the real array.
    expect(provider.extractJSON(`Found [1] bubble: ${valid}`)).toEqual([
      { bbox: [0, 0, 10, 10], translated: 'Hello' },
    ]);
    expect(provider.extractJSON('Found [1] bubble')).toBeNull();
    expect(provider.extractJSON('[1]')).toBeNull();
    expect(provider.extractJSON('[]')).toEqual([]);
  });

  describe('unparseable responses', () => {
    let errorLog: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
      errorLog = vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    afterEach(() => {
      errorLog.mockRestore();
    });

    it('rejects Gemini replies that are empty, prose-only or safety-blocked', async () => {
      const provider = new TranslationProvider({ provider: 'gemini', apiKey: 'test-key' });
      const replies = [
        { candidates: [{ content: { parts: [{ text: 'I cannot help with that [sorry]' }] } }] },
        { candidates: [{ content: { parts: [{ text: '   ' }] } }] },
        { candidates: [{ finishReason: 'SAFETY' }] },
        { candidates: [{ finishReason: 'BLOCKLIST', content: { parts: [] } }] },
        { candidates: [{ finishReason: 'PROHIBITED_CONTENT' }] },
        { candidates: [] },
        {},
      ];

      for (const reply of replies) {
        fetchMock.mockResolvedValueOnce({ ok: true, json: async () => reply });
        await expect(provider.translateImage('base64', 'English')).rejects.toThrow(
          'Failed to parse translation response',
        );
      }
    });

    it('rejects OpenAI-compatible replies that are empty or unparseable', async () => {
      const provider = new TranslationProvider({ provider: 'openrouter', apiKey: 'test-key' });
      const replies = [
        { choices: [{ message: { content: 'Sorry, I cannot read this image.' } }] },
        { choices: [{ message: { content: '' } }] },
        { choices: [{ message: { content: null, refusal: 'refused' } }] },
        { choices: [] },
      ];

      for (const reply of replies) {
        fetchMock.mockResolvedValueOnce({ ok: true, json: async () => reply });
        await expect(provider.translateImage('base64', 'English')).rejects.toThrow(
          'Failed to parse translation response',
        );
      }
    });

    it('still accepts an explicit empty array as a page with no text', async () => {
      const gemini = new TranslationProvider({ provider: 'gemini', apiKey: 'test-key' });
      const openai = new TranslationProvider({ provider: 'openai', apiKey: 'test-key' });

      expect(
        gemini.parseGeminiResponse({ candidates: [{ content: { parts: [{ text: '[]' }] } }] }),
      ).toEqual([]);
      expect(
        openai.parseOpenAIResponse({ choices: [{ message: { content: '```json\n[]\n```' } }] }),
      ).toEqual([]);
    });
  });

  describe('upstream error bodies', () => {
    const secretKey = 'AIzaSyD-1234567890abcdefghijklmnop';

    it('redacts the API key and bounds Gemini error bodies', async () => {
      const provider = new TranslationProvider({ provider: 'gemini', apiKey: secretKey });
      fetchMock.mockResolvedValueOnce({
        ok: false,
        status: 400,
        text: async () => `API key not valid: ${secretKey}. ${'x'.repeat(2000)}`,
      });

      const error = await rejection(provider.translateImage('base64', 'English'));

      expect(error.message).toContain('Gemini API error: 400 - API key not valid: [redacted].');
      expect(error.message).not.toContain(secretKey);
      expect(error.message.length).toBeLessThan(400);
    });

    it('redacts the API key and bearer tokens in OpenAI-compatible error bodies', async () => {
      const provider = new TranslationProvider({
        provider: 'openrouter',
        apiKey: 'my-private-token',
      });
      fetchMock.mockResolvedValueOnce({
        ok: false,
        status: 401,
        text: async () =>
          'Invalid key my-private-token; header Authorization: Bearer abc.def-123 rejected; sk-abcdefgh12345678 unknown',
      });

      const error = await rejection(provider.translateImage('base64', 'English'));

      expect(error.message).toBe(
        'API error: 401 - Invalid key [redacted]; header Authorization: [redacted] rejected; [redacted] unknown',
      );
    });

    it('sanitizeErrorBody masks secrets, ignores empty secrets and truncates', () => {
      expect(sanitizeErrorBody('token=abc123 and abc123', ['abc123', '', null, undefined])).toBe(
        'token=[redacted] and [redacted]',
      );
      expect(sanitizeErrorBody('key AIzaSyD-1234567890abcdefghijklmnop end')).toBe(
        'key [redacted] end',
      );
      expect(sanitizeErrorBody('short body')).toBe('short body');
      expect(sanitizeErrorBody(undefined)).toBe('');

      const long = sanitizeErrorBody('y'.repeat(1000));
      expect(long.length).toBeLessThanOrEqual(303);
      expect(long.endsWith('...')).toBe(true);
    });

    it('does not leave half of a secret behind when truncating', () => {
      const secret = 'super-secret-value';
      const body = `${'z'.repeat(290)}${secret}`;

      expect(sanitizeErrorBody(body, [secret])).not.toContain('super');
    });
  });

  it('normalizes provider aliases and rejects malformed blocks', () => {
    expect(
      normalizeTranslationBlocks([
        {
          bbox: [0, 0, 360, 120],
          bubble_bbox: [10, 5, 350, 115],
          bubble_center: [178.5, 60],
          translated_text: '  keep me  ',
          original: 'ignore',
        },
        { bbox: [0, 0, 1, 1], text: 'original only' },
        { bbox: [0, 0, Number.NaN, 1], translated: 'nan' },
        { bbox: [4, 2, 3, 5], translated: 'reversed' },
        { bbox: [-1, 0, 2, 3], translated: 'negative' },
      ]),
    ).toEqual([
      {
        translated: 'keep me',
        bbox: [0, 0, 360, 120],
        bubble_bbox: [10, 5, 350, 115],
        bubble_center: [178.5, 60],
      },
    ]);
  });
});

describe('LocalBackendProvider', () => {
  beforeEach(() => {
    fetchMock = mockFetch();
  });

  it('returns translated blob', async () => {
    const provider = new LocalBackendProvider({
      localBackendUrl: 'http://localhost:8001',
      pipelineSettings: {
        detector: 'default',
        ocr: 'manga-ocr',
        inpainter: 'lama_large',
        translator: 'gemini',
        targetLang: 'ENG',
      },
    });

    fetchMock.mockResolvedValueOnce({
      ok: true,
      blob: async () => new Blob(['ok'], { type: 'image/png' }),
    });

    const blob = await provider.translateImage(new Blob(['img'], { type: 'image/png' }));
    expect(blob).toBeInstanceOf(Blob);
  });

  it('submits Distilled LanPaint when no inpainter is configured', async () => {
    const provider = new LocalBackendProvider({
      localBackendUrl: 'http://localhost:8001',
    });

    fetchMock.mockImplementationOnce((_url: unknown, options?: RequestInit) => {
      expect(formOf(options).get('inpainter')).toBe('flux2_klein_lanpaint');
      return Promise.resolve({
        ok: true,
        blob: async () => new Blob(['ok'], { type: 'image/png' }),
      });
    });

    await provider.translateImage(new Blob(['img'], { type: 'image/png' }));
  });

  it('sends 16px as the preferred auto-fit font cap', async () => {
    const provider = new LocalBackendProvider({
      localBackendUrl: 'http://localhost:8001',
      fontSize: 16,
      render: { autoScale: true },
    });

    fetchMock.mockImplementationOnce((_url: unknown, options?: RequestInit) => {
      expect(formOf(options).get('font_size')).toBe('16');
      expect(formOf(options).get('auto_scale')).toBe('true');
      return Promise.resolve({
        ok: true,
        blob: async () => new Blob(['ok'], { type: 'image/png' }),
      });
    });

    await provider.translateImage(new Blob(['img'], { type: 'image/png' }));
  });

  it('captures backend request id from successful local responses', async () => {
    const provider = new LocalBackendProvider({ localBackendUrl: 'http://localhost:8001' });

    fetchMock.mockResolvedValueOnce({
      ok: true,
      headers: {
        get: (name: string) =>
          name.toLowerCase() === 'x-request-id' ? 'req-success-1' : 'image/png',
      },
      blob: async () => new Blob(['ok'], { type: 'image/png' }),
    });

    const blob = await provider.translateImage(new Blob(['img'], { type: 'image/png' }));

    expect(provider.lastRequestId).toBe('req-success-1');
    expect(blob.requestId).toBe('req-success-1');
  });

  it('marks a Q4 response as a fallback when Distilled LanPaint was requested', async () => {
    const provider = new LocalBackendProvider({
      localBackendUrl: 'http://localhost:8001',
      pipelineSettings: { inpainter: 'flux2_klein_lanpaint' },
    });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      headers: {
        get: (name: string) => {
          if (name.toLowerCase() === 'x-inpainter-used') return 'flux2_klein_q4';
          if (name.toLowerCase() === 'content-type') return 'image/png';
          return '';
        },
      },
      blob: async () => new Blob(['ok'], { type: 'image/png' }),
    });

    const blob = await provider.translateImage(new Blob(['img'], { type: 'image/png' }));

    expect(blob.inpainterUsed).toBe('flux2_klein_q4');
    expect(blob.inpainterFallback).toBe(true);
  });

  it('includes backend request id in local backend errors', async () => {
    const provider = new LocalBackendProvider({ localBackendUrl: 'http://localhost:8001' });

    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      headers: {
        get: (name: string) => (name.toLowerCase() === 'x-request-id' ? 'req-error-1' : ''),
      },
      text: async () => 'pipeline failed',
    });

    await expect(provider.translateImage(new Blob(['img'], { type: 'image/png' }))).rejects.toThrow(
      'request id: req-error-1',
    );
    expect(provider.lastRequestId).toBe('req-error-1');
  });

  it('omits api_key and model so the backend can use .env defaults', async () => {
    const provider = new LocalBackendProvider({
      localBackendUrl: 'http://localhost:8001',
      provider: 'openrouter',
      model: 'ui/default-model',
      pipelineSettings: {
        translator: 'openrouter',
        targetLang: 'English',
      },
    });

    fetchMock.mockImplementationOnce((_url: unknown, options?: RequestInit) => {
      const formData = formOf(options);
      expect(formData.get('translator')).toBe('openrouter');
      expect(formData.has('api_key')).toBe(false);
      expect(formData.has('model')).toBe(false);
      return Promise.resolve({
        ok: true,
        blob: async () => new Blob(['ok'], { type: 'image/png' }),
      });
    });

    await provider.translateImage(new Blob(['img'], { type: 'image/png' }));
  });

  it('sends api_key and model when configured in the extension', async () => {
    const provider = new LocalBackendProvider({
      localBackendUrl: 'http://localhost:8001',
      provider: 'openrouter',
      apiKey: 'token',
      model: 'openrouter/model',
      pipelineSettings: {
        translator: 'openrouter',
        targetLang: 'English',
      },
    });

    fetchMock.mockImplementationOnce((_url: unknown, options?: RequestInit) => {
      const formData = formOf(options);
      expect(formData.get('api_key')).toBe('token');
      expect(formData.get('model')).toBe('openrouter/model');
      return Promise.resolve({
        ok: true,
        blob: async () => new Blob(['ok'], { type: 'image/png' }),
      });
    });

    await provider.translateImage(new Blob(['img'], { type: 'image/png' }));
  });

  it('sends local LLM fields without the generic translation API key', async () => {
    const provider = new LocalBackendProvider({
      localBackendUrl: 'http://localhost:8001',
      apiKey: 'browser-key',
      localBackend: {
        translator: 'local',
      },
      localLlm: {
        baseUrl: 'http://localhost:8000/v1',
        apiKey: 'local-key',
        model: 'qwen2.5',
      },
    });

    fetchMock.mockImplementationOnce((_url: unknown, options?: RequestInit) => {
      const formData = formOf(options);
      expect(formData.get('translator')).toBe('local');
      expect(formData.get('local_llm_base_url')).toBe('http://localhost:8000/v1');
      expect(formData.get('local_llm_api_key')).toBe('local-key');
      expect(formData.get('model')).toBe('qwen2.5');
      expect(formData.has('api_key')).toBe(false);
      return Promise.resolve({
        ok: true,
        blob: async () => new Blob(['ok'], { type: 'image/png' }),
      });
    });

    await provider.translateImage(new Blob(['img'], { type: 'image/png' }));
  });

  it('sends Google OCR key only when google-ocr is selected', async () => {
    const provider = new LocalBackendProvider({
      localBackendUrl: 'http://localhost:8001',
      googleOcr: {
        apiKey: 'vision-key',
        languageHint: 'ja',
      },
      localBackend: {
        ocr: 'google-ocr',
        translator: 'none',
      },
    });

    fetchMock.mockImplementationOnce((_url: unknown, options?: RequestInit) => {
      const formData = formOf(options);
      expect(formData.get('ocr')).toBe('google-ocr');
      expect(formData.get('google_ocr_api_key')).toBe('vision-key');
      expect(formData.get('google_ocr_language_hint')).toBe('ja');
      return Promise.resolve({
        ok: true,
        blob: async () => new Blob(['ok'], { type: 'image/png' }),
      });
    });

    await provider.translateImage(new Blob(['img'], { type: 'image/png' }));
  });

  it('honors explicit OCR-only translator selection instead of falling back to provider', async () => {
    const provider = new LocalBackendProvider({
      localBackendUrl: 'http://localhost:8001',
      provider: 'openrouter',
      apiKey: 'token',
      model: 'openrouter/model',
      localBackend: {
        translator: 'none',
      },
    });

    fetchMock.mockImplementationOnce((_url: unknown, options?: RequestInit) => {
      const formData = formOf(options);
      expect(formData.get('translator')).toBe('none');
      expect(formData.has('api_key')).toBe(false);
      expect(formData.has('model')).toBe(false);
      return Promise.resolve({
        ok: true,
        blob: async () => new Blob(['ok'], { type: 'image/png' }),
      });
    });

    await provider.translateImage(new Blob(['img'], { type: 'image/png' }));
  });

  it('uses an extended default timeout for slow local rendering', async () => {
    const timeoutSpy = vi
      .spyOn(AbortSignal, 'timeout')
      .mockReturnValue(undefined as unknown as AbortSignal);
    const provider = new LocalBackendProvider({ localBackendUrl: 'http://localhost:8001' });

    fetchMock.mockResolvedValueOnce({
      ok: true,
      blob: async () => new Blob(['ok'], { type: 'image/png' }),
    });

    await provider.translateImage(new Blob(['img'], { type: 'image/png' }));

    expect(timeoutSpy).toHaveBeenCalledWith(600000);
    timeoutSpy.mockRestore();
  });

  it('allows local backend timeout override from settings', async () => {
    const timeoutSpy = vi
      .spyOn(AbortSignal, 'timeout')
      .mockReturnValue(undefined as unknown as AbortSignal);
    const provider = new LocalBackendProvider({
      localBackend: {
        url: 'http://localhost:8001',
        requestTimeoutMs: '240000',
      },
    });

    fetchMock.mockResolvedValueOnce({
      ok: true,
      blob: async () => new Blob(['ok'], { type: 'image/png' }),
    });

    await provider.translateImage(new Blob(['img'], { type: 'image/png' }));

    expect(timeoutSpy).toHaveBeenCalledWith(240000);
    timeoutSpy.mockRestore();
  });

  it('checkHealth returns true when server is reachable', async () => {
    const provider = new LocalBackendProvider({ localBackendUrl: 'http://localhost:8001' });
    fetchMock.mockResolvedValueOnce({ ok: true });

    const result = await provider.checkHealth();
    expect(result).toBe(true);
  });

  it('checkHealth returns false when required models are not loaded', async () => {
    const provider = new LocalBackendProvider({ localBackendUrl: 'http://localhost:8001' });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'degraded', models_loaded: false }),
    });

    const result = await provider.checkHealth();
    expect(result).toBe(false);
  });

  it('bounds local backend error bodies while keeping the request id', async () => {
    const provider = new LocalBackendProvider({ localBackendUrl: 'http://localhost:8001' });

    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      headers: {
        get: (name: string) => (name.toLowerCase() === 'x-request-id' ? 'req-long-1' : ''),
      },
      text: async () => 'e'.repeat(1500),
    });

    const error = await rejection(
      provider.translateImage(new Blob(['img'], { type: 'image/png' })),
    );

    expect(error.message).toBe(
      `Local backend error: 500 - ${'e'.repeat(300)}... (request id: req-long-1)`,
    );
    expect(error.requestId).toBe('req-long-1');
  });

  it('checkHealth treats a loading backend as not ready without throwing', async () => {
    const provider = new LocalBackendProvider({ localBackendUrl: 'http://localhost:8001' });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'loading' }),
    });
    await expect(provider.checkHealth()).resolves.toBe(false);

    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'loading', models_loaded: false }),
    });
    await expect(provider.checkHealth()).resolves.toBe(false);

    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'ok', models_loaded: true }),
    });
    await expect(provider.checkHealth()).resolves.toBe(true);
  });

  it('checkHealth records why the backend is not ready', async () => {
    const provider = new LocalBackendProvider({ localBackendUrl: 'http://localhost:8001' });
    const cases: Array<[PartialResponse, boolean, string]> = [
      [
        { ok: true, json: async () => ({ status: 'loading', models_loaded: false }) },
        false,
        'loading',
      ],
      [
        { ok: true, json: async () => ({ status: 'degraded', models_loaded: false }) },
        false,
        'degraded',
      ],
      [{ ok: false, status: 401 }, false, 'unauthorized'],
      [{ ok: false, status: 503 }, false, 'auth_unconfigured'],
      [{ ok: false, status: 500 }, false, 'error'],
      [{ ok: true, json: async () => ({ status: 'ok', models_loaded: true }) }, true, 'ok'],
    ];

    for (const [response, expected, reason] of cases) {
      fetchMock.mockResolvedValueOnce(response);
      await expect(provider.checkHealth()).resolves.toBe(expected);
      expect(provider.lastHealthReason).toBe(reason);
    }

    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await expect(provider.checkHealth()).resolves.toBe(false);
    expect(provider.lastHealthReason).toBe('unreachable');
    warn.mockRestore();
  });

  it('tags backend HTTP errors with the response status', async () => {
    const provider = new LocalBackendProvider({ localBackendUrl: 'http://localhost:8001' });
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 504,
      headers: { get: () => '' },
      text: async () => '{"detail":"Translation provider request timed out"}',
    });

    const error = await rejection(
      provider.translateImage(new Blob(['img'], { type: 'image/png' })),
    );

    expect(error.backendStatus).toBe(504);
    expect(error.message).toContain('Translation provider request timed out');
  });

  it('trims whitespace from model ids before sending them', async () => {
    const cloudModel = new LocalBackendProvider({
      localBackendUrl: 'http://localhost:8001',
      provider: 'openrouter',
      apiKey: 'token',
      model: ' openrouter/model\n',
      pipelineSettings: { translator: 'openrouter' },
    });
    const localModel = new LocalBackendProvider({
      localBackendUrl: 'http://localhost:8001',
      localBackend: { translator: 'local' },
      localLlm: { baseUrl: 'http://localhost:8000/v1', model: '  qwen2.5 ' },
    });
    const sent: Array<FormDataEntryValue | null> = [];
    fetchMock.mockImplementation((_url: unknown, options?: RequestInit) => {
      sent.push(formOf(options).get('model'));
      return Promise.resolve({
        ok: true,
        blob: async () => new Blob(['ok'], { type: 'image/png' }),
      });
    });

    await cloudModel.translateImage(new Blob(['img'], { type: 'image/png' }));
    await localModel.translateImage(new Blob(['img'], { type: 'image/png' }));

    expect(sent).toEqual(['openrouter/model', 'qwen2.5']);
  });

  it('rejects non-image successful backend responses', async () => {
    const provider = new LocalBackendProvider({ localBackendUrl: 'http://localhost:8001' });
    fetchMock.mockResolvedValueOnce({
      ok: true,
      headers: { get: () => 'application/json' },
      blob: async () => new Blob(['{"translation_failed":true}'], { type: 'application/json' }),
    });

    await expect(provider.translateImage(new Blob(['img'], { type: 'image/png' }))).rejects.toThrow(
      'application/json',
    );
  });

  it('allows only loopback backend URLs', () => {
    expect(buildLocalBackendUrl('http://localhost:8001', '/health')).toBe(
      'http://localhost:8001/health',
    );
    expect(buildLocalBackendUrl('http://127.0.0.1:8001/api', '/health')).toBe(
      'http://127.0.0.1:8001/api/health',
    );
    expect(() => buildLocalBackendUrl('https://example.com', '/health')).toThrow(
      'Local backend URL',
    );
    expect(() => buildLocalBackendUrl('http://192.168.1.10:8001', '/health')).toThrow(
      'Local backend URL',
    );
  });

  it('resolves local backend translator precedence explicitly', () => {
    expect(resolveLocalBackendTranslator({ translator: 'none' }, {}, 'openrouter')).toBe('none');
    expect(resolveLocalBackendTranslator({ translator: 'local' }, {}, 'openrouter')).toBe('local');
    expect(resolveLocalBackendTranslator({}, { translator: 'gemini' }, 'openrouter')).toBe(
      'gemini',
    );
    expect(resolveLocalBackendTranslator({}, {}, 'openrouter')).toBe('openrouter');
  });
});

describe('LocalBackendProvider model-free pipeline form', () => {
  it('posts the expected form for a keyless translator=none run', async () => {
    fetchMock = mockFetch();
    fetchMock.mockResolvedValue({
      ok: true,
      headers: {
        get: (header: string) =>
          header.toLowerCase() === 'x-request-id' ? 'req-core-contract' : 'image/png',
      },
      blob: async () => new Blob(['translated'], { type: 'image/png' }),
    });

    const provider = new LocalBackendProvider({
      localBackend: {
        url: 'http://127.0.0.1:8001',
        detector: 'default',
        ocr: 'manga-ocr',
        translator: 'none',
        inpainter: 'lama_large',
      },
      targetLang: 'Thai',
    });

    const translated = await provider.translateImage(new Blob(['source'], { type: 'image/png' }));
    const [url, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    const form = request.body as FormData;

    expect(url).toBe('http://127.0.0.1:8001/translate');
    expect(request.method).toBe('POST');
    expect(form.get('target_lang')).toBe('Thai');
    expect(form.get('detector')).toBe('default');
    expect(form.get('ocr')).toBe('manga-ocr');
    expect(form.get('translator')).toBe('none');
    expect(form.get('inpainter')).toBe('lama_large');
    expect(form.get('render')).toBe('true');
    expect(form.has('api_key')).toBe(false);
    expect(form.has('model')).toBe(false);
    expect(translated.requestId).toBe('req-core-contract');
  });

  it('sends the backend access token as X-Manga-Translator-Token', async () => {
    fetchMock = mockFetch();
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok', models_loaded: true }),
    });

    const provider = new LocalBackendProvider({ localBackend: { authToken: 'secret-token' } });
    await provider.checkHealth();

    const [url, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://localhost:8001/health');
    expect(request.headers).toEqual({ 'X-Manga-Translator-Token': 'secret-token' });
  });
});
