import http from 'node:http';
import type { AddressInfo, Socket } from 'node:net';
import path from 'node:path';
import { bundleAsGlobal } from './bundle.ts';
import { expect, test } from './fixtures.ts';

type OverlayDomApi = typeof import('../../extension/lib/overlay-dom.ts');

const overlayDomEntry = path.join(
  import.meta.dirname,
  '..',
  '..',
  'extension',
  'lib',
  'overlay-dom.ts',
);

async function createTestServer() {
  const sockets = new Set<Socket>();
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(`<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Manga Translator Test Page</title>
</head>
<body>
  <main>
    <h1>Test Page</h1>
    <p>This page is served locally so the extension content script can inject styles.</p>
  </main>
</body>
</html>`);
  });

  server.on('connection', (socket) => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });

  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });

  const address = server.address() as AddressInfo;
  const url = `http://localhost:${address.port}/`;

  return {
    close: async () => {
      for (const socket of sockets) {
        socket.destroy();
      }

      await new Promise((resolve) => server.close(resolve));
    },
    url,
  };
}

test('shadow overlay styles apply and hostile page CSS cannot reach them', async ({ page }) => {
  const { close, url } = await createTestServer();

  try {
    await page.goto(url);
    await page.addStyleTag({
      content: `
        * { outline: 9px solid red; }
        div { width: 1px !important; height: 1px !important; animation: none !important; }
        button { display: none !important; width: 1px !important; }
        manga-overlay { opacity: 0.5; }
      `,
    });
    await page.addScriptTag({
      content: await bundleAsGlobal(overlayDomEntry, 'MangaTranslatorOverlayDom'),
    });
    await page.evaluate(() => {
      const { createShadowHost, mountInShadow } = (
        window as unknown as { MangaTranslatorOverlayDom: OverlayDomApi }
      ).MangaTranslatorOverlayDom;
      const spinner = document.createElement('div');
      spinner.id = 'manga-style-probe';
      spinner.className = 'manga-progress-spinner';
      const button = document.createElement('button');
      button.id = 'manga-button-probe';
      button.className = 'manga-reader-control-btn';
      const host = createShadowHost({
        id: 'manga-host-probe',
        styleText: 'position: fixed; top: 0; left: 0;',
      });
      mountInShadow(host, spinner, button);
      document.body.appendChild(host);
    });

    const spinner = page.locator('#manga-style-probe');
    await expect(spinner).toHaveCSS('width', '16px');
    await expect(spinner).toHaveCSS('height', '16px');
    await expect(spinner).toHaveCSS('animation-name', 'manga-spin');

    const button = page.locator('#manga-button-probe');
    await expect(button).toBeVisible();
    await expect(button).toHaveCSS('width', '36px');
    await expect(page.locator('#manga-host-probe')).toHaveCSS('opacity', '1');
    await expect(page.locator('#manga-host-probe')).toHaveCSS('outline-style', 'none');
  } finally {
    await close();
  }
});
