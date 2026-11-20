# LOC Reduction Plan

**Who this is for:** an implementing agent. Follow it literally. Do not improvise scope.
**Written against:** commit `3dae820` on `integrate/wp1-wp7` (2026-11-20). Line numbers below are
hints from that commit — always locate code by **symbol / test name**, not by line number.

## Goal

| Scope | Baseline | Target after this plan |
|---|---|---|
| Production code (`backend/src`, `extension/`, `backend/start.ps1`) | 30,121 | **≈ 28,900** (must end < 30,000) |
| All tracked code (`.py .ts .svelte .css .html .ps1`) | 61,830 | ≈ 51,500 |
| Tracked eval JSON baselines (`eval/`) | 25,641 | 0 |

Measure with (Git Bash):

```bash
# all code
git ls-files '*.py' '*.ts' '*.svelte' '*.css' '*.html' '*.ps1' | xargs -d '\n' cat | wc -l
# production only
git ls-files 'backend/src/*' 'extension/*' backend/start.ps1 | grep -E '\.(py|ts|svelte|css|html|ps1)$' | xargs -d '\n' cat | wc -l
```

## Decisions already made by the owner (do not revisit)

- **Remove** Cloud API mode (extension calls Gemini/OpenAI directly + SVG overlay).
- **Remove** Google Vision OCR (`google-ocr` engine).
- **Remove** dev tools CI never runs (eval harness, smoke scripts, BookWalker UAT, detector comparison) and their tests.
- **Remove** low-value tests (tooling tests, e2e tests that duplicate unit tests, source-text/constant tests).
- **KEEP** every optional model backend: FLUX/LanPaint/Klein inpainters, `manga_backend/comfy_lanpaint/`,
  `scripts/comfy-lanpaint/*`, AnimeText YOLO + `scripts/export_animetext_yolo.py`, PaddleOCR ONNX,
  `scripts/build_fp16_models.py`.

## Ground rules (read twice)

1. **Branch:** `git switch -c chore/loc-reduction` from `integrate/wp1-wp7`. Never push. Never merge.
2. **Commit once per phase**, message `chore(loc): phase N - <title>`. Stage explicit paths only
   (`git add <path>`, `git rm <path>`). **Never `git add -A` / `git add .`** — the owner keeps private
   untracked files (e.g. `eval/pages/`) that must never be committed.
3. **Do not touch:** the KEEP list above; translator providers in `backend/src/manga_backend/models/translator.py`
   and the translator alias `"google": "gemini"` in `api/validation.py` (that is Gemini translation, NOT Google OCR);
   algorithms in `pipeline/masks.py`; coverage thresholds in `vitest.config.ts`; `biome.json`; ruff/pyright config
   (except the one `include` line named in Phase 1); `.gitignore` entries for `eval/pages/` and `eval/runs/`.
4. **No line-count gaming:** don't join lines, raise line width, reformat, or strip comments/docstrings.
   Only delete comments that describe code you removed.
5. **Behavior must not change** in Phases 5–7. Those are pure refactors; every remaining test must still pass
   and Phase 7 must not change the number of tests.
6. **Never lower a coverage threshold.** If a coverage gate fails, restore tests (see Troubleshooting).
7. **Stop rule:** if a check fails and two honest fix attempts don't fix it, restore that phase's uncommitted
   edits (`git restore --staged --worktree <files>`; for deleted files `git restore --source=HEAD <files>`),
   write what failed under **Results** at the bottom of this file, commit only that note, and stop.
8. After deleting any code, run `git grep -n -w <Name>` for every helper/type/import it used. If the only hit is
   its own definition, delete it too. Ruff flags unused Python imports (`F401`); for TS, `npm run typecheck`
   and biome flag most leftovers.

## Commands

