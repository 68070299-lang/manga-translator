/**
 * Jest test setup file
 * Runs before each test file
 */

// Import Chrome API mocks
const chromeMock = require('./mocks/chrome-mock');

// Set up global Chrome mock
global.chrome = chromeMock;

// Mock fetch for API tests
global.fetch = jest.fn();

// Polyfill AbortSignal.timeout for jsdom environment
if (!global.AbortSignal || typeof global.AbortSignal.timeout !== 'function') {
  global.AbortSignal = global.AbortSignal || class AbortSignal {};
  global.AbortSignal.timeout = (ms) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ms);
    if (typeof timer.unref === 'function') timer.unref();
    return controller.signal;
  };
}

// Mock console methods to reduce noise (optional)
// global.console.log = jest.fn();
// global.console.warn = jest.fn();

// Reset all mocks before each test
beforeEach(() => {
  jest.clearAllMocks();
  chromeMock.storage.sync.clear();
  chromeMock.storage.local.clear();
  chromeMock.storage.session.clear();
  chromeMock.tabs._clearMessageListeners?.();
  chromeMock.runtime._clearMessageListeners?.();
});

// Clean up after all tests
afterAll(() => {
  jest.restoreAllMocks();
});

// Custom matchers (optional)
expect.extend({
  toBeValidSettings(received) {
    const requiredKeys = ['provider', 'apiKey', 'model', 'targetLang'];
    const hasAllKeys = requiredKeys.every(key => key in received);
    
    if (hasAllKeys) {
      return {
        message: () => `expected ${JSON.stringify(received)} not to be valid settings`,
        pass: true
      };
    } else {
      const missingKeys = requiredKeys.filter(key => !(key in received));
      return {
        message: () => `expected settings to have keys: ${missingKeys.join(', ')}`,
        pass: false
      };
    }
  }
});
