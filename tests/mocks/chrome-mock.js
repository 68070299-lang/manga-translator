/**
 * Chrome API Mock for Jest testing
 * Simulates chrome.* namespace for unit and integration tests
 */

// Storage mock implementation
const cloneStorageValue = value => {
  if (value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map(cloneStorageValue);
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, cloneStorageValue(item)]));
};

const createStorageMock = () => {
  let data = {};
  
  return {
    get: jest.fn((keys, callback) => {
      const result = {};
      if (typeof keys === 'string') {
        result[keys] = cloneStorageValue(data[keys]);
      } else if (Array.isArray(keys)) {
        keys.forEach(key => {
          result[key] = cloneStorageValue(data[key]);
        });
      } else if (keys === null) {
        Object.assign(result, cloneStorageValue(data));
      } else {
        Object.keys(keys).forEach(key => {
          result[key] = data[key] !== undefined ? cloneStorageValue(data[key]) : cloneStorageValue(keys[key]);
        });
      }
      if (callback) callback(result);
      return Promise.resolve(result);
    }),
    set: jest.fn((items, callback) => {
      Object.assign(data, cloneStorageValue(items));
      if (callback) callback();
      return Promise.resolve();
    }),
    remove: jest.fn((keys, callback) => {
      const keyList = Array.isArray(keys) ? keys : [keys];
      keyList.forEach(key => delete data[key]);
      if (callback) callback();
      return Promise.resolve();
    }),
    clear: jest.fn((callback) => {
      data = {};
      if (callback) callback();
      return Promise.resolve();
    }),
    // Helper for tests
    _getData: () => ({ ...data }),
    _setData: (newData) => { data = { ...newData }; }
  };
};

const messageHandlers = {};

const runtimeMessageListeners = new Set();
let pendingResponseTimeout = 5000;
let messageSender = {};
const MISSING_RECEIVER_MESSAGE = 'Could not establish connection. Receiving end does not exist.';

function settleCallback(callback, value, error) {
  if (typeof callback !== 'function') return;
  if (error) {
    runtimeMock.lastError = { message: error.message };
    callback(undefined);
    runtimeMock.lastError = null;
    return;
  }
  runtimeMock.lastError = null;
  callback(value);
}

function rejectedPromise(error) {
  const promise = Promise.reject(error);
  promise.catch(() => {});
  return promise;
}

const runtimeMock = {
  sendMessage: jest.fn((message, callback) => {
    const handler = messageHandlers[message.type];
    if (handler) {
      const response = handler(message);
      settleCallback(callback, response);
      return Promise.resolve(response);
    }
    if (runtimeMessageListeners.size === 0) {
      const error = new Error(MISSING_RECEIVER_MESSAGE);
      settleCallback(callback, undefined, error);
      return typeof callback === 'function' ? Promise.resolve(undefined) : rejectedPromise(error);
    }
    const response = runtimeMock._dispatchOnMessage(message, messageSender);
    if (typeof callback === 'function') {
      response.then(value => settleCallback(callback, value), error => settleCallback(callback, undefined, error));
    }
    return response;
  }),
  onMessage: {
    addListener: jest.fn(listener => runtimeMessageListeners.add(listener)),
    removeListener: jest.fn(listener => runtimeMessageListeners.delete(listener)),
    hasListener: jest.fn(listener => runtimeMessageListeners.has(listener))
  },
  onInstalled: {
    addListener: jest.fn(),
    removeListener: jest.fn()
  },
  onStartup: {
    addListener: jest.fn(),
    removeListener: jest.fn()
  },
  onSuspend: {
    addListener: jest.fn(),
    removeListener: jest.fn()
  },
  getURL: jest.fn((path) => `chrome-extension://mock-id/${path}`),
  lastError: null,
  id: 'mock-extension-id',
  _registerMessageHandler: (type, handler) => {
    messageHandlers[type] = handler;
  },
  _clearMessageHandlers: () => {
    Object.keys(messageHandlers).forEach(key => delete messageHandlers[key]);
  },
  _dispatchOnMessage: async (message, sender = {}) => {
    let responded = false;
    let responseValue;
    let pending = false;
    let pendingResolve;
    for (const listener of runtimeMessageListeners) {
      const response = listener(message, sender, value => {
        if (!responded) {
          responded = true;
          responseValue = value;
          if (pendingResolve) {
            const resolve = pendingResolve;
            pendingResolve = null;
            resolve();
          }
        }
      });
      if (response && typeof response.then === 'function') await response;
      if (response === true) pending = true;
    }
    if (responded) return responseValue;
    if (pending) {
      await new Promise(resolve => {
        pendingResolve = resolve;
        setTimeout(resolve, pendingResponseTimeout);
      });
    }
    return responded ? responseValue : undefined;
  },
  _clearMessageListeners: () => runtimeMessageListeners.clear(),
  _setPendingResponseTimeout: value => {
    if (!Number.isFinite(value) || value <= 0) throw new TypeError('Pending response timeout must be positive');
    pendingResponseTimeout = value;
  },
  _setMessageSender: sender => {
    messageSender = sender || {};
  }
};

