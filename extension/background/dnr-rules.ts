/**
 * Transient declarativeNetRequest session rules that set Referer for a fixed
 * allowlist of hotlink-protected CDNs while FETCH_IMAGE downloads from them.
 *
 * - Rule ids are limited to 1000-1999 (`DNR_RULE_ID_MIN`..`DNR_RULE_ID_MAX`).
 * - Installs, removals and reconciliation are serialized by one mutex.
 * - Concurrent fetches for the same CDN share one reference-counted lease.
 * - Rules left by an earlier worker are reconciled on worker load, install and
 *   first use; unrelated rules are never touched.
 */
import { browser } from 'wxt/browser';
import {
  buildDnrRule,
  CDN_REFERRER_RULES,
  type DnrRule,
  getReferrerRule,
  type ReferrerRule,
} from '../lib/referrer-rules';
import { errorMessage } from '../lib/utils';

export const DNR_RULE_ID_MIN = 1000;
export const DNR_RULE_ID_MAX = 1999;

interface DnrLease {
  referrerRule: ReferrerRule;
  refCount: number;
  enabled: boolean;
  ready: Promise<void> | null;
  ruleId: number;
  removeOnRelease: boolean;
}

interface CreatedLease {
  ruleId: number;
  removeOnRelease: boolean;
}

const dnrRuleLeases = new Map<string, DnrLease>();
let dnrSessionRulesById = new Map<number, DnrRule>();
let dnrReconciliationPromise: Promise<void> | null = null;
let dnrMutexTail: Promise<unknown> = Promise.resolve();

function getDnrApi(): typeof browser.declarativeNetRequest | undefined {
  return browser.declarativeNetRequest;
}

function isDnrRuleIdInOwnedRange(ruleId: unknown): boolean {
  return (
    Number.isInteger(ruleId) &&
    (ruleId as number) >= DNR_RULE_ID_MIN &&
    (ruleId as number) <= DNR_RULE_ID_MAX
  );
}

function getDnrRuleId(referrerRule: ReferrerRule): number {
  return Number.isInteger(referrerRule?.sessionRuleId)
    ? referrerRule.sessionRuleId
    : DNR_RULE_ID_MIN;
}

function hasExpectedReferrerAction(
  sessionRule: DnrRule | undefined,
  referrerRule: ReferrerRule,
): boolean {
  const headers = sessionRule?.action?.requestHeaders;
  const header = Array.isArray(headers) && headers.length === 1 ? headers[0] : null;
  return (
    sessionRule?.action?.type === 'modifyHeaders' &&
    header?.header === 'Referer' &&
    header?.operation === 'set' &&
    header?.value === referrerRule.referrer
  );
}

function hasCurrentDnrCondition(
  sessionRule: DnrRule | undefined,
  referrerRule: ReferrerRule,
): boolean {
  const condition = sessionRule?.condition;
  return (
    Array.isArray(condition?.requestDomains) &&
    condition.requestDomains.length === 1 &&
    condition.requestDomains[0] === referrerRule.hostname &&
    condition.urlFilter === `||${referrerRule.hostname}/` &&
    Array.isArray(condition.resourceTypes) &&
    condition.resourceTypes.length === 1 &&
    condition.resourceTypes[0] === 'xmlhttprequest'
  );
}

function hasLegacyDnrCondition(
  sessionRule: DnrRule | undefined,
  referrerRule: ReferrerRule,
): boolean {
  const condition = sessionRule?.condition;
  return (
    !condition?.requestDomains &&
    condition?.urlFilter === referrerRule.urlFilter &&
    Array.isArray(condition.resourceTypes) &&
    condition.resourceTypes.length === 1 &&
    condition.resourceTypes[0] === 'xmlhttprequest'
  );
}

/** Whether a session rule is one this extension installed for `referrerRule`. */
export function isOwnedDnrRule(
  sessionRule: DnrRule | undefined,
  referrerRule: ReferrerRule,
): boolean {
  return (
    isDnrRuleIdInOwnedRange(sessionRule?.id) &&
    hasExpectedReferrerAction(sessionRule, referrerRule) &&
    (hasCurrentDnrCondition(sessionRule, referrerRule) ||
      hasLegacyDnrCondition(sessionRule, referrerRule))
  );
}

function isOwnedByAnyReferrerRule(sessionRule: DnrRule): boolean {
  return CDN_REFERRER_RULES.some((rule) => isOwnedDnrRule(sessionRule, rule));
}

/**
 * Serialize every session-rule read-modify-write so an install can never
 * interleave with a pending remove (or reconcile) of the same rule id.
 */
function withDnrMutex<T>(task: () => Promise<T>): Promise<T> {
  const run = dnrMutexTail.then(task);
  dnrMutexTail = run.then(
    () => undefined,
    () => undefined,
  );
  return run;
}

async function reconcileDnrSessionRulesLocked(): Promise<void> {
  const api = getDnrApi();
  if (typeof api?.getSessionRules !== 'function') return;

  try {
    const rules = await api.getSessionRules();
    const list = Array.isArray(rules) ? rules : [];
    dnrSessionRulesById = new Map(list.map((rule) => [rule.id, rule]));
    const ownedStaleRules = list.filter(isOwnedByAnyReferrerRule);
    if (ownedStaleRules.length === 0 || typeof api.updateSessionRules !== 'function') return;

    await api.updateSessionRules({ removeRuleIds: ownedStaleRules.map((rule) => rule.id) });
    for (const rule of ownedStaleRules) dnrSessionRulesById.delete(rule.id);
  } catch (error) {
    // A failed reconciliation must not prevent ordinary image fetching.
    console.warn(
      '[Service Worker] Failed to reconcile transient referrer rules:',
      errorMessage(error),
    );
  }
}

