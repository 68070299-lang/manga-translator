const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('@playwright/test');

const DEFAULT_URL = 'https://bookwalker.jp/deb7c583b3-7a94-4ebc-ac81-68ffb6a30e62/';
const targetUrl = process.argv.find(arg => /^https?:\/\//i.test(arg)) || DEFAULT_URL;
const keepOpen = process.argv.includes('--keep-open');
const headless = process.argv.includes('--headless');
const singlePage = process.argv.includes('--single-page');
const browserChannel = process.env.PLAYWRIGHT_CHANNEL || 'chromium';
const extensionPath = path.join(__dirname, '..', 'extension');
const outputDir = path.join(__dirname, '..', 'test-results', 'real-url-smoke');
const logFile = path.join(outputDir, 'run.log');

const translatedSvg = `
<svg xmlns="http://www.w3.org/2000/svg" width="640" height="960" viewBox="0 0 640 960">
  <rect width="640" height="960" fill="#fffdf5"/>
  <rect x="36" y="36" width="568" height="888" rx="18" fill="none" stroke="#0b6bcb" stroke-width="14"/>
  <text x="320" y="450" text-anchor="middle" font-family="Arial, sans-serif" font-size="54" font-weight="700" fill="#0b6bcb">E2E</text>
  <text x="320" y="525" text-anchor="middle" font-family="Arial, sans-serif" font-size="48" font-weight="700" fill="#0b6bcb">TRANSLATED</text>
</svg>`.trim();

function startBackend() {
  let translateCount = 0;
  let waiters = [];

  const server = http.createServer((req, res) => {
    if (req.method === 'GET' && req.url.startsWith('/health')) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ models_loaded: true }));
      return;
    }

    if (req.method === 'POST' && req.url.startsWith('/translate')) {
      req.resume();
      req.on('end', () => {
        translateCount += 1;
        waiters.forEach(resolve => resolve(translateCount));
        waiters = [];
        res.writeHead(200, {
          'content-type': 'image/svg+xml',
          'cache-control': 'no-store'
        });
        res.end(translatedSvg);
      });
      return;
    }

    res.writeHead(404, { 'content-type': 'text/plain' });
    res.end('not found');
  });

  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      resolve({
        url: `http://127.0.0.1:${address.port}`,
        getTranslateCount: () => translateCount,
        waitForTranslate: (previousCount, timeoutMs = 60000) => {
          if (translateCount > previousCount) return Promise.resolve(translateCount);
          return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
              waiters = waiters.filter(waiter => waiter !== done);
              reject(new Error(`Timed out waiting for /translate request after ${timeoutMs}ms`));
            }, timeoutMs);
            const done = count => {
              clearTimeout(timer);
              resolve(count);
            };
            waiters.push(done);
          });
        },
        close: () => new Promise(done => {
          let settled = false;
          const finish = () => {
            if (settled) return;
            settled = true;
            done();
          };
          server.close(finish);
          server.closeIdleConnections?.();
          server.closeAllConnections?.();
          setTimeout(finish, 2000);
        })
      });
    });
  });
}

function log(message, data) {
  const line = data === undefined ? message : `${message} ${JSON.stringify(data, null, 2)}`;
  console.log(line);
  fs.appendFileSync(logFile, `${new Date().toISOString()} ${line}\n`);
}

async function getExtensionWorker(context) {
  const existing = context.serviceWorkers()[0];
  if (existing) return existing;
  return context.waitForEvent('serviceworker', { timeout: 15000 });
}

async function setExtensionSettings(context, extensionId, backendUrl) {
  const settingsPage = await context.newPage();
  await settingsPage.goto(`chrome-extension://${extensionId}/popup/popup.html`);
  await settingsPage.evaluate(async ({ backendUrl }) => {
    await chrome.storage.sync.clear();
    await chrome.storage.local.clear();
    await chrome.storage.session.clear();
    await chrome.storage.sync.set({
      settings: {
        provider: 'openrouter',
        apiKey: '',
        model: '',
        targetLang: 'English',
        autoTranslate: false,
        showProgress: true,
        pipelineMode: 'local',
        localBackend: {
          url: backendUrl,
          detector: 'default',
          ocr: 'manga-ocr',
          inpainter: 'lama_large',
          translator: 'none'
        }
      }
    });
  }, { backendUrl });
  await settingsPage.close();
}

