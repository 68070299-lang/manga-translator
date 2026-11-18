# Baselines

Four runs of `scripts/eval_pipeline.py` on `eval/corpus.json` (11 pages), made
on commit `517c4c47c56b` (clean tree). That commit is **WP3, the pre-render
residual check**: after a cheap fill tier paints a block, the WP5 ink extractor
runs again on the inpainted crop and flags letter-shaped residue, with one
bounded escalation (grow the mask, move to the next tier). Production no
longer runs the Step 6 manga-ocr re-read of the rendered page; the harness
runs the same re-read itself after the page timer, so the CJK rows stay
comparable. Every synthetic quality row is byte-identical to the WP5
baselines (commit `0c09aec693d5`); the moved rows are timing, the two
real-page ghost-halftone escalations and the CJK row's one real page.

The WP5 method behind the masks is unchanged: the glyph mask is built by
clustering the crop's Lab colours against a paper estimate, classifying
components by distance-transform stroke width (line art, periodic tone dots,
mid-tone structure) and growing outline rings and unmixed halos; the bubble
interior is a seeded flood fill over paper with leak rejection, falling back
to the old smallest-closed-contour search. The router tiers 0-2 and the
crop-LaMa/FLUX model tier are unchanged apart from the residual check's
single escalation. Each folder holds `run.json` and `summary.md`. No images
are committed.

Source runs (copied here after the gate runs): `wp3-gate-default-b`
(`rtdetr-default`), `wp3-gate-lama` (`rtdetr-lama`), `wp3-base-rect-default`
(`rect-masks-default`), `wp3-base-rect-lama` (`rect-masks-lama`).

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
- **Pipeline settings.** OCR `manga-ocr`, stub translator, Step 6 **off the
  production path** (WP3): the harness re-reads the rendered page itself,
  after the page timer, so `timings.verify` is 0 and the re-OCR cost is
  reported separately as `timings.verify_harness`; the CJK rows are computed
  from that same pass. One untimed warm-up page and the `POST /translate`
  render defaults.
- **Pages.** All 11 pages: 5 original synthetic, 3 adversarial synthetic and
  the 3 private pages in `eval/pages/`. Synthetic lettering used MS Gothic.
- **Clean trees.** Each run went to `eval/runs/<name>` with a clean tree
  (the source run names are listed above), then the artifacts were copied
  here, so every `run.json` records `git_commit 517c4c47c56b` and
  `git_dirty false`.
- **Inpainter: LaMa stands in for FLUX.** ComfyUI was not running, so the
  5 model-queued blocks on the FLUX route fell back to `lama_large`
  (`fallback_pages` 3; the other 8 pages needed no model at all). Read the
  `*-default` runs as "FLUX route with LaMa fill". Two of those five blocks
  are the WP3 escalations on real pages (see below).
- **Detector: RT-DETR only.** The AnimeText YOLO weights are gated and not on
  disk; `--mask-mode rectangular` emulates that mask path on RT-DETR boxes.

## Headline numbers (`rtdetr-default`)

The WP4 -> WP5 change, recorded as it was measured. Every quality row in this
table is identical at WP3 (see the next table for what moved); the WP3
numbers are in each folder's `run.json`.

| Metric | WP4 | WP5 | Change |
| --- | --- | --- | --- |
| ink-mask recall | 84.8% | 98.1% | +13.3 pt |
| residual ink in mask (fill failure) | 1.3% | 0.5% | -0.8 pt |
| residual ink out of mask (mask miss) | 6.5% | 1.4% | -5.1 pt |
| residual ink | 7.7% | 1.8% | -5.9 pt |
| text-region MAE | 5.04 | 1.36 | -3.68 |
| tone flattened in text boxes (px) | 1305 | 684 | -621 |
| over-erased px | 908 | 364 | -544 |
| masked px | 194,315 | 169,214 | -25,101 (-13%) |
| blocks queued to the model | 8 | 3 | -5 |
| mask stage per run | 2.21 s | 4.17 s | +1.96 s (~0.18 s/page) |
| seconds per page | 8.40 | 5.39 | machine-dependent |