/** Remove leftover referrer session rules from an earlier worker (only owned rules). */
export function reconcileDnrSessionRules(): Promise<void> {
  return withDnrMutex(reconcileDnrSessionRulesLocked);
}

/** Reconcile once per worker lifetime; later calls share the same promise. */
export function ensureDnrSessionRulesReady(): Promise<void> {
  if (typeof getDnrApi()?.getSessionRules !== 'function') return Promise.resolve();
  dnrReconciliationPromise ??= reconcileDnrSessionRules();
  return dnrReconciliationPromise;
}

/** Forget tracked rule state so the next use reconciles again (used on install). */
export function resetDnrReconciliation(): void {
  dnrReconciliationPromise = null;
  dnrSessionRulesById = new Map();
}

async function createDnrLeaseLocked(referrerRule: ReferrerRule): Promise<CreatedLease | null> {
  const api = getDnrApi();
  if (typeof api?.updateSessionRules !== 'function') return null;

  const ruleId = getDnrRuleId(referrerRule);
  const existingRule = dnrSessionRulesById.get(ruleId);
  if (existingRule && !isOwnedDnrRule(existingRule, referrerRule)) {
    console.warn('[Service Worker] Referrer rule id is occupied by an unrelated rule:', ruleId);
    return null;
  }

  const dnrRule = buildDnrRule(referrerRule, ruleId);
  const needsInstall = !existingRule || !hasCurrentDnrCondition(existingRule, referrerRule);

  try {
    if (needsInstall) {
      await api.updateSessionRules({
        ...(existingRule ? { removeRuleIds: [ruleId] } : {}),
        addRules: [dnrRule],
      });
    }
    dnrSessionRulesById.set(ruleId, dnrRule);
    return { ruleId, removeOnRelease: true };
  } catch (error) {
    console.warn(
      '[Service Worker] Failed to install transient referrer rule; using ordinary fetch:',
      errorMessage(error),
    );
    return null;
  }
}

async function acquireDnrLease(referrerRule: ReferrerRule): Promise<DnrLease | null> {
  const key = referrerRule.id;
  const current = dnrRuleLeases.get(key);
  if (current) {
    current.refCount += 1;
    await current.ready;
    return current.enabled ? current : null;
  }

  const lease: DnrLease = {
    referrerRule,
    refCount: 1,
    enabled: false,
    ready: null,
    ruleId: getDnrRuleId(referrerRule),
    removeOnRelease: false,
  };
  lease.ready = withDnrMutex(() => createDnrLeaseLocked(referrerRule))
    .then((result) => {
      if (!result) return;
      lease.enabled = true;
      lease.ruleId = result.ruleId;
      lease.removeOnRelease = result.removeOnRelease;
    })
    .catch((error) => {
      console.warn(
        '[Service Worker] Failed to acquire transient referrer rule:',
        errorMessage(error),
      );
    });
  dnrRuleLeases.set(key, lease);
  await lease.ready;
  if (!lease.enabled && dnrRuleLeases.get(key) === lease) {
    dnrRuleLeases.delete(key);
  }
  return lease.enabled ? lease : null;
}

async function releaseDnrLease(lease: DnrLease): Promise<void> {
  lease.refCount -= 1;
  if (lease.refCount > 0 || dnrRuleLeases.get(lease.referrerRule.id) !== lease) return;
  dnrRuleLeases.delete(lease.referrerRule.id);

  if (!lease.removeOnRelease) return;
  const api = getDnrApi();
  if (typeof api?.updateSessionRules !== 'function') return;

  await withDnrMutex(async () => {
    // Forget the rule before the remove is issued so an acquire queued behind
    // this task reinstalls it instead of trusting a rule that is going away.
    const removedRule = dnrSessionRulesById.get(lease.ruleId);
    dnrSessionRulesById.delete(lease.ruleId);
    try {
      await api.updateSessionRules({ removeRuleIds: [lease.ruleId] });
    } catch (error) {
      if (removedRule && !dnrSessionRulesById.has(lease.ruleId)) {
        dnrSessionRulesById.set(lease.ruleId, removedRule);
      }
      console.warn(
        '[Service Worker] Failed to remove transient referrer rule:',
        errorMessage(error),
      );
    }
  });
}

/**
 * Run `operation` with the Referer session rule for `url`'s CDN installed.
 * `operation` receives the matched rule, or null when the host is not on the
 * allowlist or the rule could not be installed (then fetch normally).
 */
export async function withDnrReferrerRule<T>(
  url: string,
  operation: (referrerRule: ReferrerRule | null) => Promise<T>,
): Promise<T> {
  const referrerRule = getReferrerRule(url);

  if (!referrerRule || typeof getDnrApi()?.updateSessionRules !== 'function') {
    return operation(null);
  }

  await ensureDnrSessionRulesReady();
  const lease = await acquireDnrLease(referrerRule);
  if (!lease) return operation(null);

  try {
    return await operation(referrerRule);
  } finally {
    await releaseDnrLease(lease);
  }
}
