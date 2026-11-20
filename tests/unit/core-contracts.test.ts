import { describe, expect, it } from 'vitest';
import { CONTEXT_MENU_ITEMS } from '../../extension/background/context-menus';
import { CDN_REFERRER_RULES } from '../../extension/lib/referrer-rules';
import { DEFAULT_SETTINGS, SETTINGS_VERSION } from '../../extension/lib/settings';
import {
  isMessageOfType,
  isWorkerRequest,
  SITE_GATED_COMMAND_TYPES,
  WORKER_REQUEST_TYPES,
} from '../../extension/shared/messages';

// Contract checks against docs/RUNTIME_CONTRACT.md for the shared libraries and
// the worker. (The LocalBackendProvider form contract lives in api.test.ts.)

describe('worker message contract', () => {
  it('handles exactly the documented request messages', () => {
    expect([...WORKER_REQUEST_TYPES].sort()).toEqual(
      [
        'CAPTURE_REGION',
        'CHECK_BACKEND',
        'CLEAR_CACHE',
        'FETCH_IMAGE',
        'GET_BUNDLED_FONT',
        'GET_CACHE_STATS',
        'TRANSLATE_IMAGE',
      ].sort(),
    );
  });

  it('never treats broadcast messages as worker requests', () => {
    for (const type of [
      'TRANSLATE_PAGE',
      'TRANSLATE_VISIBLE_IMAGES',
      'TRANSLATE_IMAGE_BY_SRC',
      'START_REGION_SELECTION',
      'CLEAR_PAGE_TRANSLATIONS',
      'STOP_TRANSLATION',
      'SITE_ACTIVATION_CHANGED',
      'SETTINGS_UPDATED',
      'TRANSLATION_PROGRESS',
      'TRANSLATION_ERROR',
      'TRANSLATION_COMPLETE',
      'TRANSLATION_STOPPED',
      'TRANSLATION_RESULT',
    ]) {
      expect(isWorkerRequest({ type })).toBe(false);
    }
    expect(isWorkerRequest(null)).toBe(false);
    expect(isWorkerRequest({ type: 7 })).toBe(false);
    expect(isWorkerRequest({ type: 'CLEAR_CACHE' })).toBe(true);
  });

  it('narrows messages by type', () => {
    expect(isMessageOfType({ type: 'TRANSLATE_PAGE' }, 'TRANSLATE_PAGE')).toBe(true);
    expect(isMessageOfType({ type: 'TRANSLATE_PAGE' }, 'STOP_TRANSLATION')).toBe(false);
    expect(isMessageOfType(undefined, 'STOP_TRANSLATION')).toBe(false);
  });

  it('gates only the commands that start translation work', () => {
    expect([...SITE_GATED_COMMAND_TYPES].sort()).toEqual(
      [
        'START_REGION_SELECTION',
        'TRANSLATE_IMAGE_BY_SRC',
        'TRANSLATE_PAGE',
        'TRANSLATE_VISIBLE_IMAGES',
      ].sort(),
    );
  });

  it('keeps one context menu entry per content-script command', () => {
    expect(CONTEXT_MENU_ITEMS.map((item) => item.id)).toEqual([
      'translate-image',
      'translate-visible-page',
      'translate-page',
      'translate-region',
      'stop-translation',
      'clear-page-translations',
    ]);
  });
});

describe('settings contract defaults', () => {
  it('matches the documented defaults', () => {
    expect(DEFAULT_SETTINGS.settingsVersion).toBe(SETTINGS_VERSION);
    expect(SETTINGS_VERSION).toBe(2);
    expect(DEFAULT_SETTINGS).toMatchObject({
      pipelineMode: 'local',
      provider: 'openrouter',
      model: '',
      baseUrl: '',
      targetLang: 'English',
      fontFamily: "'CC Wild Words', sans-serif",
      fontSize: 16,
      autoTranslate: false,
      localBackend: {
        url: 'http://localhost:8001',
        detector: 'default',
        ocr: 'manga-ocr',
        inpainter: 'flux2_klein_lanpaint',
        translator: '',
        requestTimeoutMs: 600000,
      },
      localLlm: { baseUrl: '', model: '' },
      render: {
        textAlignment: 'center',
        textDirection: 'auto',
        lineSpacing: 1.2,
        textColor: '#000000',
        borderColor: '#ffffff',
        borderSize: 2,
        autoScale: true,
      },
    });
  });

  it('uses the documented referrer CDN allowlist', () => {
    expect(CDN_REFERRER_RULES.map((rule) => rule.hostname)).toEqual([
      'pximg.net',
      'i.hamreus.com',
      'hitomi.la',
      'klimv1.xyz',
    ]);
  });
});
