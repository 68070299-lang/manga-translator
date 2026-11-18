# Baselines

Four runs of `scripts/eval_pipeline.py` on `eval/corpus.json` (11 pages), made
on commit `9d179d644643` (clean tree). That commit is the WP1 harness upgrade on
the harness branch: it adds the diagnostic metrics (`ink_mask_recall`,
`residual_in_mask` / `residual_out_of_mask`, residual after low-pass,
hole-in-tone, OCR CER, typesetting geometry, mask reason codes, oracle modes)
and the three adversarial synthetic pages. It is observational: every metric
that existed before is unchanged on the old 8 pages (see "Identity check"
below). Each folder holds `run.json` and `summary.md`. No images are committed.
The run label inside each folder still reads `wp1-identity-*`: the promoted
baselines are the identity-check runs themselves, not a rerun.

| Folder | Flags | What it measures |
| --- | --- | --- |
| `rtdetr-default` | `--inpainter flux2_klein_lanpaint` | The backend default route: RT-DETR boxes, contour-safe masks, white-fill fast path, then FLUX. |
| `rtdetr-lama` | `--inpainter lama_large` | LaMa chosen directly. No white-fill fast path. |
| `rect-masks-default` | `--mask-mode rectangular --inpainter flux2_klein_lanpaint` | The AnimeText mask path on RT-DETR boxes, default inpainter. |
| `rect-masks-lama` | `--mask-mode rectangular --inpainter lama_large` | The AnimeText mask path with LaMa. |

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
  checkout's `backend/models_cache` through `MANGA_BACKEND_MODELS_DIR`. The
  desktop was in active use during the day (browser, editor): each timed run
  was started only after two load readings under 20, 30 s apart, with no other
  `eval_pipeline` or `pytest` process alive, and the two plain-CPU timings
  (`render`, `mask`) came out within ±20 % of the other routes.
- **Pipeline settings.** OCR `manga-ocr`, stub translator, Step 6 on, one
  untimed warm-up page, and the `POST /translate` render defaults.
- **Pages.** All 11 pages were scored: 5 original synthetic, 3 adversarial
  synthetic (`syn-tone-rotated`, `syn-faint-lettering`,
  `syn-text-touching-outline`) and the 3 private pages in `eval/pages/`.
  Synthetic lettering used MS Gothic (`msgothic.ttc`).
- **Inpainter: LaMa stands in for FLUX.** ComfyUI was not running at
  `127.0.0.1:8188`, so FLUX (LanPaint) never ran. Every page that needed a
  model on the FLUX route fell back to `lama_large`; that is the 5 fallback
  pages. The other 6 pages report `flux2_klein_lanpaint` only because the
  white-fill fast path covered every block, so no model was called — including
  all three adversarial pages, whose rotated screentone still passes the
  white test. Read the `*-default` runs as "FLUX route with LaMa fill".
  Rerun them with ComfyUI up to measure FLUX itself.
- **Detector: RT-DETR only.** The AnimeText YOLO weights are not on disk, and
  their Hugging Face repository is gated. No token was used. The `rect-masks`
  runs emulate the AnimeText mask path with `--mask-mode rectangular` on
  RT-DETR boxes. They measure that mask path, not AnimeText detection.

## Identity check

Before promoting these runs, each route was compared against the previous
baselines (commit `c1af6c2`, 8 pages) over the pages scored in both. Every
metric that existed then is identical on the old 8 pages: blocks 79 / to erase
77, Step 6 verdict (13.9 / 15.2 / 12.7 / 11.4 %), kana-kanji residue, px
outside mask 0, mask kinds, white-fill blocks 68, masked px (124,721 / 124,721
/ 181,216 / 718,173), render regions 75, shrink 32, below-10 px 6, overflow 4,
detection recall 100 %, residual ink (6.4 / 6.5 / 6.4 / 11.3 %), text MAE
(7.97 / 8.09 / 7.97 / 13.44), over-erased px, px touched away from text. Only
the new metrics and the three new pages differ. The harness change is
observational, as intended.

## Headline numbers

