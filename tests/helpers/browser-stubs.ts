import { vi } from 'vitest';
import { browser } from 'wxt/browser';

/**
 * fake-browser leaves some APIs as "not implemented" stubs that throw. This
 * installs plain mocks for the ones the service worker calls and removes
 * `declarativeNetRequest` (tests that need it install their own with
 * `setDeclarativeNetRequest`). Call it from `beforeEach`.
 */
export function stubWorkerBrowserApis() {
  const contextMenusCreate = vi.fn();
  const contextMenusOnClicked = { addListener: vi.fn() };
  const tabsSendMessage = vi.fn(async (..._args: unknown[]): Promise<unknown> => undefined);
  const captureVisibleTab = vi.fn(async (..._args: unknown[]): Promise<string> => '');
  const action = {
    setIcon: vi.fn(async (..._args: unknown[]) => undefined),
    setTitle: vi.fn(async (..._args: unknown[]) => undefined),
    setBadgeText: vi.fn(async (..._args: unknown[]) => undefined),
    setBadgeBackgroundColor: vi.fn(async (..._args: unknown[]) => undefined),
  };

  Object.assign(browser, {
    contextMenus: { create: contextMenusCreate, onClicked: contextMenusOnClicked },
    action,
  });
  Object.assign(browser.tabs, { sendMessage: tabsSendMessage, captureVisibleTab });
  setDeclarativeNetRequest(undefined);

  return { contextMenusCreate, contextMenusOnClicked, tabsSendMessage, captureVisibleTab, action };
}

/** Replace (or remove, with `undefined`) `browser.declarativeNetRequest`. */
export function setDeclarativeNetRequest(api: Record<string, unknown> | undefined): void {
  Object.assign(browser, { declarativeNetRequest: api });
}

/** Wrap a factory so a `vi.fn()` mock can be called with `new` and return that object. */
export function constructing<T extends object>(factory: () => T) {
  return function (this: object) {
    return Object.assign(this, factory());
  };
}
