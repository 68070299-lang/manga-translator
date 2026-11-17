/**
 * Fixed CDN/referrer mappings used by the service worker's transient DNR rules.
 * The allowlist is fixed in code: nothing in settings or page content can add a host.
 */
import type { Browser } from 'wxt/browser';

export interface ReferrerRule {
  readonly id: string;
  readonly hostname: string;
  /** Legacy session rules matched on this plain URL filter. */
  readonly urlFilter: string;
  /** Rule id used for the transient session rule (within 1000-1999). */
  readonly sessionRuleId: number;
  readonly referrer: string;
}

export const CDN_REFERRER_RULES: readonly ReferrerRule[] = Object.freeze([
  Object.freeze({
    id: 'pixiv',
    hostname: 'pximg.net',
    urlFilter: 'pximg.net',
    sessionRuleId: 1000,
    referrer: 'https://www.pixiv.net/',
  }),
  Object.freeze({
    id: 'manhuagui',
    hostname: 'i.hamreus.com',
    urlFilter: 'i.hamreus.com',
    sessionRuleId: 1001,
    referrer: 'https://www.manhuagui.com/',
  }),
  Object.freeze({
    id: 'hitomi',
    hostname: 'hitomi.la',
    urlFilter: 'hitomi.la',
    sessionRuleId: 1002,
    referrer: 'https://hitomi.la/',
  }),
  Object.freeze({
    id: 'klmanga',
    hostname: 'klimv1.xyz',
    urlFilter: 'klimv1.xyz',
    sessionRuleId: 1003,
    referrer: 'https://klmanga.com/',
  }),
]);

/** The rule for a URL on an allowlisted CDN host (or a subdomain of it), else null. */
export function getReferrerRule(url: string): ReferrerRule | null {
  let hostname = '';
  try {
    hostname = new URL(url).hostname.toLowerCase();
  } catch {
    return null;
  }

  return (
    CDN_REFERRER_RULES.find(
      (rule) => hostname === rule.hostname || hostname.endsWith(`.${rule.hostname}`),
    ) ?? null
  );
}

/** A `declarativeNetRequest` session rule. */
export type DnrRule = Browser.declarativeNetRequest.Rule;

/** Build the `modifyHeaders` session rule that sets Referer for XHR requests to one host. */
export function buildDnrRule(rule: ReferrerRule, ruleId: number): DnrRule {
  if (!rule || !Number.isInteger(ruleId) || ruleId < 1) {
    throw new TypeError('A valid referrer rule and positive rule id are required');
  }

  return {
    id: ruleId,
    priority: 1,
    action: {
      type: 'modifyHeaders',
      requestHeaders: [
        {
          header: 'Referer',
          operation: 'set',
          value: rule.referrer,
        },
      ],
    },
    condition: {
      requestDomains: [rule.hostname],
      urlFilter: `||${rule.hostname}/`,
      resourceTypes: ['xmlhttprequest'],
    },
  } as DnrRule;
}