| Check | Command |
|---|---|
| Backend lint | `npm run lint:backend` |
| Backend types | `npm run typecheck:backend` |
| Backend tests | `npm run test:backend` |
| Extension lint | `npm run lint` |
| Extension types | `npm run typecheck` |
| Build (**run before unit tests** — `tests/unit/manifest.test.ts` reads `dist/chrome-mv3`) | `npm run build` |
| Unit tests + coverage gate | `npm run test:coverage` |
| E2E (needs a fresh build) | `npx playwright test` |
| Repo hygiene | `npm run verify:repository` |
| Everything CI runs | `npm run verify` then `npx playwright test` |

**Known noise (rerun before believing a failure):**
- `content` unit test "region selection" is intermittently flaky — rerun that file alone: `npx vitest run tests/unit/content.test.ts`.
- If a real backend is running on `127.0.0.1:8001`, e2e popup tests can fail with 401. Stop that backend first.
- A stale `dist/chrome-mv3` makes 2 tests in `manifest.test.ts` fail. Run `npm run build` first.

**Phase gate** (run at the end of every phase before committing):
`npm run lint:backend && npm run typecheck:backend && npm run test:backend && npm run lint && npm run typecheck && npm run build && npm run test:coverage && npm run verify:repository`
Phases that touch `tests/e2e/` or extension code also run `npx playwright test`.

---

## Phase 0 — Setup and baseline

