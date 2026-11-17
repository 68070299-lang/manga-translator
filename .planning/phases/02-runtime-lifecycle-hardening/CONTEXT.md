# Phase 2 Context: Runtime Lifecycle Hardening

## Goal

Make the personal-use Ichigo-inspired runtime reliable across MV3 worker
restarts, hostname toggles, and temporary message-channel failures.

## Scope

- Reconcile only owned transient DNR rules and use fixed per-CDN IDs.
- Anchor DNR matches by request domain and fall back safely when installation
  fails.
- Dispose site-scoped observers, listeners, timers, controls, and intervals.
- Refresh toolbar state at worker startup and installation.
- Recheck persistent cache state before provider execution and retry only
  transport-level worker failures once.

## Constraints

- Do not add login, subscriptions, model catalogs, cookies, or account state.
- Do not persist raw base64 image payloads in extension storage.
- Preserve completed translations when a hostname is disabled.
- Keep local provider concurrency serial and page-level concurrency unchanged.