async function visibleCount(locator) {
  const count = await locator.count();
  let visible = 0;
  for (let i = 0; i < count; i += 1) {
    if (await locator.nth(i).isVisible().catch(() => false)) visible += 1;
  }
  return visible;
}

async function clickReaderEntry(page) {
  const labels = [
    '\u7121\u6599\u3067\u8aad\u3080',
    '\u8a66\u3057\u8aad\u307f',
    '\u8aad\u3080'
  ];

  for (const label of labels) {
    const candidate = page.locator('a, button').filter({ hasText: label }).first();
    if (await candidate.isVisible({ timeout: 5000 }).catch(() => false)) {
      const text = (await candidate.innerText().catch(() => label)).replace(/\s+/g, ' ').trim();
      log(`Clicking reader entry: ${text}`);
      await candidate.click({ timeout: 10000 });
      return true;
    }
  }

  const directLink = page.locator('a[href*="viewer"], a[href*="trial"]').first();
  if (await directLink.isVisible({ timeout: 5000 }).catch(() => false)) {
    const href = await directLink.getAttribute('href');
    log(`Opening direct reader link: ${href}`);
    await page.goto(new URL(href, page.url()).toString(), { waitUntil: 'domcontentloaded' });
    return true;
  }

  return false;
}

async function findReaderPage(context, fallbackPage) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    for (const page of context.pages()) {
      const url = page.url();
      if (/\/\/viewer(?:-trial)?\.bookwalker\.jp\//i.test(url)) {
        await page.bringToFront();
        return page;
      }
    }
    await fallbackPage.waitForTimeout(500);
  }
  return fallbackPage;
}

async function sendTranslatePage(worker, pageUrl) {
  return worker.evaluate(async targetUrl => {
    const tabs = await chrome.tabs.query({});
    const tab = tabs.find(item => item.url === targetUrl) ||
      tabs.find(item => item.url && item.url.includes('viewer.bookwalker.jp')) ||
      tabs.find(item => item.url && item.url.includes('viewer-trial.bookwalker.jp')) ||
      tabs.find(item => item.url && item.url.includes('bookwalker.jp'));

    if (!tab?.id) {
      throw new Error(`No BookWalker tab found. Tabs: ${tabs.map(item => item.url).join(', ')}`);
    }

    return new Promise(resolve => {
      const timer = setTimeout(() => {
        resolve({ success: false, timeout: true, tabId: tab.id, url: tab.url });
      }, 5000);

      chrome.tabs.sendMessage(tab.id, { type: 'TRANSLATE_PAGE' }, response => {
        clearTimeout(timer);
        resolve({
          success: !chrome.runtime.lastError,
          error: chrome.runtime.lastError?.message || '',
          response,
          tabId: tab.id,
          url: tab.url
        });
      });
    });
  }, pageUrl);
}

async function getCanvasTranslationState(page) {
  return page.evaluate(() => {
    const canvases = Array.from(document.querySelectorAll('canvas'));
    const visibleCanvases = canvases.filter(canvas => {
      const rect = canvas.getBoundingClientRect();
      return rect.width >= 100 && rect.height >= 100;
    });
    const canvas = canvases.find(item => item.hasAttribute('data-manga-translated')) ||
      canvases.find(item => item.hasAttribute('data-manga-processing')) ||
      canvases.find(item => item.hasAttribute('data-manga-source-signature')) ||
      visibleCanvases[0] ||
      canvases[0] ||
      null;
    const overlays = Array.from(document.querySelectorAll('.manga-canvas-overlay'));

    if (!canvas) {
      return {
        canvasCount: canvases.length,
        visibleCanvasCount: visibleCanvases.length,
        overlayCount: overlays.length,
        translated: false,
        processing: false,
        sourceSignature: '',
        counterText: (document.querySelector('#pageSliderCounter')?.textContent || '').trim(),
        viewerClass: document.querySelector('#viewer')?.className || ''
      };
    }

    const rect = canvas.getBoundingClientRect();
    return {
      canvasCount: canvases.length,
      visibleCanvasCount: visibleCanvases.length,
      overlayCount: overlays.length,
      translated: canvas.hasAttribute('data-manga-translated'),
      processing: canvas.hasAttribute('data-manga-processing'),
      sourceSignature: canvas.getAttribute('data-manga-source-signature') || '',
      translatedSrc: canvas.dataset.mangaTranslatedSrc || '',
      counterText: (document.querySelector('#pageSliderCounter')?.textContent || '').trim(),
      viewerClass: document.querySelector('#viewer')?.className || '',
      width: Math.round(rect.width),
      height: Math.round(rect.height)
    };
  });
}