Times are wall-clock totals over the 11 pages, except seconds per page.

| Metric | `rtdetr-default` | `rtdetr-lama` | `rect-masks-default` | `rect-masks-lama` |
| --- | --- | --- | --- | --- |
| Inpainter used (pages) | white fill only x6, lama_large x5 (fallback) | lama_large x11 | white fill only x6, lama_large x5 (fallback) | lama_large x11 |
| Blocks detected / to erase | 89 / 87 | 89 / 87 | 89 / 87 | 89 / 87 |
| CJK residue, Step 6 verdict | 14.6% | 15.7% | 13.5% | 12.4% |
| CJK residue, kana/kanji only | 14.9% | 16.1% | 13.8% | 12.6% |
| Px changed outside mask | 0 | 0 | 0 | 0 |
| Px inpainter changed outside its mask | 0 | 0 | 0 | 0 |
| Masks: glyph-kind / rectangular-kind | 85 / 0 | 85 / 0 | 85 / 0 | 0 / 85 |
| White-fill blocks | 78 | 0 | 78 | 0 |
| Composite duplicates not masked | 2 | 2 | 2 | 2 |
| Masked px | 194,315 | 194,315 | 290,274 | 914,494 |
| Mask reason: no contour found / text_free | 39 / 22 | 39 / 22 | 39 / 22 | n/a |
| Render regions / shrink / below 10 px / overflow / unfit | 85 / 32 / 6 / 4 / 0 | 85 / 32 / 6 / 4 / 0 | 85 / 32 / 6 / 4 / 0 | 85 / 32 / 6 / 4 / 0 |
| Median font size / font spread (mean per page) | 16 / 2.5 | 16 / 2.5 | 16 / 2.5 | 16 / 2.5 |
| Synthetic: detection recall | 100% | 100% | 100% | 100% |
| Synthetic: ink-mask recall | 84.8% | 84.8% | 88.7% | 98.7% |
| Synthetic: residual ink ratio | 11.9% | 12.3% | 10.3% | 14.1% |
| Synthetic: residual ink in mask (fill failure) | 5.4% | 5.9% | 6.0% | 12.9% |
| Synthetic: residual ink outside mask (mask miss) | 6.5% | 6.5% | 4.4% | 1.2% |
| Synthetic: residual ink after low-pass | 36.4% | 33.7% | 38.2% | 1.4% |
| Synthetic: tone flattened in text boxes (px) | 44,541 | 40,056 | 46,138 | 120 |
| Synthetic: text-region MAE | 11.57 | 11.71 | 11.44 | 16.29 |
| Synthetic: OCR character error rate | 0.092 | 0.092 | 0.092 | 0.092 |
| Synthetic: over-erased px (visible) | 8,709 | 8,616 | 8,709 | 16,888 |
| Synthetic: px touched away from text | 8,709 | 22,161 | 8,709 | 288,495 |
| Synthetic: rendered ink outside bubble interior (px) | 0 | 0 | 0 | 0 |
| Seconds per page | 8.2 | 12.8 | 8.1 | 20.8 |
| Time: detect / OCR / inpaint model / verify (s) | 9.5 / 17.2 / 40.5 / 22.0 | 8.7 / 17.6 / 89.3 / 24.1 | 9.7 / 16.8 / 40.3 / 21.3 | 11.9 / 34.8 / 128.1 / 51.7 |

Seconds per page dropped against the old baselines (13.6 / 20.2 / 22.1* /
28.5*) mostly because the three new pages are fast (2–2.5 s each: no model
call, small images) and because the old `rect-masks` pair was measured on a
busy CPU. Do not compare across that boundary.

On the FLUX route, `--mask-mode rectangular` does not change the per-block
masks (they stay glyph-kind); it only grows the white-fill area (masked px
290,274 vs 194,315). On the LaMa route it produces real rectangular masks
(masked px 914,494). This reproduces the old baselines' behavior.

Synthetic pages, as residual ink / visible over-erased px:

