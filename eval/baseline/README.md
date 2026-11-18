# Baselines

Four runs of `scripts/eval_pipeline.py` on `eval/corpus.json` (11 pages), made
on commit `c0e8b15088aa` (clean tree). That commit is **WP7, shape-aware
typesetting**: the rectangle fit is gone. Each render region gets a *room* -
the WP5 bubble interior eroded by an air inset that scales with the font
size, or, for free text, the largest blank rectangle the fill left around the
block - lines are laid out by a Knuth-Plass-style dynamic program over
per-line band widths, and the size is anchored to the source lettering's
measured em, harmonised to one lettering size per page, with no 16 px
ceiling. The ad-hoc overflow rescue is replaced by the free-space rooms.
Masks, the router (tiers 0-2) and the crop-LaMa/FLUX model tier are
unchanged: every mask and quality row below is byte-identical to the WP5
baselines. Each folder holds `run.json` and `summary.md`; the labels inside
still read `wp7-gate8` / `wp7-base-*` (the runs these folders were copied
from). No images are committed.

| Folder | Flags | What it measures |
| --- | --- | --- |
| `rtdetr-default` | `--inpainter flux2_klein_lanpaint` | The backend default route: RT-DETR boxes, contour-safe masks, router tiers, model fallback. |
| `rtdetr-lama` | `--inpainter lama_large` | Same masks; blocks that fail every tier go to crop LaMa. |
| `rect-masks-default` | `--mask-mode rectangular --inpainter flux2_klein_lanpaint` | Same as `rtdetr-default` (see "rect-masks-default is still the contour route" below). |
| `rect-masks-lama` | `--mask-mode rectangular --inpainter lama_large` | Real rectangular masks on RT-DETR boxes, router tiers, LaMa for the rest. |

To compare a change against one of them:

```powershell
uv run --project backend --locked python scripts/eval_pipeline.py --out eval/runs/my-change --inpainter lama_large
```

```powershell
uv run --project backend --locked python scripts/eval_pipeline.py --compare eval/baseline/rtdetr-lama eval/runs/my-change
```

## How they were made

- **Machine.** Windows 11, Intel Core i5-13420H (12 threads), 16 GB RAM, CPU
  only (onnxruntime `CPUExecutionProvider`). Models came from the main
  checkout's `backend/models_cache` through `MANGA_BACKEND_MODELS_DIR`.
- **Pipeline settings.** OCR `manga-ocr`, stub translator, Step 6 on, one
  untimed warm-up page, and the `POST /translate` render defaults.
- **Pages.** All 11 pages: 5 original synthetic, 3 adversarial synthetic and
  the 3 private pages in `eval/pages/`. Synthetic lettering used MS Gothic.
- **Clean trees.** Each run went to `eval/runs/base-<route>` with a clean
  tree, then the artifacts were copied here, so every `run.json` records
  `git_commit 0c09aec693d5` and `git_dirty false`.
- **Inpainter: LaMa stands in for FLUX.** ComfyUI was not running, so the
  3 model-queued blocks on the FLUX route fell back to `lama_large`
  (`fallback_pages` 2; the other 9 pages needed no model at all). Read the
  `*-default` runs as "FLUX route with LaMa fill".
- **Detector: RT-DETR only.** The AnimeText YOLO weights are gated and not on
  disk; `--mask-mode rectangular` emulates that mask path on RT-DETR boxes.

## Headline numbers (`rtdetr-default`)

| Metric | WP5 | WP7 | Change |
| --- | --- | --- | --- |
| ink-mask recall | 98.1% | 98.1% | = |
| residual ink in mask (fill failure) | 0.5% | 0.5% | = |
| residual ink out of mask (mask miss) | 1.4% | 1.4% | = |
| residual ink | 1.8% | 1.8% | = |
| text-region MAE | 1.36 | 1.36 | = |
| tone flattened in text boxes (px) | 684 | 684 | = |
| over-erased px | 364 | 364 | = |
| masked px | 169,214 | 169,214 | = |
| blocks queued to the model | 3 | 3 | = |
| shrink events (vs the anchored target) | 32 | 27 | -5 |
| below min readable size | 6 | 6 | = (different regions) |
| overflow events | 4 | 0 | -4 |
| rendered ink outside the bubble interior (px) | 0 | 0 | = |
| font-size spread per page (mean) | 2.546 | 2.182 | -0.364 |
| dialogue font-size spread per page (mean) | n/a | 1.800 | new row |
| unfit regions skipped | 0 | 0 | = |
| render stage per run | 2.19 s | 4.17 s | +1.98 s (~0.18 s/page) |
| seconds per page | 5.39 | 5.09 | machine-dependent |