async function dismissCookieBanner(page) {
  const selectors = [
    '#onetrust-accept-btn-handler',
    '#onetrust-reject-all-handler',
    '.onetrust-close-btn-handler',
    'button[aria-label="Close"]'
  ];

  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    if (await locator.isVisible({ timeout: 1000 }).catch(() => false)) {
      log(`Dismissing cookie/banner control: ${selector}`);
      await locator.click({ timeout: 5000, force: true }).catch(error => {
        log(`Cookie/banner dismiss failed for ${selector}: ${error.message}`);
      });
      await page.waitForTimeout(500);
      return;
    }
  }
}

async function waitForCanvasState(page, label, predicate, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let lastState = null;

  while (Date.now() < deadline) {
    lastState = await getCanvasTranslationState(page);
    if (predicate(lastState)) {
      log(`${label}:`, lastState);
      return lastState;
    }
    await page.waitForTimeout(500);
  }

  throw new Error(`${label} timed out. Last canvas state: ${JSON.stringify(lastState)}`);
}

async function waitForTranslatedCanvas(page, label) {
  return waitForCanvasState(page, label, state =>
    state.translated &&
    !state.processing &&
    state.overlayCount > 0 &&
    Boolean(state.sourceSignature),
  30000);
}

async function translateReaderCanvas({ backend, worker, readerPage, readerUrl, label }) {
  log(`Sending TRANSLATE_PAGE for ${label}...`);
  const previousTranslateCount = backend.getTranslateCount();
  const messageResult = await sendTranslatePage(worker, readerUrl);
  log(`${label} TRANSLATE_PAGE message result:`, messageResult);

  if (!messageResult.success) {
    throw new Error(`Could not send TRANSLATE_PAGE to reader tab for ${label}: ${messageResult.error || 'timeout'}`);
  }

  const translateCount = await backend.waitForTranslate(previousTranslateCount, 60000);
  log(`${label} fake backend /translate requests: ${translateCount}`);
  return waitForTranslatedCanvas(readerPage, `${label} translated canvas`);
}

