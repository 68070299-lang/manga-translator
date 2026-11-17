# Phase 1 Context: Reliable Personal Translation Runtime

## User Intent

Use the useful Ichigo runtime ideas to improve a personal manga translator.
Do not add login, subscription tiers, model catalog, cookies, or account state.

## Decisions

- Implement a local FIFO queue with a `Map<cacheKey, Promise>` style in-flight
  registry. Do not copy Ichigo's `FullQueue` polling protocol because this
  extension controls both request and worker sides.
- Keep local backend concurrency at one. Allow cloud concurrency up to three.
- Keep current page-level concurrency at one for BookWalker and composite-reader
  ordering.
- Make hostname activation default to enabled when no override is stored. An
  explicit disabled hostname suppresses startup observers and auto processing.
- Use only four fixed CDN mappings for DNR referrer rules: pximg.net to
  pixiv.net, i.hamreus.com to manhuagui.com, hitomi.la to hitomi.la, and
  klimv1.xyz to klmanga.com.

## Discretion

- Exact popup copy and icon-state presentation may follow the existing visual
  language.
- Promise deduplication may use a small reusable library module rather than
  duplicating queue logic in the worker.

## Deferred

- Snapshot capture and crop calibration.
- Shadow DOM overlay isolation.
- Bundled font registry.
- Signed CRX integrity files.