### The render metrics after WP7

- **`shrink_events` is redefined** relative to the *anchored target* (the
  page's lettering size, derived from the source glyphs) instead of the old
  fixed 16 px preference. With the ceiling gone, "shrank" has to mean "could
  not be set at the size the source asks for", not "is smaller than 16".
  The harness records each region's anchored target and the old comparison
  would have read 20/85 below 16; the new definition reads 27/85 below their
  target. The two numbers are not comparable, which is why the row's label
  changed.
- **`overflow_events`** counted regions where the old ad-hoc rescue (grow the
  box by up to 72x48 px) was needed and found. That rescue no longer exists:
  free text gets a free-space room instead, so the row is structurally 0. It
  would only fire if a fitted layout did not fit its room at all.
- **`dialogue font-size spread`** is a new additive row: the existing
  `font_size_spread` is the per-page spread over *all* regions, which mixes
  free text (lettered at the page size too) with dialogue.
- **`rendered ink outside the bubble interior`** was already 0 in the WP5
  baseline and stays 0: the rooms are built from the interior itself, so the
  WP1 metric cannot go lower. The honest measure of the change is that a
  rectangle no longer overstates the capacity of an oval bubble.

Per tag (`rtdetr-default`, residual ink / residual in mask / residual out of
mask / text MAE):

| Tag | Residual | In mask | Out of mask | MAE |
| --- | --- | --- | --- | --- |
| adversarial | 4.5% | 1.3% | 3.2% | 2.83 |
| conjoined-bubbles | 0.0% | 0.0% | 0.0% | 0.09 |
| horizontal-text | 2.7% | 0.8% | 1.9% | 1.97 |
| light-on-dark | 0.1% | 0.0% | 0.1% | 1.27 |
| narration | 1.3% | 1.1% | 0.2% | 1.35 |
| sfx | 1.9% | 1.1% | 0.7% | 1.13 |
| textured-background | 1.1% | 0.8% | 0.4% | 1.08 |
| vertical-text | 2.3% | 0.7% | 1.6% | 1.66 |
| webtoon-strip | 0.0% | 0.0% | 0.0% | 0.10 |
| white-bubble | 2.2% | 0.3% | 1.9% | 1.47 |

The whole `white-bubble` residual is the adversarial `syn-text-touching-outline`
page (9.4% out of mask); every other white-bubble page is at 0.0%.

Per synthetic page (`rtdetr-default`):

| Page | Recall | Out of mask | In mask | Hole in tone | Over-erased |
| --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | 100.0% | 0.0% | 0.0% | 0 | 0 |
| syn-conjoined-bubbles | 100.0% | 0.0% | 0.0% | 0 | 0 |
| syn-textured-narration | 100.0% | 0.0% | 0.0% | 0 | 0 |
| syn-sfx | 95.9% | 1.2% | 0.0% | 0 | 0 |
| syn-webtoon-strip | 100.0% | 0.0% | 0.0% | 0 | 0 |
| syn-tone-rotated | 99.0% | 0.3% | 2.2% | 24 | 0 |
| syn-faint-lettering | 100.0% | 0.0% | 0.8% | 0 | 0 |
| syn-text-touching-outline | 89.5% | 9.4% | 0.8% | 660 | 364 |

## What the masks are now (87 blocks to erase) - unchanged by WP7

| | WP4 | WP5 |
| --- | --- | --- |
| `bubble-clipped-glyph` | 22 | 30 |
| `contour-clipped-glyph` | 2 | 0 |
| `glyph` (no interior clip) | 61 | 55 |
| reason `no_contour` | 39 | 0 |
| reason `flood_leak` (open/borderless bubble) | n/a | 35 |
| reason `text_free` (no bubble semantics) | 22 | 20 |

Every block that used to end with `no_contour` now names the flood's own
diagnosis, and four more blocks per run are clipped to a verified bubble
interior (the four white-bubble blocks under RT-DETR, which used to fall back
to the unclipped glyph mask; the two faint-lettering blocks that only the
contour fallback could serve also moved to the flood). The interiors are
found by a seeded flood fill, so a block with lettering touching the outline
no longer loses its interior to the edge rule - it now leaks and falls back,
which is what `flood_leak` reports.

## What the router chose

| Tiers | `rtdetr-default` | `rtdetr-lama` | `rect-masks-default` | `rect-masks-lama` |
| --- | --- | --- | --- | --- |
| flat | 44 | 44 | 44 | 16 |
| smooth | 1 | 1 | 1 | 1 |
| lattice | 37 | 37 | 37 | 42 |
| model | 3 | 3 | 3 | 26 |
| Blocks queued to the model | 3 of 87 | 3 of 87 | 3 of 87 | 26 of 87 |

Tier shifts against WP4: flat 39 -> 44, lattice 37 -> 37, model 8 -> 3. The
moves are on `syn-white-bubbles` (its last model block is now flat-filled
because the flood verifies an interior and the clip keeps the outline out of
the mask) and on the two BookWalker spreads (four and one fewer model
escalations; five and four fewer lattice blocks, replaced by flat). Those
masks no longer reach the sampled ring, so a flat fill verifies where an
outline-contaminated mask used to force the lattice or the model. Screentone
and hatching still go to the lattice tier; the dark gradient still goes to
smooth.

## What the typesetter does now (WP7)

The four files are new and live in `backend/src/manga_backend/pipeline/`:
`rooms.py` (rooms, air inset, conjoined split, bands), `linebreak.py`
(Knuth-Plass-style DP), `fit.py` (anchored size search) and `measure.py`
(source glyph size, ink/paper/outline colours). `rendering.py` drives them;
`layout.py` lost the overflow rescue.

- **Rooms.** A dialogue region's room is the WP5 bubble interior, its holes
  filled, eroded by `max(2, 0.18 * font_size)` px of air, with its body
  centre (the tail-robust one from `bubbles.py`) as the axis. When the flood
  found no interior for a bubble, neighbouring unresolved bubbles are joined
  into one window, flooded together and split per text group by a hull-defect
  chord (seeded geodesic watershed as the fallback); 0 of 11 pages now get a
  split lobe that the individual flood missed, so the conjoined-bubbles
  improvement the plan hoped for did not materialise - the shared outline is
  still outside the detector's box on `syn-conjoined-bubbles` (the union
  window does contain it, but the flood then covers both lobes and the split
  is rejected by the lobe-balance guard). Free text gets the largest blank
  rectangle around the block centre (low-gradient pixels = what a flat or
  smooth fill leaves), clipped to the growth cap, to the other render boxes
  and to rooms already claimed.