async function logReaderNavigationCandidates(page) {
  const candidates = await page.evaluate(() => {
    return Array.from(document.querySelectorAll('button, a, div, span'))
      .map(element => {
        const rect = element.getBoundingClientRect();
        const label = [
          element.id,
          element.className,
          element.getAttribute('aria-label'),
          element.getAttribute('title'),
          element.textContent
        ].filter(Boolean).join(' ');

        return {
          tag: element.tagName.toLowerCase(),
          id: element.id || '',
          className: typeof element.className === 'string' ? element.className : '',
          text: (element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
          aria: element.getAttribute('aria-label') || '',
          title: element.getAttribute('title') || '',
          visible: rect.width > 0 && rect.height > 0,
          rect: {
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            width: Math.round(rect.width),
            height: Math.round(rect.height)
          },
          navLike: /left|right|prev|next|back|forward|button|bottom|page/i.test(label)
        };
      })
      .filter(item => item.visible && item.navLike)
      .slice(0, 30);
  }).catch(error => [{ error: error.message }]);

  log('Reader navigation candidates:', candidates);
}

function getNavigationFallbackPoint(page, direction, position) {
  const viewport = page.viewportSize() || { width: 1280, height: 720 };
  const y = position === 'middle'
    ? Math.round(viewport.height * 0.5)
    : Math.max(24, viewport.height - 80);

  return direction === 'next'
    ? { x: Math.max(24, Math.round(viewport.width * 0.08)), y }
    : { x: Math.min(viewport.width - 24, Math.round(viewport.width * 0.92)), y };
}

async function clickReaderNavigation(page, direction, position = 'bottom') {
  const leftSelectors = [
    '#button_left',
    '#bottom_left',
    '#btn_left',
    '#left',
    '[id*="button_left" i]',
    '[id*="bottom_left" i]',
    '[id*="left" i][id*="button" i]',
    '[class*="button_left" i]',
    '[class*="bottom_left" i]',
    '[class*="left" i][class*="button" i]'
  ];
  const rightSelectors = [
    '#button_right',
    '#bottom_right',
    '#btn_right',
    '#right',
    '[id*="button_right" i]',
    '[id*="bottom_right" i]',
    '[id*="right" i][id*="button" i]',
    '[class*="button_right" i]',
    '[class*="bottom_right" i]',
    '[class*="right" i][class*="button" i]'
  ];

  const selectors = direction === 'next' ? leftSelectors : rightSelectors;

  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    if (await locator.isVisible({ timeout: 1000 }).catch(() => false)) {
      log(`Clicking ${direction} navigation selector: ${selector}`);
      await locator.click({ timeout: 5000, force: true });
      return;
    }
  }

  const point = getNavigationFallbackPoint(page, direction, position);
  log(`Clicking ${direction} navigation ${position} fallback point:`, point);
  await page.mouse.move(point.x, point.y);
  await page.mouse.click(point.x, point.y);
}

async function navigateReaderAndWait(page, direction, label, predicate, timeoutMs = 20000) {
  const strategies = [
    async () => clickReaderNavigation(page, direction, 'bottom'),
    async () => clickReaderNavigation(page, direction, 'middle'),
    async () => {
      const key = direction === 'next' ? 'ArrowLeft' : 'ArrowRight';
      log(`Pressing ${direction} navigation key: ${key}`);
      await page.keyboard.press(key);
    }
  ];

  let lastError = null;
  for (let i = 0; i < strategies.length; i += 1) {
    await strategies[i]();
    try {
      return await waitForCanvasState(page, `${label} strategy ${i + 1}`, predicate, timeoutMs);
    } catch (error) {
      lastError = error;
      log(`${label} strategy ${i + 1} did not reach expected state: ${error.message}`);
    }
  }

  throw lastError || new Error(`${label} did not reach expected state.`);
}

