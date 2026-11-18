/**
 * Right-click menu: translate image/page/region, stop, and clear.
 */
import type { Browser } from 'wxt/browser';
import { browser } from 'wxt/browser';
import type { TabMessage } from '../shared/messages';
import { safeBrowserCall } from './tab-messaging';

export const CONTEXT_MENU_ITEMS = [
  {
    id: 'translate-image',
    title: 'Translate this manga image',
    contexts: ['image'],
  },
  {
    id: 'translate-visible-page',
    title: 'Translate visible manga on page',
    contexts: ['page'],
  },
  {
    id: 'translate-page',
    title: 'Translate all manga on page',
    contexts: ['page'],
  },
  {
    id: 'translate-region',
    title: 'Translate a selected region',
    contexts: ['page', 'image'],
  },
  {
    id: 'stop-translation',
    title: 'Stop manga translation',
    contexts: ['page'],
  },
  {
    id: 'clear-page-translations',
    title: 'Clear manga translations on page',
    contexts: ['page'],
  },
] satisfies Browser.contextMenus.CreateProperties[];

/** Create the context menu items (done once, at install). */
export function registerContextMenus(): void {
  for (const item of CONTEXT_MENU_ITEMS) {
    browser.contextMenus.create({ ...item, contexts: [...item.contexts] });
  }
  console.log('[Service Worker] Extension installed, context menus created');
}

/** The content-script message a menu click maps to, or null for unknown ids. */
export function getContextMenuMessage(info: {
  menuItemId: string | number;
  srcUrl?: string;
}): TabMessage | null {
  switch (info.menuItemId) {
    case 'translate-image':
      return { type: 'TRANSLATE_IMAGE_BY_SRC', src: info.srcUrl };
    case 'translate-visible-page':
      return { type: 'TRANSLATE_VISIBLE_IMAGES' };
    case 'translate-page':
      return { type: 'TRANSLATE_PAGE' };
    case 'translate-region':
      return { type: 'START_REGION_SELECTION' };
    case 'stop-translation':
      return { type: 'STOP_TRANSLATION' };
    case 'clear-page-translations':
      return { type: 'CLEAR_PAGE_TRANSLATIONS' };
    default:
      return null;
  }
}

export function handleContextMenuClick(
  info: { menuItemId: string | number; srcUrl?: string },
  tab: { id?: number } | undefined,
): void {
  const message = getContextMenuMessage(info);
  if (!message || !tab?.id) return;
  safeBrowserCall(browser.tabs.sendMessage(tab.id, message));
}
