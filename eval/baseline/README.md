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