// Tabs mock
const tabMessageListeners = new Map();

const tabsMock = {
  query: jest.fn((queryInfo, callback) => {
    const tabs = [{ id: 1, url: 'https://example.com', active: true }];
    if (callback) callback(tabs);
    return Promise.resolve(tabs);
  }),
  sendMessage: jest.fn((tabId, message, callback) => {
    const listener = tabMessageListeners.get(tabId);
    if (listener) {
      let response = { success: true };
      listener(message, { tab: { id: tabId } }, value => { response = value; });
      settleCallback(callback, response);
      return Promise.resolve(response);
    }
    const error = new Error(MISSING_RECEIVER_MESSAGE);
    settleCallback(callback, undefined, error);
    return typeof callback === 'function' ? Promise.resolve(undefined) : rejectedPromise(error);
  }),
  get: jest.fn((tabId, callback) => {
    const tab = { id: tabId, url: 'https://example.com', active: true };
    if (callback) callback(tab);
    return Promise.resolve(tab);
  }),
  onUpdated: {
    addListener: jest.fn(),
    removeListener: jest.fn()
  },
  onActivated: {
    addListener: jest.fn(),
    removeListener: jest.fn()
  },
  _addMessageListener: (tabId, listener) => {
    tabMessageListeners.set(tabId, listener);
  },
  _clearMessageListeners: () => {
    tabMessageListeners.clear();
  },
  captureVisibleTab: jest.fn((windowId, options, callback) => {
    const dataUrl = 'data:image/png;base64,mockImageData';
    if (callback) callback(dataUrl);
    return Promise.resolve(dataUrl);
  }),
  create: jest.fn((createProperties, callback) => {
    const tab = { id: 2, ...createProperties };
    if (callback) callback(tab);
    return Promise.resolve(tab);
  })
};

// Scripting mock
const scriptingMock = {
  executeScript: jest.fn((injection, callback) => {
    const results = [{ result: null }];
    if (callback) callback(results);
    return Promise.resolve(results);
  }),
  insertCSS: jest.fn((injection, callback) => {
    if (callback) callback();
    return Promise.resolve();
  })
};

// Context menus mock
const contextMenusMock = {
  create: jest.fn((createProperties, callback) => {
    if (callback) callback();
    return 'mock-menu-id';
  }),
  remove: jest.fn((menuItemId, callback) => {
    if (callback) callback();
    return Promise.resolve();
  }),
  removeAll: jest.fn((callback) => {
    if (callback) callback();
    return Promise.resolve();
  }),
  onClicked: {
    addListener: jest.fn(),
    removeListener: jest.fn()
  }
};

// Action mock (Manifest V3)
const actionMock = {
  setIcon: jest.fn(),
  setBadgeText: jest.fn(),
  setBadgeBackgroundColor: jest.fn(),
  setTitle: jest.fn(),
  onClicked: {
    addListener: jest.fn(),
    removeListener: jest.fn()
  }
};

// Alarms mock
const alarmsMock = {
  create: jest.fn(),
  get: jest.fn((name, callback) => {
    if (callback) callback(null);
    return Promise.resolve(null);
  }),
  clear: jest.fn((name, callback) => {
    if (callback) callback(true);
    return Promise.resolve(true);
  }),
  onAlarm: {
    addListener: jest.fn(),
    removeListener: jest.fn()
  }
};

// Complete chrome mock object
const chromeMock = {
  storage: {
    sync: createStorageMock(),
    local: createStorageMock(),
    session: createStorageMock(),
    onChanged: {
      addListener: jest.fn(),
      removeListener: jest.fn()
    }
  },
  runtime: runtimeMock,
  tabs: tabsMock,
  scripting: scriptingMock,
  contextMenus: contextMenusMock,
  action: actionMock,
  alarms: alarmsMock
};

module.exports = chromeMock;