Per tag (`rtdetr-default`, residual ink / residual in mask / residual out of
mask / text MAE) — identical at WP3:

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

Per synthetic page (`rtdetr-default`) — identical at WP3:

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

## WP3: what moved, and the pre-render flag

| Metric (`rtdetr-default`) | WP5 | WP3 | |
| --- | --- | --- | --- |
| seconds per page | 5.387 | 3.584 | -1.80 (production verify removed) |
| verify stage per run | 23.60 s | 0 | production no longer re-reads |
| harness re-OCR per run | (in verify) | 25.06 s | measurement only, outside the page timer |
| mask stage per run | 4.17 s | 5.67 s | +1.50 s (~0.14 s/page: the re-extraction) |
| blocks queued to the model | 3 | 5 | +2: two real-page ghost-halftone escalations |
| masked px | 169,214 | 169,217 | +3 |
| CJK residue, kana/kanji | 19.5% | 17.2% | -2.3 pt: one real page, see below |
| every other quality row | | identical | = |

The pre-render flag, on the 8 synthetic pages (the only ones with truth):

| Flag row | Value |
| --- | --- |
| agreement with truth residue | 64% (25 routed blocks, 9 truth-positive) |
| agreement, fill failure only | 80% (5 in-mask truth blocks) |
| blocks flagged | 0 |
| false positives / false negatives | 0 / 9 |
| blocks escalated / second flags | 0 / 0 |

Both real-page flags are genuine (inspected at 4x): lattice fills that copied
halftone dots tracing the erased glyphs, on `real-single-page-01`
[417,388,528,492] and `real-bookwalker-spread-01` [24,444,71,510]. They
escalate lattice -> model, the model repaints the same mask and the ghost
dots go. The 9 false negatives are all residue a re-extraction cannot see:
SFX outline strokes the extractor never masked, lettering outside the
detector box (`syn-text-touching-outline`), and in-mask fill noise (lattice
dot-phase slips, the missing bubble wall under erased lettering).

## What the masks are now (87 blocks to erase)

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

| Tiers (WP3) | `rtdetr-default` | `rtdetr-lama` | `rect-masks-default` | `rect-masks-lama` |
| --- | --- | --- | --- | --- |
| flat | 44 | 44 | 44 | 16 |
| smooth | 1 | 1 | 1 | 1 |
| lattice | 35 | 35 | 35 | 41 |
| model | 5 | 5 | 5 | 27 |
| Blocks queued to the model | 5 of 87 | 5 of 87 | 5 of 87 | 27 of 87 |

WP3 shifts against WP5: lattice 37 -> 35 and model 3 -> 5 on the contour
routes (the two real-page ghost flags above); `rect-masks-lama` 42 -> 41
lattice, 26 -> 27 model (one real-page flag, 156 px of mask growth from the
alpha band in the rectangular route). WP4 -> WP5 shifts, unchanged: flat
39 -> 44, lattice 37 -> 37, model 8 -> 3. The
moves are on `syn-white-bubbles` (its last model block is now flat-filled
because the flood verifies an interior and the clip keeps the outline out of
the mask) and on the two BookWalker spreads (four and one fewer model
escalations; five and four fewer lattice blocks, replaced by flat). Those
masks no longer reach the sampled ring, so a flat fill verifies where an
outline-contaminated mask used to force the lattice or the model. Screentone
and hatching still go to the lattice tier; the dark gradient still goes to
smooth.

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
- **Timings are machine-bound.** The WP4 baselines were made in a degraded
  window (8.40 s/page); the same work now reads 5.39 s/page, so only the
  mask-stage delta is meaningful: 2.21 -> 4.17 s per 11-page run, about
  +0.18 s/page, inside the D3 budget (default route 5.39 s/page now).