| Page | `rtdetr-default` | `rtdetr-lama` | `rect-masks-default` | `rect-masks-lama` |
| --- | --- | --- | --- | --- |
| `syn-white-bubbles` | 0.0% / 252 | 0.0% / 62 | 0.0% / 252 | 0.0% / 33 |
| `syn-conjoined-bubbles` | 0.0% / 84 | 0.0% / 24 | 0.0% / 84 | 0.0% / 11 |
| `syn-textured-narration` | 12.0% / 2,496 | 12.2% / 2,572 | 12.0% / 2,496 | 16.1% / 2,448 |
| `syn-sfx` | 20.1% / 0 | 20.1% / 0 | 20.1% / 0 | 40.4% / 7,175 |
| `syn-webtoon-strip` | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 |
| `syn-tone-rotated` | 15.7% / 3,295 | 15.7% / 3,301 | 15.7% / 3,295 | 27.7% / 4,134 |
| `syn-faint-lettering` | 25.8% / 2,582 | 29.0% / 2,657 | 13.3% / 2,582 | 18.2% / 2,832 |
| `syn-text-touching-outline` | 21.8% / 0 | 21.8% / 0 | 21.8% / 0 | 10.4% / 255 |

## H1–H5: confirmed or rejected

The research report (section 2.0) proposed five code-reading hypotheses. The
new metrics answer them on the default route unless noted.

| Hypothesis | Verdict | Evidence |
| --- | --- | --- |
| **H1** Two-tone SFX lettering is half-masked: the white outline is never in the mask, so it survives (mask miss); with rectangular masks it is covered but residual rises to a fill failure. | **Confirmed** | `syn-sfx`: ink-mask recall 50.6 % — only the dark fill is masked; residual splits 10.0 % in-mask / 10.1 % out-of-mask, i.e. the outline stays. On `rect-masks-lama` recall is 100 % and residual jumps to 40.4 %, all in-mask (LaMa cannot fill the large hole in phase). |
| **H2** Screentone dots pass the white test, so tone blocks are white-filled and the tone is flattened to a visible hole. | **Confirmed, and it also fires on rotated / non-integer tone** | `syn-textured-narration`: 2 of 3 blocks white-filled, hole-in-tone 12,484 px, residual 12.0 % entirely in-mask. `syn-tone-rotated` (periods 7.3 at 15°, 8.6 at −24°): all 4 blocks white-filled, hole-in-tone 25,399 px, residual 15.7 % (10.3 in / 5.3 out). This is a wrong-tier fill, not an inpainter weakness. |
| **H3** The ROI-edge rule drops whole glyph components that the detector box cuts, leaving a mask miss. | **Confirmed on the adversarial page; contribution to the old SFX residue is not separable from H1** | `syn-text-touching-outline`: residual 21.8 %, all out-of-mask, 0 % in-mask; ink recall 76.3 %. The strokes crossing the bubble outline are never masked; the outlines themselves survive. |
| **H4** Light-on-dark halos stay unmasked (small mask miss). | **Rejected on the synthetic page** | The `light-on-dark` block of `syn-textured-narration`: ink recall 100 %, out-of-mask residual 0.0 % on all four routes; its 12–16 % residual is entirely in-mask (fill side). The halos are covered by the erase mask as built (block mask plus white-fill rect), so no mask miss is measurable. |
| **H5** Bubble interiors fail even on clean bubbles, so blocks fall back to the plain glyph mask. | **Confirmed, with the reason now attributed** | 61 of 85 masked blocks use the plain glyph strategy on the default routes (was 57 of 75). Reason codes: "no contour found" 39, "text_free" 22; "contour touches ROI edge", "area test failed" and "coverage below 35 %" never fired on this corpus. |

## Phase-2 order call

**WP4 (the fill-tier router) first, WP5 (ink masks + flood-fill interiors)
second.**

