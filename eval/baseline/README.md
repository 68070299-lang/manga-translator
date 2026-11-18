# Baselines

Four runs of `scripts/eval_pipeline.py` on `eval/corpus.json` (11 pages), made
on commit `0c09aec693d5` (clean tree). That commit is **WP5, the
ink-structure masks and seeded flood-fill bubble interiors**: the glyph mask
is now built by clustering the crop's Lab colours against a paper estimate,
classifying components by distance-transform stroke width (line art, periodic
tone dots, mid-tone structure) and growing outline rings and unmixed halos;
the bubble interior is a seeded flood fill over paper with leak rejection,
falling back to the old smallest-closed-contour search. The router
(tiers 0-2) and the crop-LaMa/FLUX model tier are unchanged. Each folder holds
`run.json` and `summary.md`. No images are committed.

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