- **Bands and breaking.** For a size and a line count, n bands are centred on
  the room's body axis; each band's usable width is the narrowest room row
  inside it, shifted towards the axis. The DP minimises squared slack
  (normalised, with a 4% tolerance) plus a per-line cost and a hyphen
  penalty, in `even` mode (every line pays) or `ragged` (the last line is
  nearly free). A word that cannot fit whole is split at a
  consonant-consonant boundary and carries a real hyphen; the consecutive
  hyphenated-line cap of 2 yields when it is the only thing that makes a
  readable size infeasible. No hyphenation dictionary (D7).
- **Sizes.** The source lettering's em is measured *while masking* on the
  original page (projection-profile advance pitch in the reading direction,
  corroborated by the glyph component boxes) and stored on
  `TextBlock.source_font_size`; `render_text` only ever sees the filled page.
  The page is lettered at the median of those anchors (one type scale per
  page); the fitter searches downwards from it coarse-then-bisect, tries
  tighter leading (0.9, 0.82) before it accepts a size below the readable
  floor, and never shrinks for a word it can hyphenate instead.
- **Colours.** Paper is the median of the room's own pixels, the outline the
  darkest quartile of a 4 px ring outside it. Dark paper switches the block
  to light lettering with a dark stroke and a paper-coloured background box
  (so light text never lands on the user's white box); anything else keeps
  the user's colours.

## Findings and caveats

- **H1 (SFX white outline) is closed.** `syn-sfx` recall 50.6% -> 95.9% and
  out-of-mask residual 10.1% -> 1.2%. The outline is found by growing outline
  bands outward while the band keeps one level and either differs from the
  local background or carries none of its texture (a white ring over
  screentone has the paper's own level and only the *absence* of dots marks
  it). `sfx` residual 17.9% (WP1) -> 12.2% (WP4) -> 1.9%.
- **H4 (faint lettering) is closed.** `syn-faint-lettering` 15.7% -> 0.8%;
  the 185-level lettering used to be invisible to a dark-only threshold.
- **H5 (bubble interiors).** `white-bubble` blocks with a verified interior:
  1 of 4 under RT-DETR (WP4) -> 4 of 4. `contour-clipped-glyph` is gone: the
  two faint-lettering blocks that needed the contour fallback now get a flood
  interior. `syn-conjoined-bubbles` still gets no interior on either side of
  the change: the detector bubble box is one lobe, the ROI cuts the shared
  outline, and the flood therefore leaks (the plan's conjoined split is out of
  scope here).
- **`syn-text-touching-outline` is better but not solved.** 21.8% -> 9.4%
  out-of-mask, 10.2% residual, 660 hole-in-tone and 364 over-erased px. The
  lettering that crosses the wall is now mostly masked (the merged
  lettering+outline component survives the stroke-width rules) but the
  detector box is wider than the bubble, so the parts outside the interior
  are clipped away and the outline the lettering covers cannot be
  reconstructed by any cheap tier. Reported as a follow-up, not a gate.
- **Tone dots leave the mask, but only where they are dot-shaped.** A dot
  that touches a glyph merges into its component; it is removed by its
  little plateau at the tone level (a cross erosion passes a dot and kills a
  glyph's one-pixel antialiased ramp), which is what keeps `hole_in_tone`
  at 684 px instead of the 6,179 px it reached before that step.
- **`px touched away from text` rose 1,035 -> 4,068**, all of it on
  `syn-textured-narration` and all of it *below* the 40-level visibility
  bound (`hole_in_tone` and `over_erased` both improved). It is a
  sub-visible lattice phase difference: the tighter mask changes which
  band the lattice tier verifies on, and the copy phase shifts by a pixel on
  some dot edges. Records as noise for the "any change at all" row only.
- **rect-masks-default is still identical to rtdetr-default.** Redefining
  rectangular mask semantics remains out of scope (WP4 note, unchanged).
- **Timings are machine-bound.** These baselines read 5.09 s/page on the
  default route in a quiet window. The one stage WP7 made slower is render:
  2.19 -> 4.17 s per 11-page run (+1.98 s, ~+0.18 s/page), because each
  region now builds a room, runs the DP over several sizes and measures its
  colours. Total per-page time did not move (5.39 -> 5.09 s/page, within
  noise), so the D3 budget holds.
- **Quality rows are deterministic.** The three contour routes are
  byte-identical on every quality row, and a rerun of `rtdetr-default`
  reproduced them exactly (only timings move). The Lab k-means init is
  quantile-based rather than OpenCV's RNG exactly so this stays true.
- **The typesetting rows (WP7).** `shrink_events` 32 -> 27 (redefined, see
  above), `overflow_events` 4 -> 0, `font_size_spread` 2.546 -> 2.182,
  dialogue spread 1.8, `below_min_readable` 6 -> 6, `unfit_skipped` 0. The
  six below-minimum regions are 24-38 px-wide source boxes on the two
  BookWalker spreads whose English needs more lines than a 10 px setting can
  hold (7 lines of 10 px in a 69 px-tall room is impossible without negative
  leading); the WP5 baseline reached 10-16 px on some of them only by
  lettering across the bubble outline or growing the box into the art, which
  is exactly the mechanism this WP removes. They are a translation-length
  (2.4) and detector-box problem, not a typesetting one.
- **Shape-aware fitting is smaller, not bigger.** In an oval bubble the
  interior's top and bottom bands are narrower than the box, so a region that
  the old rectangle lettered at 16 px now fits at 11-13 px. That is the
  honest caveat of research 2.6 and it is what the redefined shrink metric
  measures: 20 of 85 regions cannot be set at their anchored target (27 under
  the old "< 16" definition, which is not comparable).
- **Real pages.** No truth ink exists for them, so only the mask/count rows
  are meaningful; the mask px are unchanged from WP5
  (`real-single-page-01` 25,728, `real-bookwalker-spread-01` 20,837,
  `real-bookwalker-spread-02` 23,732). CJK residue (Step 6) *improved* as a
  side effect of the larger lettering: 19.1% -> 14.6% overall (kana/kanji
  19.5% -> 14.9%). Step 6 is WP3's path and was not touched; the row moves
  because it re-OCRs the rendered page and bigger, cleaner Latin reads back
  as Latin more often. Treat it as a bonus, not a WP7 claim.

## Follow-ups

- **The conjoined split never fires.** The union window does contain the
  shared outline, but the flood then covers both lobes and the lobe-balance
  guard rejects the hull-defect chord; the geodesic split on the union
  interior gives unbalanced lobes. A real distance-transform watershed (or a
  notch seeded from the detector boxes) is still needed before
  `syn-conjoined-bubbles` gets a split room. Mask-side, `flood_leak` is
  unchanged at 35.
- **Below-minimum regions are a translation-length problem.** Six regions
  (24-38 px source boxes on the BookWalker spreads) cannot hold their English
  at 10 px. A length budget in 2.4, or a detector that returns the balloon
  rather than a strip of it, would fix them; the typesetter cannot.
- **Hyphenation is character-level.** Breaks prefer a consonant-consonant
  boundary, but "rig-ht" style splits still happen in very narrow bands. A
  syllable dictionary would need a package (D7 forbids it); a small built-in
  pattern table would be the follow-up.
- **Free-space rooms use a growth cap, not panel detection.** The room is
  limited to 1.6 box widths / 0.9 box heights around the block rather than to
  the detected panel, so a block in a wide blank gutter cannot reach the
  whole panel.
- Lettering that crosses a bubble wall: needs an interior that follows the
  wall through the lettering (or a model tier that reconstructs the outline).
- Lattice phase slips: a subpixel period refinement would remove the
  remaining sub-visible "touched" pixels (and the 163 -> 24 hole-in-tone px
  on `syn-tone-rotated`).
- Rectangular mask semantics are still undefined (rect routes equal the
  contour routes except for `rect-masks-lama`).

## The WP6a merge line (2026-11-18)

The WP6a reading-order and bubble-level-unit work (`wp6a/reading-order`
`6f8b033`, based on the pre-WP4 `6e37fca`) was merged into this line by the
Team Lead at `c3f8679` (manual resolution of `layout.py` and
`orchestrator.py`): the reading-order sort, the translation-unit grouping,
`resolve_reading_direction` and the reading-order agreement script are now
on the WP7 typesetting line; `layout.py` keeps the merge's new machinery
but drops the `FitLayout`/`FitBox` aliases whose overflow rescue WP7 removed
(free-space rooms replace it). The merged suite is 539 tests + 198
subtests, all green, and `rect-masks-default` remains identical to
`rtdetr-default`.

These four baselines were regenerated at the merge line (11 pages, clean
trees, quiet window ~2-13% load). **Every deterministic quality and render
row is identical to the WP7 baselines** (residual 1.8%, in-mask 0.5%,
out-of-mask 1.4%, recall 98.0%, MAE 1.358, hole-in-tone 684, over-erase 364,
masked px 169,214, model blocks 3, tiers 44/1/37/3, shrink 27, below-min 6,
overflow 0, spread 2.182 / dialogue 1.8, px outside mask 0). The only mover
is the CJK re-OCR row: 14.6% -> 16.9% (kana/kanji 14.9% -> 17.2%), entirely
one real page (`real-bookwalker-spread-02`, 1 -> 3 flagged blocks).
Inferred mechanism (not image-verified): reading-order region claiming
shifts free-room lettering positions on the real pages, and the Step-6
manga-ocr re-read is position/background-sensitive (the documented noise
floor). That row is a diagnostic on pages with no truth; production Step 6
itself is WP3's to remove on its own branch.

Stamp disclosure: the three promotion commits are docs-only, so the code
state is identical across all four baselines — `rtdetr-default` records the
merge commit `c3f8679` (it IS the clean identity run), `rtdetr-lama`
records `f365082`, and the two rect routes record `0039533`. Labels inside
the run files still read `merge-identity` / `base-<route>`.

## WP6b (page context, length budgets, concurrent erasing): baselines unchanged

`wp6b/page-context` (`eaca86a` + `719d456`, based on the WP6a merge head
`77ef4ad`) makes the translation step multimodal and length-aware: a
vision-capable provider gets the downscaled page with a reading-order number
drawn at each translation unit, every unit gets a character budget read from
its WP7 room, a unit that cannot be set at the minimum readable size gets one
re-ask for a shorter version, and masking/inpainting now start with the
translation instead of after it.

All four routes were re-run on the branch (11 pages, clean tree, commit
`719d456`) and compared against these baselines:

| Route | Quality and render rows | Pages with a different rendered image |
| --- | --- | --- |
| `rtdetr-default` | all identical | 0 |
| `rtdetr-lama` | all identical | 0 |
| `rect-masks-default` | all identical | 0 |
| `rect-masks-lama` | all identical | 0 |

Masked px (169,214 / 914,494 on the rect-LaMa route), the tier counts, the mask
reasons, the render region count, shrink / below-min / overflow / unfit, the
font spreads, the CJK re-OCR rows and px-outside-mask are byte-identical on
every route, and every page reports `ok` rather than `ok (image differs)` — the
rendered images match too. **The baselines are therefore not regenerated:
nothing moved, so they still measure this line.** `--translator` stays the stub
for all four, and the stub bypasses the provider, so a translator-side change
cannot move a row; the concurrent erase was built to keep the erase set
identical on the stub, which these runs confirm.

The runs add one **additive** section to `run.json` (schema version 3):
`translate`, with `budget_chars` (one character budget per unit, in reading
order), `budget_chars_min/max/mean`, `page_image`, `requests`, `reask_units`,
`reask_shortened` and `reask_failed`. On the stub the budgets are real and the
provider counts are zero (the stub never enters the translation step). Across
the 11 pages: 89 units budgets, mean 31.9 characters, min 6. The `compare`
table gains six rows for these numbers; they read `n/a` on these baselines
because the baselines predate the section. Nothing else in the metric
definitions changed.

Timing on the stub grew slightly (rtdetr-default 5.107 -> 5.193 s/page, mask
3.634 -> 4.049 s): the page context (rooms, font metrics, one flood per bubble)
is on the critical path because the stub provider is instant. With a real
provider it runs under the provider latency. `rect-masks-default` timings from
the branch runs are unusable — one run hit a ~7 h machine suspend and the
reruns ran at 30-54% ambient load; its quality rows were identical in all
three.

**Real-translator rows: blocked-on-key.** The D5 recipe (`LOCAL_LLM_API_KEY`
from the main checkout's `.env`, `LOCAL_LLM_BASE_URL=https://opencode.ai/...`)
produced the backend's sanitised `TranslatorError`, and a status-only probe
against both candidate endpoints returned HTTP 403 for the configured model.
`below_min_readable` / `overflow_events` on a real provider and the
concurrency saving are therefore unmeasured; the budgets and the one re-ask are
unit-tested and the per-unit numbers are recorded. A refreshed key makes
`--translator local` runs work with no code change.
