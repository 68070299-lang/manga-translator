import { describe, expect, it } from 'vitest';
import {
  buildDnrRule,
  CDN_REFERRER_RULES,
  getReferrerRule,
} from '../../extension/lib/referrer-rules';

describe('fixed CDN referrer rules', () => {
  it('matches known CDNs and subdomains only', () => {
    expect(getReferrerRule('https://i.hamreus.com/books/page.png')).toEqual(
      expect.objectContaining({ id: 'manhuagui', referrer: 'https://www.manhuagui.com/' }),
    );
    expect(getReferrerRule('https://img.pximg.net/a.png')).toEqual(
      expect.objectContaining({ id: 'pixiv' }),
    );
    expect(getReferrerRule('https://not-pximg.net/a.png')).toBeNull();
    expect(getReferrerRule('data:image/png;base64,abc')).toBeNull();
  });

  it('builds a session rule scoped to XMLHttpRequest fetches', () => {
    const rule = buildDnrRule(CDN_REFERRER_RULES[0]!, 1234);
    expect(rule).toEqual(expect.objectContaining({ id: 1234, priority: 1 }));
    expect(rule.action).toEqual({
      type: 'modifyHeaders',
      requestHeaders: [
        {
          header: 'Referer',
          operation: 'set',
          value: 'https://www.pixiv.net/',
        },
      ],
    });
    expect(rule.condition).toEqual({
      requestDomains: ['pximg.net'],
      urlFilter: '||pximg.net/',
      resourceTypes: ['xmlhttprequest'],
    });
  });
});
