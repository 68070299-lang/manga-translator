import { type Mock, vi } from 'vitest';

/** The subset of `Response` the extension reads; tests only provide what they need. */
export interface PartialResponse {
  ok?: boolean;
  status?: number;
  headers?: { get(name: string): string | null } | Headers;
  json?: () => Promise<unknown>;
  text?: () => Promise<string>;
  blob?: () => Promise<Blob>;
  arrayBuffer?: () => Promise<ArrayBuffer>;
}

export type FetchMock = Mock<(input: unknown, init?: RequestInit) => unknown>;

function toResponse(value: unknown): unknown {
  if (value instanceof Response || value === null || typeof value !== 'object') return value;
  const partial = value as PartialResponse;
  return {
    status: partial.ok === false ? 500 : 200,
    headers: new Headers(),
    json: async () => {
      throw new SyntaxError('Unexpected end of JSON input');
    },
    text: async () => '',
    ...partial,
  };
}

/**
 * Replace the global `fetch` with a mock that accepts partial responses and fills
 * in the members real `Response` objects always have (headers, json, text).
 * Returns the underlying mock for stubbing and call assertions. The stub is
 * undone automatically (`unstubGlobals` in vitest.config.ts).
 */
export function mockFetch(): FetchMock {
  const mock: FetchMock = vi.fn();
  vi.stubGlobal('fetch', async (input: unknown, init?: RequestInit) =>
    toResponse(await mock(input, init)),
  );
  return mock;
}

/** Await a promise that is expected to reject and return the error (typed loosely for assertions). */
export async function rejection(
  promise: Promise<unknown>,
): Promise<Error & { requestId?: string; backendStatus?: number }> {
  try {
    await promise;
  } catch (error) {
    return error as Error & { requestId?: string; backendStatus?: number };
  }
  throw new Error('Expected promise to reject');
}
