import { build } from 'vite';

/**
 * Bundle one extension module into a classic script that exposes its exports on
 * `window[globalName]`, so a spec can load it into a plain web page without
 * going through the built content script.
 */
export async function bundleAsGlobal(entry: string, globalName: string): Promise<string> {
  const output = await build({
    configFile: false,
    logLevel: 'silent',
    build: {
      write: false,
      minify: false,
      target: 'chrome110',
      lib: { entry, formats: ['iife'], name: globalName, fileName: 'bundle' },
    },
  });
  const results = Array.isArray(output) ? output : [output];
  for (const result of results) {
    if (!('output' in result)) continue;
    const chunk = result.output.find((item) => item.type === 'chunk');
    if (chunk && chunk.type === 'chunk') return chunk.code;
  }
  throw new Error(`Vite produced no chunk for ${entry}`);
}
