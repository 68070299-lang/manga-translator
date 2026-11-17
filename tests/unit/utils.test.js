const { hashObject, base64ToBlob, blobToBase64 } = require('../../extension/lib/utils');

describe('utils', () => {
  it('hashObject returns stable hash', () => {
    const a = hashObject({ foo: 'bar' });
    const b = hashObject({ foo: 'bar' });
    expect(a).toBe(b);
  });

  it('base64ToBlob creates a blob', () => {
    const blob = base64ToBlob('aGVsbG8=', 'text/plain');
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe('text/plain');
  });

  it('blobToBase64 returns expected string', async () => {
    const blob = new Blob(['hello'], { type: 'text/plain' });
    const base64 = await blobToBase64(blob);
    expect(base64).toBe('aGVsbG8=');
  });
});