- [ ] `git status` is clean (this plan file may be untracked — that's fine).
- [ ] Create the branch (rule 1). Commit this plan file: `git add .planning/loc-reduction/PLAN.md`.
- [ ] Run the phase gate + `npx playwright test`. Record pass/fail counts under **Results → Baseline**.
      If anything fails that isn't in "Known noise", stop and report — don't start cutting on a red baseline.
- [ ] Record both LOC numbers under **Results → Baseline**.

## Phase 1 — Delete dev tools that CI never runs (≈ −6.3K code, −25.6K JSON)

Delete (`git rm -r`):

| Path | Lines |
|---|---|
| `scripts/eval_pipeline.py` | 2523 |
| `scripts/eval_reading_order.py` | 311 |
| `eval/` (whole tracked dir: README, corpus.json, baselines) | 25,641 (JSON/MD) |
| `backend/tests/test_eval_pipeline.py` | 1023 |
| `scripts/real-url-smoke.ts` | 740 |
| `scripts/popup-smoke.ts` | 150 |
| `scripts/backend-smoke.py` | 279 |
| `backend/tests/test_backend_smoke.py` | 198 |
| `scripts/bookwalker-real-uat.ps1` | 566 |
| `scripts/prepare-bookwalker-profile.ts` | 197 |
| `tests/unit/bookwalker-uat.test.ts` | 53 |
| `scripts/compare_detectors.py` | 217 |

Do **not** delete `scripts/lib/extension.ts` (e2e uses it), `scripts/extension-key.ts`, `scripts/validate-extension.ts`,
`scripts/repository-check.ts`, `scripts/comfy-lanpaint/*`, `scripts/export_animetext_yolo.py`, `scripts/build_fp16_models.py`.

Edit:

- [ ] `package.json` → remove scripts `smoke:popup`, `smoke:real-url`, `smoke:backend`, `eval:pipeline`, `uat:bookwalker`.
      Change `lint:backend` to `uv run --project backend --locked ruff check backend`.
- [ ] `backend/pyproject.toml` → `include = ["src", "../scripts/eval_pipeline.py"]` becomes `include = ["src"]`.
- [ ] `scripts/repository-check.ts` → in `REQUIRED_PATHS` remove `'scripts/bookwalker-real-uat.ps1'`,
      `'scripts/prepare-bookwalker-profile.ts'`, `'scripts/popup-smoke.ts'`, `'scripts/real-url-smoke.ts'`.
      **Keep** the `'eval/pages/'`, `'eval/runs/'` entries in the forbidden list and in `GENERATED_PATHS`
      (they stop private pages from being committed). Keep `LEGACY_PATHS` as is.
- [ ] Docs: `git grep -n -E "eval_pipeline|eval_reading_order|real-url-smoke|popup-smoke|backend-smoke|smoke:backend|smoke:real-url|smoke:popup|bookwalker-real-uat|uat:bookwalker|prepare-bookwalker-profile|compare_detectors|eval:pipeline"`
      Remove or reword each hit in `README.md`, `backend/README.md`, `MAINTENANCE_COMPLETION_GUIDE.md`,
      `.planning/codebase/STACK.md`, `tests/**/README.md`. Leave `plan.md` and `.planning/phases/**` (historical) alone.
      The grep must then return only historical files.
- [ ] Phase gate. Commit.

## Phase 2 — Remove Google Vision OCR (≈ −0.2K production, −0.4K tests)

After this phase, OCR engines are `manga-ocr` (default) and `paddle-ocr` only.

### Backend

- [ ] `pipeline/ocr.py`: delete `GOOGLE_OCR_CONCURRENCY`, `GOOGLE_OCR_URL`, `GOOGLE_OCR_TIMEOUT_SECONDS`,
      `GOOGLE_OCR_NAMES`, `_upstream_status_error`, `_annotation_text`, `_encode_png_base64`, `google_ocr`
      (confirm each with `git grep -w` first). In `run_ocr` delete the `elif ocr in GOOGLE_OCR_NAMES:` branch and
      the parameters `google_ocr_api_key`, `google_ocr_language_hint`. Fix the module docstring
      (`"OCR routing across manga-ocr and PaddleOCR."`). Remove the `GoogleOCRError` import.
- [ ] `pipeline/errors.py`: delete `GoogleOCRError`, `GoogleOCRTimeoutError`.
      `pipeline/__init__.py`: drop them from imports and `__all__`. `api/errors.py`: drop their imports and handlers.
- [ ] `pipeline/orchestrator.py`: remove `google_ocr_api_key` / `google_ocr_language_hint` parameters from every
      method signature (3 places, ~L259, ~L287, ~L311) and from every call that passes them (~L267, ~L294, ~L384).
- [ ] `api/deps.py`: delete the two `google_ocr_*` `Form` fields and where they're passed into the options object;
      change the OCR description to `"OCR model: manga-ocr or paddle-ocr"`.
- [ ] `api/routes/ocr.py`: same — delete the two fields, the `engine == "google-ocr"` check, the two kwargs, and
      `sensitive_values=[google_ocr_api_key]` (delete the kwarg entirely if that list becomes empty and the callee
      has a default; otherwise pass `[]`).
- [ ] `api/routes/translate.py`: delete the `ocr == "google-ocr"` check and the two kwargs.
- [ ] `api/schemas.py`: delete the two fields; remove `self.google_ocr_api_key` from the sensitive-values list.
- [ ] `api/sanitize.py`: remove `"google_ocr_api_key"` from `_REDACTED_FIELD_NAMES`.
- [ ] `api/validation.py`: in `VALID_OCR_ENGINES` delete `"google-ocr"`, `"googleocr"`, `"google"`.
      **Do not touch `VALID_TRANSLATORS`.** Delete `validate_language_hint`, its call in
      `preflight_translator_inputs`, and `LANGUAGE_HINT_RE` if nothing else uses it.
- [ ] Tests: in `backend/tests/test_pipeline.py` delete `test_ocr_routes_google_with_api_key_and_language_hint`,
      `test_google_ocr_runs_concurrently_within_limit_and_keeps_block_order`,
      `test_google_ocr_failure_cancels_remaining_requests_and_stays_sanitized`,
      `test_ocr_rejects_google_without_api_key_as_client_error`, `test_google_ocr_skips_zero_area_regions`.
      In `test_api.py` and `test_core_contract.py`: delete tests that only exercise Google OCR; in mixed tests,
      remove the google fields/expectations only.

### Extension

- [ ] `git rm extension/components/options/GoogleOcrSection.svelte`; remove its import and `<GoogleOcrSection …/>`
      from `extension/entrypoints/options/App.svelte`; remove `{ id: 'google-ocr', … }` from `OptionsNav.svelte`.
- [ ] `shared/types.ts`: remove the `googleOcr` settings type/field and `'google-ocr'` from the OCR union.
- [ ] `lib/settings.ts`: remove `googleOcr` defaults, private-key handling for `googleOcr.apiKey`, and normalization.
      A stored `ocr: 'google-ocr'` must load as the default OCR — the existing malformed-value fallback should do
      this; keep/adjust the unit test "shows the default OCR instead of a blank selector for malformed storage"
      and add one case for `'google-ocr'` if it's a one-line addition.
- [ ] `lib/api.ts` (`LocalBackendProvider`): drop the `googleOcr` config field and the `google_ocr_api_key` /
      `google_ocr_language_hint` form fields.
- [ ] `background/providers.ts`: remove `googleOcr: settings.googleOcr`.
      `background/settings-migration.ts`: remove the `settings.googleOcr?.apiKey` check.
- [ ] `lib/cache.ts`: remove the 3 `googleOcr` references from the cache identity.
- [ ] `ui/choices.ts`: delete `GOOGLE_LANGUAGE_HINT_CHOICES` and the `google-ocr` OCR choice.
- [ ] `ui/options/options-state.svelte.ts`, `ui/options/forms.svelte.ts`: delete `googleOcr` form state and
      `googleKeyWarningVisible`. Delete CSS rules in `options.css` that only the Google section used.
- [ ] Tests: delete Google-OCR tests in `tests/unit/options.test.ts` ("warns when google-ocr is selected…",
      "does not warn about google-ocr when a Google Vision key is stored", "does not warn about google-ocr in cloud
      mode"), `api.test.ts` ("sends Google OCR key only when google-ocr is selected"), and the google cases in
      `settings.test.ts`, `service-worker.test.ts`, `cache.test.ts`, `core-contracts.test.ts`. In shared fixtures,
      just remove the `googleOcr` property.
- [ ] Docs: update `README.md`, `backend/README.md`, `docs/RUNTIME_CONTRACT.md`, `MAINTENANCE_COMPLETION_GUIDE.md`.

**Done when** this returns nothing outside `plan.md` and `.planning/phases/`:

```bash
git grep -n -iE "google_ocr|GoogleOCR|GOOGLE_OCR|google-ocr|googleocr|googleOcr|GoogleOcr|GOOGLE_LANGUAGE_HINT|googleKeyWarning|language_hint|languageHint|vision\.googleapis"
```

- [ ] Phase gate + `npx playwright test`. Commit.

## Phase 3 — Remove Cloud API mode (≈ −0.8K production, −0.8K tests)

After this phase the extension always uses the local backend. Provider/apiKey/model settings **stay**
(the local backend still uses them to pick a translator).

- [ ] `git rm extension/content/svg-overlay.ts extension/components/popup/PipelineMode.svelte`.
- [ ] `extension/entrypoints/popup/App.svelte`: remove the `PipelineMode` import and `<PipelineMode {popup} />`.
      `popup.css`: delete rules for class names that only `PipelineMode.svelte` used (`git grep` each class name).
- [ ] `content/translate.ts`: remove the `renderTranslationOverlay` import; in `ensureBackendReady` delete the
      `pipelineMode !== 'local'` branch; in the result handler delete the
      `else if (Array.isArray(result) || (result?.blocks?.length ?? 0) > 0)` branch (keep the
      `translatedImage` branch and the "No text found" `else`).
- [ ] `content/canvas-memory.ts`: remove the `renderTranslationOverlay` import and the `{ type: 'blocks' }`
      record path (the `blocks` extraction ~L47 and the overlay restore ~L109). Keep image-record restore.
- [ ] `content/types.ts`: `mode?: 'cloud' | 'local'` → `mode?: 'local'`.
- [ ] `lib/api.ts`: delete `TranslationProvider`, `TranslationProviderConfig`, `GeminiResponse`, `OpenAiResponse`,
      `PROVIDER_BASE_URLS`, `PROVIDER_DEFAULT_MODELS`, `CLOUD_REQUEST_TIMEOUT_MS`, `GEMINI_BLOCKED_FINISH_REASONS`.
      Then check each of `normalizeBase64ImageInput`, `Base64ImageInput`, `normalizeTranslationBlocks`, `toNumbers`,
      `sanitizeErrorBody`, `truncateErrorBody` with `git grep -w` across `extension/` — delete only the ones
      nothing in `extension/` uses any more. Rewrite the file header comment.
- [ ] `background/providers.ts`: delete `handleCloudTranslation`; fix header comment and imports.
- [ ] `background/translate.ts`: delete `cloudTranslationQueue`; `getTranslationQueues()` returns `{ local }` only
      (update its test usages); always call `handleLocalBackendTranslation`; always use `localTranslationQueue`;
      in `isUsableTranslationResult` delete the `Array.isArray(result)` and `mode === 'cloud'` branches.
      Fix the header comment ("one job at a time").
- [ ] `shared/types.ts`: delete `PipelineMode`, the `pipelineMode` settings field, `CloudTranslationResult`, and any
      block types that nothing uses any more.
- [ ] `lib/settings.ts`: delete `pipelineMode: 'local'` default; in `resolveTranslator` delete the
      `merged.pipelineMode === 'cloud'` branch.
- [ ] `lib/cache.ts`: replace `pipelineMode: settings.pipelineMode || 'local'` with `pipelineMode: 'local'`
      (keeps existing cache keys valid — do not drop the property).
- [ ] `ui/popup/popup-state.svelte.ts`: delete `pipelineMode` state and `setPipelineMode`; the backend status is
      always visible (remove `backendVisible` if it becomes always-true, and its `{#if}` in markup).
      `ui/popup/progress.ts`: `getTranslationSetupError` keeps only the backend-URL check;
      `getApiKeyWarning` always uses the local-backend text.
- [ ] `ui/options/options-state.svelte.ts`: delete `pipelineMode` state; treat every `this.pipelineMode === 'local'`
      condition as `true` and simplify.
- [ ] Tests — delete (or, where noted, convert to local mode):
  - `tests/unit/api.test.ts`: `describe('TranslationProvider')`, `describe('unparseable responses')`, and the
    TranslationProvider cases in `describe('upstream error bodies')` (keep `sanitizeErrorBody` tests only if that
    function still exists).
  - `tests/unit/service-worker.test.ts`: "normalizes cloud provider blocks before caching", "refuses the Local LLM
    provider in cloud mode"; convert "runs local jobs one at a time and cloud jobs three at a time" to the local half
    only; any other test that sets `pipelineMode: 'cloud'` → switch to local if it's a one-line change, else delete.
  - `tests/unit/content.test.ts`: "treats an empty cloud result as a successful no-op".
  - `tests/unit/popup.test.ts`: `describe('popup pipeline mode')`, "ignores the OCR-only local translator in cloud
    mode", "blocks cloud translation without an API key", "does not probe the backend in cloud mode".
  - `tests/unit/options.test.ts`: "preserves the popup-selected pipeline mode when saving options-only fields",
    "ignores the local translator override in cloud mode".
  - `settings.test.ts`, `cache.test.ts`: cloud cases.
  - `tests/integration/service-worker-provider.test.ts`: delete the file if it only exercises cloud mode; otherwise
    switch it to local.
  - `tests/e2e/bookwalker.spec.ts`: "cloud text overlay on BookWalker canvas does not wrap or move the canvas".
  - `tests/e2e/popup.spec.ts`: "pipeline mode toggle reveals local backend status only for local mode",
    "cloud mode API key warning ignores OCR-only local backend setting".
  - Everywhere else: remove `pipelineMode: 'local'` properties from fixtures (TypeScript will flag them).
- [ ] Docs: remove Cloud API mode from `README.md`, `docs/RUNTIME_CONTRACT.md`, `MAINTENANCE_COMPLETION_GUIDE.md`,
      `.planning/STATE.md`, `.planning/codebase/*.md`.

**Done when** this returns nothing in `extension/` or `tests/`:

```bash
git grep -n -E "pipelineMode|PipelineMode|cloudTranslation|handleCloudTranslation|TranslationProvider\b|svg-overlay|renderTranslationOverlay|CLOUD_REQUEST|mode-cloud|'cloud'|CloudTranslationResult" -- extension tests
```

- [ ] Phase gate + `npx playwright test`. Commit. (Coverage: code and its tests leave together, so the gates
      should hold — see Troubleshooting if not.)

## Phase 4 — Delete low-value tests (≈ −0.8K)

E2E tests don't count toward the vitest coverage gate; unit-test deletions here are tooling/source-text tests.

- [ ] `git rm tests/unit/repository-check.test.ts` (tests the repo-hygiene script; the script itself still runs in CI).
- [ ] `tests/unit/validate-extension.test.ts`: keep only `describe('remote code')` and the final
      `it('passes validation')` (plus helpers/imports they use). Delete every other `describe`/`it`.
      (The validator already runs on every real build via `wxt.config.ts`.)
- [ ] `git rm tests/e2e/options.spec.ts` — all 4 tests duplicate `tests/unit/options.test.ts` tests of the same name.
- [ ] `git rm tests/e2e/extension.spec.ts` — "extension popup loads" is implied by every popup test.
- [ ] `tests/e2e/popup.spec.ts`: keep only "popup uses the Ichigo-style visual shell without compact text overflow".
      Delete the rest (each has a unit twin in `tests/unit/popup.test.ts`) and any helper the kept test doesn't use.
- [ ] `tests/e2e/wiring.spec.ts`: delete "the worker serves the bundled font and the browser accepts its bytes"
      (flaky; covered by `service-worker.test.ts` "serves a registered bundled font as base64" and `font-registry.test.ts`).
- [ ] `tests/e2e/bookwalker.spec.ts`: delete "canvas backend timeout clears processing state and is not immediately
      retried", and first add its one unique assertion to "BookWalker auto translate does not loop after backend
      timeout", right after the idle-state poll:
      `expect((await getCanvasOverlayState(page)).progressText).toContain('timed out');`
- [ ] `tests/unit/overlay-dom.test.ts`: delete "shadow overlay stylesheet keeps progress spinner contract available"
      (asserts on source text).
- [ ] `backend/tests/test_comfy_lanpaint.py`: delete `test_default_prompt_is_general_and_not_material_specific`
      (asserts prompt wording).
- [ ] Update `tests/e2e/README.md` / `tests/unit/README.md` if they list deleted files.
- [ ] Phase gate + `npx playwright test`. Commit.

## Phase 5 — Delete dead code (≈ −60)

Each has exactly one hit (its definition) in the whole repo at `3dae820` — re-check with `git grep -n -w`.

- [ ] `backend/src/manga_backend/pipeline/fill_tiers.py`: `_lattice_paint`
- [ ] `backend/src/manga_backend/pipeline/measure.py`: `_median_run`
- [ ] `backend/src/manga_backend/pipeline/fit.py`: `_bands_for`
- [ ] `extension/shared/messages.ts`: `export type PopupMessageType`
- [ ] `uv run --project backend --locked ruff check backend/src --select F401,RET505 --fix`
- [ ] Phase gate. Commit.

## Phase 6 — Merge duplicated production code (≈ −150, behavior identical)

Run the named backend test file after each item, the full phase gate at the end.

- [ ] **6.1 Shared NMS + un-letterbox.** Create `backend/src/manga_backend/utils/boxes.py` with:
  - `nms(boxes, scores, threshold) -> list[int]` — copy the body of `TextSegmenter._nms` verbatim.
  - `unletterbox_blocks(boxes, scores, keep, image_shape, scale, pad) -> list[TextBlock]` — copy the loop at the
    end of `TextSegmenter._decode_blk` (from `height, width = image_shape` to `return blocks`), reading
    `scores[idx]` for confidence.
  In `models/text_segmenter.py` and `models/animetext_yolo_detector.py`: delete both `_nms` methods, call `nms(...)`,
  and replace both decode tail loops with `return unletterbox_blocks(boxes, scores, keep, image_shape, scale, pad)`
  (YOLO passes `values[:, 4]` as `scores`). **Do not merge the two `_letterbox` methods** — they resize with
  different libraries and pad values, so outputs would change.
  Test: `uv run --project backend --locked pytest backend/tests/test_text_segmenter.py backend/tests/test_animetext_yolo_detector.py`
- [ ] **6.2 `models/detector.py`.** Add `_greedy_dedup(candidates: Iterable[TextBlock]) -> list[TextBlock]` holding
  the shared `kept` loop (same `block_type`, `_is_duplicate_box(..., iou_threshold=0.45, contain_threshold=0.80)`).
  `_dedup_same_side_blocks` → `return _greedy_dedup(sorted(blocks, key=lambda b: b.confidence, reverse=True))`.
  `_merge_spread_text_blocks` ends with `return _greedy_dedup(block for block, _side in tagged)`.
  Test: `test_detector_inpainter.py`, `test_pipeline.py`.
- [ ] **6.3 `pipeline/rendering.py`.** `_nearest_interior` and `_nearest_estimate` share the overlap-score loop.
  Add generic `_best_overlap(key, mapping)` returning the best value or `None`; `_nearest_interior` keeps its
  `if key in interiors` fast path then returns `_best_overlap(key, interiors)`; `_nearest_estimate` returns
  `_best_overlap(key, estimates)`. Test: `test_typesetting.py`, `test_pipeline.py`.
- [ ] **6.4 `pipeline/masks.py`.** `_compute_bubble_interior_details` (~L1952) and the function containing ~L2026–2037
  repeat: `_interior_window` → two early `InteriorResult(...)` returns → `roi`. Extract
  `_interior_roi(image, block)` returning either the early `InteriorResult` or `(bounds, local_text, detector_bounds, roi)`;
  callers do `got = _interior_roi(...); if isinstance(got, InteriorResult): return got`. Move nothing else.
  Test: `test_masks.py`, `test_pipeline.py`.
- [ ] **6.5 `pipeline/residual.py`.** The two identical early `return ResidueObservation(residual_px=0, …, flagged=False,
  residue=residue)` blocks (~L211, ~L245) → one local helper inside the function. Test: `test_residual.py`.
- [ ] **6.6 `pipeline/fit.py`.** In the size-search function (docstring "Coarse-then-bisect downward search…") the
  three 11-line `_fit_at_size(room, units, joiner, font_path, <size>, line_spacing, base_stroke_width, mode,
  strict_hyphenation=…, keep_short_words=…)` calls → one local `def fit(size): return _fit_at_size(...)`.
  Leave the band preamble shared by `_fit_at_size` and the capacity function alone unless both end identically
  (they don't: one returns `None`, the other `0`). Test: `test_typesetting.py`, `test_pipeline.py`.
- [ ] **6.7 `models/translator.py`.** The identical `logger.warning("Parser returned %s translations but expected %s. …")`
  blocks (~L626, ~L664) → `_warn_if_short(translations, expected_count)` module helper. Test: `test_translator.py`.
- [ ] Phase gate. Commit.

## Phase 7 — De-duplicate test code (≈ −0.8K, same number of tests)

Only setup/helper code moves. **No assertion may be removed.** Count tests before and after:
`npx vitest list 2>/dev/null | wc -l`, `npx playwright test --list | tail -1`,
`uv run --project backend --locked pytest backend/tests --collect-only -q | tail -1` — the numbers must match.

- [ ] **7.1 `tests/e2e/bookwalker.spec.ts`.** The idle-after-failure poll (`expect.poll(... getCanvasOverlayState ...)
  .toEqual({ translated: false, processing: false, overlayCount: 0, calls: N })`) remains 3× after Phase 4: in
  "canvas translation failure clears processing state and can be retried", "BookWalker auto translate does not loop
  after backend timeout", and "bad translated canvas output is rejected and does not block retry". Add
  `async function expectIdleCanvas(page: Page, calls: number)` and use it in all three. Then extract shared helpers for these
  duplicated pairs (read both sides, parametrize the differences): ~1122–1149 vs ~1232–1259, ~527–539 vs ~559–571,
  ~600–609 vs ~630–639, ~1158–1167 vs ~1262–1271, ~1248–1255 vs ~1415–1422.
- [ ] **7.2 Shared e2e helpers.** These blocks are copy-pasted between specs — move one copy into
  `tests/e2e/fixtures.ts` (exported) and import it in each spec:
  bookwalker.spec ~99–121 = context-menu.spec ~67–88 = content.spec ~33–47;
  bookwalker.spec ~197–209 = context-menu.spec ~109–121; ~223–233 = ~172–182; ~301–312 = ~201–212.
- [ ] **7.3 `tests/e2e/context-menu.spec.ts`.** ~259–273 = ~387–401 (and ~456–463) → one helper.
- [ ] **7.4 `backend/tests/test_pipeline.py`.** 12 inline `class FakeDetector` / `class FakeInpainter` definitions and
  39 `TranslationPipeline(device="cpu")` setups. Add module-level `StaticDetector(blocks)` and `RecordingInpainter`
  (records `self.mask`, returns `source.copy()`, `last_backend_used = "test"`) plus
  `_pipeline_with_fakes(blocks, *, detector_key=None) -> tuple[TranslationPipeline, RecordingInpainter]` that wires
  `_detectors` (default or `optional[detector_key]`), `_ocr = FakeOcrEngine()`, `_inpainters.instance/type`, and
  `model_status["inpainter"]`. Replace an inline fake only when its body is identical to these.
  Start with `test_translation_passes_contour_safe_mask_to_inpainter` / `test_translation_uses_rectangular_mask_for_animetext_yolo`.
- [ ] **7.5 `backend/tests/test_start_script.py`.** ~129–146 = ~159–176 = ~190–203 (and ~222–229) → one helper
  method that runs the script and returns the captured output.
- [ ] **7.6 Remaining pairs** (helper or `it.each` / `subTest`): `service-worker.test.ts` ~609–621 vs ~641–653;
  `popup.test.ts` ~320–329 vs ~348–356; `wiring.spec.ts` ~339–348 vs ~378–387; `test_residual.py` ~113–121 vs
  ~178–186; `test_comfy_lanpaint.py` ~128–136 vs ~148–156. Skip any pair a previous phase already deleted.
- [ ] Phase gate + `npx playwright test`. Commit.

## Phase 8 — Final verification and report

- [ ] `npm run verify` and `npx playwright test` both green (apart from Known noise, rerun once).
- [ ] Recount LOC with both commands. Production must be **< 30,000**.
- [ ] Fill in **Results** below (per-phase LOC, test counts before/after, anything skipped and why). Commit.
- [ ] Stop. Do not push, open a PR, or merge — the owner reviews `chore/loc-reduction` first.

## Troubleshooting

- **Coverage gate fails** (`npm run test:coverage` prints which folder/metric): find a deleted unit test that covered
  code that still exists, restore it with `git show HEAD~N:<path>` (or `git restore --source=<commit> <path>`), and
  re-run. Never edit `vitest.config.ts` thresholds. If no deleted test fits, stop (rule 7).
- **`verify:repository` fails** on a missing path: a deleted file is still listed in `REQUIRED_PATHS` or
  `REQUIRED_PACKAGE_SCRIPTS` in `scripts/repository-check.ts` — remove that entry (never re-add the deleted file).
- **pyright error about `scripts/eval_pipeline.py`**: Phase 1's `include` edit was missed.
- **TS "property does not exist" on `pipelineMode` / `googleOcr`** in a test fixture: delete the property from the fixture.
- **A refactor in Phase 6 changes a test result**: the refactor isn't identical — revert that item and move on; note it
  under Results.

## Results

_(Implementing agent fills this in.)_

### Baseline
- Tests: …
- LOC: all code … / production …

### Per phase
| Phase | All-code LOC | Production LOC | Tests (unit / backend / e2e) | Notes |
|---|---|---|---|---|
| 0 | | | | |
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| 5 | | | | |
| 6 | | | | |
| 7 | | | | |
