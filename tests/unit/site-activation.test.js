const SiteActivation = require('../../extension/lib/site-activation');

describe('site activation', () => {
  beforeEach(async () => {
    await chrome.storage.local.clear();
  });

  it('keeps supported sites enabled when no overrides exist', async () => {
    await expect(SiteActivation.getSiteState('https://Example.com/reader/1')).resolves.toEqual({
      hostname: 'example.com',
      enabled: true,
      supported: true,
      activeUrls: [],
      disabledUrls: []
    });
  });

  it('disables and re-enables one hostname without affecting other sites', async () => {
    await expect(SiteActivation.setSiteEnabled('https://reader.example.com/page', false))
      .resolves.toEqual(expect.objectContaining({ hostname: 'reader.example.com', enabled: false }));
    await expect(SiteActivation.getSiteState('https://reader.example.com/next'))
      .resolves.toEqual(expect.objectContaining({ enabled: false }));
    await expect(SiteActivation.getSiteState('https://other.example.com/next'))
      .resolves.toEqual(expect.objectContaining({ enabled: true }));

    await expect(SiteActivation.setSiteEnabled('reader.example.com', true))
      .resolves.toEqual(expect.objectContaining({ enabled: true, disabledUrls: [] }));
  });

  it('honors an explicit active host list', async () => {
    await chrome.storage.local.set({ activeUrls: ['one.example.com'] });

    expect(SiteActivation.isSiteEnabledFromState('one.example.com', {
      activeUrls: ['one.example.com'],
      disabledUrls: []
    })).toBe(true);
    expect(SiteActivation.isSiteEnabledFromState('two.example.com', {
      activeUrls: ['one.example.com'],
      disabledUrls: []
    })).toBe(false);
  });

  it('rejects unsupported URLs for state changes', async () => {
    await expect(SiteActivation.getSiteState('chrome://settings')).resolves.toEqual(expect.objectContaining({
      hostname: 'settings',
      enabled: false,
      supported: false
    }));
    await expect(SiteActivation.setSiteEnabled('not a url', false))
      .rejects.toThrow('Cannot change activation');
    expect(SiteActivation.isSupportedUrl('chrome://settings')).toBe(false);
  });
});
