/**
 * Fixed CDN/referrer mappings used by the service worker's transient DNR rules.
 */
(function(root) {
  'use strict';

  const CDN_REFERRER_RULES = Object.freeze([
    Object.freeze({
      id: 'pixiv',
      hostname: 'pximg.net',
      urlFilter: 'pximg.net',
      sessionRuleId: 1000,
      referrer: 'https://www.pixiv.net/'
    }),
    Object.freeze({
      id: 'manhuagui',
      hostname: 'i.hamreus.com',
      urlFilter: 'i.hamreus.com',
      sessionRuleId: 1001,
      referrer: 'https://www.manhuagui.com/'
    }),
    Object.freeze({
      id: 'hitomi',
      hostname: 'hitomi.la',
      urlFilter: 'hitomi.la',
      sessionRuleId: 1002,
      referrer: 'https://hitomi.la/'
    }),
    Object.freeze({
      id: 'klmanga',
      hostname: 'klimv1.xyz',
      urlFilter: 'klimv1.xyz',
      sessionRuleId: 1003,
      referrer: 'https://klmanga.com/'
    })
  ]);

  function getReferrerRule(url) {
    let hostname = '';
    try {
      hostname = new URL(url).hostname.toLowerCase();
    } catch (_error) {
      return null;
    }

    return CDN_REFERRER_RULES.find(rule =>
      hostname === rule.hostname || hostname.endsWith(`.${rule.hostname}`)
    ) || null;
  }

  function buildDnrRule(rule, ruleId) {
    if (!rule || !Number.isInteger(ruleId) || ruleId < 1) {
      throw new TypeError('A valid referrer rule and positive rule id are required');
    }

    return {
      id: ruleId,
      priority: 1,
      action: {
        type: 'modifyHeaders',
        requestHeaders: [{
          header: 'Referer',
          operation: 'set',
          value: rule.referrer
        }]
      },
      condition: {
        requestDomains: [rule.hostname],
        urlFilter: `||${rule.hostname}/`,
        resourceTypes: ['xmlhttprequest']
      }
    };
  }

  const exported = { CDN_REFERRER_RULES, getReferrerRule, buildDnrRule };
  if (root) root.MangaTranslatorReferrerRules = exported;
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
})(typeof globalThis !== 'undefined' ? globalThis : this);