On the textured/sfx synthetic pages (`syn-textured-narration`, `syn-sfx`,
`syn-tone-rotated`), residual ink in the mask — fill failure — is about twice
the mask miss: 10.8 % in-mask vs 5.2 % out-of-mask on average. The in-mask half
is dominated by two wrong-tier fills the router owns: the white-fill fast path
flattening screentone (hole-in-tone 12,484 + 25,399 px, H2) and the LaMa smear
that does not regenerate hatching in phase (`syn-sfx` keeps 94.3 % of its
residual after a low-pass at the tone period — almost pure texture-phase
mismatch). `light-on-dark` (12 %) is also all in-mask.

WP5 follows immediately: the out-of-mask half is concentrated exactly where
WP5 aims — the SFX white outline (H1, 10.1 % of `syn-sfx` ink), faint gray
lettering (14.7 % out-of-mask on `syn-faint-lettering`) and lettering that
crosses the bubble outline (21.8 % out-of-mask on
`syn-text-touching-outline`), plus the 39 "no contour found" glyph blocks (H5).
Rectangular masks already show what covering that ink costs the fill (40.4 %
residual on `syn-sfx`), so WP4's tier 2 (lattice copy) is the precondition for
WP5's bigger masks to pay off.

## Eyeball observations (`--save-images`)

Checked on the sanity run (3 new pages, default route) and a dedicated run for
`syn-textured-narration` / `syn-sfx`. All three new pages complete without
errors, detection recall is 100 %, and every block takes the white-fill fast
path (no model call at all — `inpaint_model` time 0), which is itself the H2
finding: the fast path fires on rotated screentone and flattens it.

- `syn-tone-rotated`: the erased boxes come back as flat fields; the rotated
  lattice inside them is gone (25,399 px hole-in-tone). The boxed caption keeps
  its border. Over-erase 3,295 px; MAE 25.1 (worst of the corpus).
- `syn-faint-lettering`: the light-gray (185) lettering is detected and erased
  (recall 84.7 %), mostly by one large white-fill rect per block; the diagonal
  hatching inside that rect is visibly thinner than outside it. Residual 25.8 %
  (11.1 in / 14.7 out) — faint strokes at the rect edges survive. Step 6
  re-OCR reads the rendered page cleanly (0/3 blocks flagged); OCR CER 0.35
  comes from the re-read of rendered English, not from leftovers.
- `syn-text-touching-outline`: bubble outlines survive, but dark clusters sit
  on the outline exactly where the lettering crossed it — the crossing strokes
  were never masked. Residual 21.8 %, all out-of-mask, over-erase 0. Step 6
  flags 2 of 3 blocks (it re-reads the leftover strokes as CJK).
- `syn-textured-narration`: the narration block over screentone is white-
  filled, leaving a flat rectangular hole in the tone with the replacement
  text inside it; the boxed caption over the dark gradient is refilled and
  re-texted with its border intact.
- `syn-sfx`: the dark fill is erased by the LaMa fallback and the diagonal
  hatching inside the hole comes back as a shapeless smear, not in phase with
  the surrounding lines; the white outline around the erased glyphs remains.

## Noise

- **Identity.** All pre-existing metrics were identical on the old 8 pages
  against the previous baselines, on all four routes. Mask counts, render
  counts and synthetic scores have never moved between runs on this machine.
- **Timings.** The desktop is in active use; the queue rule is two load
  readings under 20 about 30 s apart, no other eval or pytest running. Under
  that rule the four routes above are mutually consistent (render 0.6–0.8 s on
  the 8-page scope, 1.4 s on `rect-masks-lama`, whose masks are 5× larger). If
  `render` or `mask` time moves more than about 20 % from these numbers, the
  machine was busy: rerun before trusting a timing. The previous baselines'
  warning about the two `rect-masks` runs having been measured on a busy CPU
  no longer applies — those runs were replaced by the ones above.
- **kana/kanji floor.** manga-ocr reads some short rendered English as kana:
  about 1 block per page on the synthetic pages, more on text that crosses
  outlines (`syn-text-touching-outline` flags 2 of 3 because it re-reads real
  leftover strokes). Treat a difference of one or two blocks per page as
  noise; on the adversarial pages check the residual ink first.
- **Private pages.** The three pages in `eval/pages/` are fixed; `--compare`
  between runs on this machine scopes itself to pages scored in both.
