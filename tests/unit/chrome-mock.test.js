describe('chrome mock fidelity', () => {
  afterEach(() => {
    jest.useRealTimers();
  });

  it('stores and returns cloned storage values', async () => {
    const original = {
      nested: {
        count: 1
      }
    };

    await chrome.storage.local.set({ item: original });
    original.nested.count = 2;

    const firstRead = await chrome.storage.local.get('item');
    expect(firstRead.item.nested.count).toBe(1);

    firstRead.item.nested.count = 3;
    const secondRead = await chrome.storage.local.get('item');
    expect(secondRead.item.nested.count).toBe(1);
  });

  it('dispatches async runtime messages through registered listeners', async () => {
    const listener = jest.fn((message, sender, sendResponse) => {
      expect(sender.tab.id).toBe(7);
      Promise.resolve().then(() => sendResponse({ ok: true, echo: message.value }));
      return true;
    });
    const observer = jest.fn();

    chrome.runtime.onMessage.addListener(listener);
    chrome.runtime.onMessage.addListener(observer);

    await expect(chrome.runtime._dispatchOnMessage(
      { type: 'PING', value: 'pong' },
      { tab: { id: 7 } }
    )).resolves.toEqual({ ok: true, echo: 'pong' });

    expect(chrome.runtime.onMessage.hasListener(listener)).toBe(true);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(observer).toHaveBeenCalledTimes(1);

    chrome.runtime.onMessage.removeListener(listener);
    expect(chrome.runtime.onMessage.hasListener(listener)).toBe(false);
  });
  it('preserves an explicit undefined sendResponse as the first response', async () => {
    chrome.runtime.onMessage.addListener(jest.fn((_message, _sender, sendResponse) => {
      sendResponse(undefined);
      return false;
    }));
    chrome.runtime.onMessage.addListener(jest.fn((_message, _sender, sendResponse) => {
      sendResponse({ late: true });
      return false;
    }));

    await expect(chrome.runtime._dispatchOnMessage({ type: 'PING' }))
      .resolves.toBeUndefined();
  });

  it('reports a missing runtime receiver for promise and callback callers', async () => {
    await expect(chrome.runtime.sendMessage({ type: 'PING' }))
      .rejects.toThrow('Could not establish connection. Receiving end does not exist.');

    let callbackError;
    chrome.runtime.sendMessage({ type: 'PING' }, () => {
      callbackError = chrome.runtime.lastError?.message;
    });

    expect(callbackError).toBe('Could not establish connection. Receiving end does not exist.');
    expect(chrome.runtime.lastError).toBeNull();
  });


  it('does not let a pending listener block a later response', async () => {
    chrome.runtime.onMessage.addListener(jest.fn(() => true));
    chrome.runtime.onMessage.addListener(jest.fn((_message, _sender, sendResponse) => {
      sendResponse({ ok: true });
      return false;
    }));

    await expect(chrome.runtime._dispatchOnMessage({ type: 'PING' }))
      .resolves.toEqual({ ok: true });
  });

  it('uses a configurable timeout for a pending response channel', async () => {
    jest.useFakeTimers();
    chrome.runtime._setPendingResponseTimeout(250);
    chrome.runtime.onMessage.addListener(jest.fn(() => true));

    const response = chrome.runtime._dispatchOnMessage({ type: 'PING' });
    let settled = false;
    response.then(() => {
      settled = true;
    });

    await jest.advanceTimersByTimeAsync(249);
    expect(settled).toBe(false);

    await jest.advanceTimersByTimeAsync(1);
    await expect(response).resolves.toBeUndefined();
  });

  it('rejects invalid pending-response timeout values', () => {
    expect(() => chrome.runtime._setPendingResponseTimeout(-1)).toThrow(TypeError);
    expect(() => chrome.runtime._setPendingResponseTimeout(Number.NaN)).toThrow(TypeError);
  });

  it('uses the configured sender for runtime.sendMessage calls', async () => {
    const listener = jest.fn((_message, sender, sendResponse) => {
      sendResponse(sender);
      return false;
    });

    chrome.runtime.onMessage.addListener(listener);
    chrome.runtime._setMessageSender({ tab: { id: 9 } });

    await expect(chrome.runtime.sendMessage({ type: 'PING' })).resolves.toEqual({
      tab: { id: 9 }
    });
  });

  it('routes tabs.sendMessage through tab-scoped content listeners', async () => {
    const listener = jest.fn((_message, sender, sendResponse) => {
      sendResponse({ tabId: sender.tab.id });
      return false;
    });

    chrome.tabs._addMessageListener(11, listener);

    await expect(chrome.tabs.sendMessage(11, { type: 'PING' })).resolves.toEqual({
      tabId: 11
    });
  });

  it('does not route tabs.sendMessage through extension runtime listeners', async () => {
    chrome.runtime.onMessage.addListener(jest.fn((_message, _sender, sendResponse) => {
      sendResponse({ ok: true });
      return false;
    }));

    await expect(chrome.tabs.sendMessage(11, { type: 'PING' }))
      .rejects.toThrow('Could not establish connection. Receiving end does not exist.');
  });

  it('rejects tabs.sendMessage when no receiving end exists', async () => {
    await expect(chrome.tabs.sendMessage(11, { type: 'PING' }))
      .rejects.toThrow('Could not establish connection. Receiving end does not exist.');
  });
});
