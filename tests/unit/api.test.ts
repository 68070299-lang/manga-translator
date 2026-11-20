import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  buildLocalBackendUrl,
  LocalBackendProvider,
  resolveLocalBackendTranslator,
} from '../../extension/lib/api';
import { type FetchMock, mockFetch, type PartialResponse, rejection } from '../helpers/fetch';

let fetchMock: FetchMock;

const formOf = (options?: RequestInit): FormData => options?.body as FormData;

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
