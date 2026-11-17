const manifest = require('../../extension/manifest.json');
const packageJson = require('../../package.json');
const bookwalkerCorsRules = require('../../extension/rules/bookwalker-cors.json');

describe('extension manifest', () => {
  it('allows persistent storage for a five-page rendered translation cache', () => {
    expect(manifest.permissions).toContain('storage');
    expect(manifest.permissions).toContain('unlimitedStorage');
  });

  it('pins the permission budget so widening it is a deliberate change', () => {
    const budget = {
      permissions: ['contextMenus', 'declarativeNetRequest', 'storage', 'unlimitedStorage'],
      host_permissions: ['<all_urls>'],
      externally_connectable: undefined,
      web_accessible_resources: undefined
    };
    const actual = {
      permissions: [...manifest.permissions].sort(),
      host_permissions: manifest.host_permissions,
      externally_connectable: manifest.externally_connectable,
      web_accessible_resources: manifest.web_accessible_resources
    };

    try {
      expect(actual).toEqual(budget);
    } catch (error) {
      error.message = 'Manifest permission budget changed. If this is intentional, update the budget in tests/unit/manifest.test.js deliberately and review why the new access is needed. ' + error.message;
      throw error;
    }
  });

  it('declares a minimum Chrome version for the APIs the extension relies on', () => {
    expect(Number(manifest.minimum_chrome_version)).toBeGreaterThanOrEqual(110);
  });

  it('keeps the manifest version in sync with package.json', () => {
    expect(manifest.version).toBe(packageJson.version);
  });

  it('loads translated-image helper before the content script', () => {
    const scripts = manifest.content_scripts[0].js;

    expect(scripts).toEqual(expect.arrayContaining([
      'lib/settings.js',
      'lib/utils.js',
      'lib/translated-image.js',
      'content/content-helpers.js',
      'content/content.js'
    ]));
    expect(scripts.indexOf('lib/utils.js')).toBeLessThan(scripts.indexOf('lib/translated-image.js'));
    expect(scripts.indexOf('lib/translated-image.js')).toBeLessThan(scripts.indexOf('content/content.js'));
    expect(scripts.indexOf('content/content-helpers.js')).toBeLessThan(scripts.indexOf('content/content.js'));
    ['lib/overlay-dom.js', 'lib/font-registry.js', 'lib/region-capture.js'].forEach(script => {
      expect(scripts).toContain(script);
      expect(scripts.indexOf(script)).toBeLessThan(scripts.indexOf('content/content.js'));
    });
  });

  it('registers a narrowly scoped BookWalker CORS ruleset', () => {
    expect(manifest.declarative_net_request.rule_resources).toContainEqual({
      id: 'bookwalker_cors',
      enabled: true,
      path: 'rules/bookwalker-cors.json'
    });

    expect(bookwalkerCorsRules).toHaveLength(1);
    const [rule] = bookwalkerCorsRules;
    expect(rule.condition).toEqual({
      urlFilter: '|https://bw-bv-epubs.bookwalker.jp/',
      initiatorDomains: ['viewer.bookwalker.jp'],
      requestDomains: ['bw-bv-epubs.bookwalker.jp'],
      requestMethods: ['get'],
      resourceTypes: ['xmlhttprequest']
    });
    expect(rule.action).toEqual({
      type: 'modifyHeaders',
      requestHeaders: [{
        header: 'Referer',
        operation: 'set',
        value: 'https://viewer.bookwalker.jp/'
      }],
      responseHeaders: [{
        header: 'Access-Control-Allow-Origin',
        operation: 'set',
        value: 'https://viewer.bookwalker.jp'
      }]
    });
  });
});