- **Quality rows are deterministic.** The three contour routes are
  byte-identical on every quality row, and a rerun of `rtdetr-default`
  reproduced them exactly (only timings move). The Lab k-means init is
  quantile-based rather than OpenCV's RNG exactly so this stays true.
- **Real pages.** No truth ink exists for them, so only the mask/count rows
  are meaningful: mask px `real-single-page-01` 21,022 -> 25,728,
  `real-bookwalker-spread-01` 22,433 -> 20,837, `real-bookwalker-spread-02`
  29,677 -> 23,732. CJK residue (Step 6) moved 21.3% -> 19.1% overall
  (kana/kanji 21.8% -> 19.5%); one real spread got one block worse. Step 6
  is WP3's path and was not touched.

## Findings added at WP3

- **Step 6 is gone from the production path.** It cost 23.6-32.7 s per
  11-page run (2.1-3.0 s/page here) and could not repair anything. The
  harness re-reads the rendered page for measurement only, after the page
  timer; that is why `timings.verify` is 0 and `timings.verify_harness` is a
  new row.
- **What the CJK row means now.** It is the same manga-ocr re-read of the
  same rendered page, so it stays comparable, but it no longer costs
  production time. It moved 19.1% -> 16.9% (kana 19.5% -> 17.2%) because one
  real page changed: `real-single-page-01` 5 -> 3 flagged blocks after its
  two escalated model fills changed the background under the rendered text.
  Every synthetic page's count is identical. This is the known noise floor
  (manga-ocr reads short rendered English as kana, and its reading is
  background-sensitive); production no longer pays for it.
- **The pre-render flag is shape-based, not pixel-overlap-based.** The
  literal reading (post-fill ink landing on the original mask) was measured
  and rejected: a *perfect* lattice fill of texture is re-detected as ink and
  escalating it turned `syn-sfx` residual from 1.2% to 21.5% and tone holes
  from 0 to 2,610 px. Matching component shapes (IoU >= 0.5, with
  background-sized, two-edge-touching and strongly elongated reference
  components excluded as structure) separated texture (max IoU 0.47 on a
  perfect hatch fill) from letters (1.0) and is silent on every synthetic
  cheap fill.
- **`sfx` (1.9%) and `textured-background` (1.1%) are saturated.** Their
  residue is mask miss the extractor shares (SFX outline strokes are in no
  mask the extractor built) and in-mask lattice phase noise; the check can
  see neither, so escalation cannot lower them. A detector re-run on the
  inpainted page (research 2.7 point 4, ~1.5 s/page) is the principled next
  step, deliberately skipped here.
- **`syn-text-touching-outline` (10.2%) is not helped.** Its miss is
  lettering outside the detector box (the box is narrower than the text), so
  growing a mask inside the box cannot reach it; a grown exposure window
  reintroduces panel and bubble-wall structure as false positives (measured).
- **Conjoined bubbles are not served by the escalation** (0 of the 35
  `flood_leak` blocks): their fills pass the residual check, so no flag
  fires.

## Follow-ups

- Lettering that crosses a bubble wall: needs an interior that follows the
  wall through the lettering (or a model tier that reconstructs the outline).
- Conjoined bubbles: the flood needs the joined outline inside the ROI, or
  the plan's notch/watershed split.
- Lattice phase slips: a subpixel period refinement would remove the
  remaining sub-visible "touched" pixels (and the 163 -> 24 hole-in-tone px
  on `syn-tone-rotated`).
- Rectangular mask semantics are still undefined (rect routes equal the
  contour routes except for `rect-masks-lama`).
- An independent detector re-run on the inpainted page would give the
  residual check a signal the ink extractor does not share (research 2.7
  point 4); it costs about 1.5 s/page and is the next step for the mask-miss
  cases the current flag cannot see.
