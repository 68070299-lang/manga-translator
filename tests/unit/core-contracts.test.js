const fs = require('fs');
const path = require('path');
const { LocalBackendProvider } = require('../../extension/lib/api');

const repoRoot = path.resolve(__dirname, '..', '..');

describe('core extension contracts', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });


  test('shadow overlay stylesheet keeps progress spinner contract available', () => {
    const css = fs.readFileSync(
      path.join(repoRoot, 'extension', 'lib', 'overlay-dom.js'),
      'utf8'
    );

    expect(css).toContain('.manga-progress-spinner');
    expect(css).toMatch(/\.manga-progress-spinner\s*\{[^}]*width:\s*16px;/s);
    expect(css).toMatch(/\.manga-progress-spinner\s*\{[^}]*height:\s*16px;/s);
    expect(css).toMatch(/\.manga-progress-spinner\s*\{[^}]*animation:\s*manga-spin\s+1s\s+linear\s+infinite;/s);
    expect(css).toContain('@keyframes manga-spin');
  });


  test('local backend provider posts the expected model-free pipeline form', async () => {
    const fetchMock = jest.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      headers: {
        get: header => (header.toLowerCase() === 'x-request-id' ? 'req-core-contract' : 'image/png')
      },
      blob: async () => new Blob(['translated'], { type: 'image/png' })
    });

    const provider = new LocalBackendProvider({
      localBackend: {
        url: 'http://127.0.0.1:8001',
        detector: 'default',
        ocr: 'manga-ocr',
        translator: 'none',
        inpainter: 'lama_large'
      },
      targetLang: 'Thai'
    });

    const translated = await provider.translateImage(new Blob(['source'], { type: 'image/png' }));
    const [url, request] = fetchMock.mock.calls[0];
    const form = request.body;

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
});