(async () => {
  fs.mkdirSync(outputDir, { recursive: true });
  fs.writeFileSync(logFile, '');
  const backend = await startBackend();
  let context;

  try {
    context = await chromium.launchPersistentContext('', {
      channel: browserChannel,
      headless,
      args: [
        `--disable-extensions-except=${extensionPath}`,
        `--load-extension=${extensionPath}`,
        '--disable-blink-features=AutomationControlled',
        '--no-first-run'
      ]
    });
    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
      });
    });

    const worker = await getExtensionWorker(context);
    const extensionId = worker.url().split('/')[2];
    await setExtensionSettings(context, extensionId, backend.url);

    const page = await context.newPage();
    page.on('console', msg => {
      if (['error', 'warning'].includes(msg.type())) {
        log(`[page:${msg.type()}] ${msg.text()}`);
      }
    });
    page.on('pageerror', error => log(`[pageerror] ${error.message}`));

    log(`Browser channel: ${browserChannel}`);
    log(`Extension id: ${extensionId}`);
    log(`Fake local backend: ${backend.url}`);
    log(`Opening product URL: ${targetUrl}`);

    await page.goto(targetUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
    await page.screenshot({ path: path.join(outputDir, 'product.png'), fullPage: false, timeout: 10000 });

    log(`Product page title: ${await page.title()}`);
    log(`Product page URL: ${page.url()}`);
    log(`Product page reader buttons visible: ${await visibleCount(page.locator('a, button').filter({ hasText: /\u8aad\u3080|\u8a66\u3057\u8aad\u307f/u }))}`);

    const clicked = await clickReaderEntry(page);
    if (!clicked) {
      throw new Error('Could not find a visible free-read or trial-read entry on the product page.');
    }

    const readerPage = await findReaderPage(context, page);
    readerPage.setDefaultTimeout(15000);
    await readerPage.waitForLoadState('domcontentloaded', { timeout: 30000 }).catch(() => {});
    await readerPage.waitForTimeout(3000);
    await dismissCookieBanner(readerPage);

    const readerUrl = readerPage.url();
    log(`Reader page URL: ${readerUrl}`);
    log('Reader frames:', readerPage.frames().map(frame => frame.url()));

    if (!/\/\/viewer(?:-trial)?\.bookwalker\.jp\//i.test(readerUrl)) {
      throw new Error(`Reader did not open a BookWalker viewer URL. Current URL: ${readerUrl}`);
    }

    const canvasFound = await readerPage.locator('canvas').first().waitFor({ state: 'attached', timeout: 30000 })
      .then(() => true)
      .catch(error => {
        log(`Canvas DOM check failed: ${error.message}`);
        return false;
      });
    log(`Reader canvas attached: ${canvasFound}`);

    for (const frame of readerPage.frames()) {
      const foundInFrame = await frame.locator('canvas').first()
        .waitFor({ state: 'attached', timeout: 5000 })
        .then(() => true)
        .catch(() => false);
      log(`Frame canvas attached: ${foundInFrame} ${frame.url()}`);
    }

    await logReaderNavigationCandidates(readerPage);

    const firstPageState = await translateReaderCanvas({
      backend,
      worker,
      readerPage,
      readerUrl,
      label: 'page 1'
    });
    const firstPageSignature = firstPageState.sourceSignature;
    const firstPageCounter = firstPageState.counterText;

    if (!singlePage) {
      const countAfterFirstPage = backend.getTranslateCount();

      await navigateReaderAndWait(readerPage, 'next', 'after bottom-left next navigation', state =>
        state.counterText &&
        state.counterText !== firstPageCounter &&
        (!state.translated || state.sourceSignature !== firstPageSignature),
      20000);

      const secondPageState = await translateReaderCanvas({
        backend,
        worker,
        readerPage,
        readerUrl,
        label: 'page 2'
      });
      const secondPageSignature = secondPageState.sourceSignature;
      const secondPageCounter = secondPageState.counterText;

      if (!secondPageSignature || secondPageSignature === firstPageSignature) {
        throw new Error('Bottom-left navigation did not produce a different canvas signature.');
      }
      if (!secondPageCounter || secondPageCounter === firstPageCounter) {
        throw new Error(`Bottom-left navigation did not change BookWalker page counter from ${firstPageCounter}.`);
      }
      if (backend.getTranslateCount() <= countAfterFirstPage) {
        throw new Error('Second page did not trigger a new backend translation request.');
      }

      const countAfterSecondPage = backend.getTranslateCount();
      await navigateReaderAndWait(readerPage, 'previous', 'after bottom-right return navigation', state =>
        state.translated &&
        state.overlayCount > 0 &&
        state.sourceSignature === firstPageSignature &&
        state.counterText === firstPageCounter,
      20000);

      const countAfterReturn = backend.getTranslateCount();
      log(`Backend request count after returning to page 1: ${countAfterReturn}`);
      if (countAfterReturn !== countAfterSecondPage) {
        throw new Error('Returning to the first page triggered a new backend call instead of restoring the cached canvas translation.');
      }
    }

    log(`Screenshots saved in: ${outputDir}`);
    log('Real URL smoke test passed.');

    if (keepOpen) {
      log('Keeping browser open. Press Ctrl+C in this terminal to close it.');
      await new Promise(() => {});
    }
  } finally {
    if (context && !keepOpen) {
      await Promise.race([
        context.close(),
        new Promise(resolve => setTimeout(resolve, 10000))
      ]);
    }
    await backend.close();
  }
})().catch(error => {
  console.error(`Real URL smoke test failed: ${error.message}`);
  try {
    fs.appendFileSync(logFile, `${new Date().toISOString()} Real URL smoke test failed: ${error.stack || error.message}\n`);
  } catch (_error) {
    // The log directory may not exist if startup failed very early.
  }
  process.exit(1);
});
