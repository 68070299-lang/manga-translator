import { describe, expect, it } from 'vitest';
import { base64ToBlob, blobToBase64, hashObject } from '../../extension/lib/utils';

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

describe('utils edge cases', () => {
  it('hashObject keeps the legacy cache-key algorithm', () => {
    // Pinned values: changing the hash orphans every existing cache entry.
    expect(hashObject({ foo: 'bar' })).toBe(hashObject({ foo: 'bar' }));
    expect(hashObject('')).toBe('440');
    expect(hashObject('a')).toBe('8b83');
    expect(hashObject({ foo: 'bar' })).toBe('2ed86cc3');
    expect(hashObject({ a: 1, b: [1, 2, 'x'], c: { d: true } })).toBe('2ff96fa');
  });

  it('base64ToBlob strips data URL prefixes and rejects bad input', async () => {
    const blob = base64ToBlob('data:text/plain;base64,aGVsbG8=', 'text/plain');
    expect(await blob.text()).toBe('hello');
    expect(() => base64ToBlob('')).toThrow('empty or not a string');
    expect(() => base64ToBlob('data:image/png;base64,')).toThrow('empty after prefix removal');
    expect(() => base64ToBlob('not*base64')).toThrow('invalid characters');
  });
});
