# Baselines

Four runs of `scripts/eval_pipeline.py` on `eval/corpus.json`, made on commit
`1b0b4cfe` (clean tree), which sits on `publish/rewrite-modern-stack`
(`189f34ed`). Each folder holds `run.json` and `summary.md`. No images are
committed.

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
  checkout's `backend/models_cache` through `MANGA_BACKEND_MODELS_DIR`.
- **Pipeline settings.** OCR `manga-ocr`, stub translator, Step 6 on, one
  untimed warm-up page, and the `POST /translate` render defaults.
- **Pages.** All 8 pages were scored: 5 synthetic and the 3 private pages in
  `eval/pages/`. Synthetic lettering used MS Gothic (`msgothic.ttc`). On a
  machine without the private pages, `--compare` falls back to the 5
  synthetic pages.
- **Inpainter: LaMa.** ComfyUI was not running at `127.0.0.1:8188`, so FLUX
  (LanPaint) never ran. Every page that needed a model on the FLUX route fell
  back to `lama_large`; that is the 5 fallback pages. The other 3 pages
  report `flux2_klein_lanpaint` only because the white-fill fast path covered
  every block, so no model was called. Read the `*-default` runs as "FLUX
  route with LaMa fill". Rerun them with ComfyUI up to measure FLUX itself.
- **Detector: RT-DETR only.** The AnimeText YOLO weights are not on disk, and
  their Hugging Face repository is gated. No token was used. The `rect-masks`
  runs emulate the AnimeText mask path with `--mask-mode rectangular` on
  RT-DETR boxes. They measure that mask path, not AnimeText detection.

## Headline numbers

Times are wall-clock totals over the 8 pages, except seconds per page.

| Metric | `rtdetr-default` | `rtdetr-lama` | `rect-masks-default` | `rect-masks-lama` |
| --- | --- | --- | --- | --- |
| Inpainter used (pages) | white fill only x3, lama_large x5 (fallback) | lama_large x8 | white fill only x3, lama_large x5 (fallback) | lama_large x8 |
| Blocks detected / to erase | 79 / 77 | 79 / 77 | 79 / 77 | 79 / 77 |
| CJK residue, Step 6 verdict | 98.7% | 98.7% | 98.7% | 98.7% |
| CJK residue, kana/kanji only | 14.3% | 15.6% | 13.0% | 11.7% |
| Px changed outside mask | 0 | 0 | 0 | 0 |
| Px inpainter changed outside its mask | 0 | 0 | 0 | 0 |
| Masks: contour-safe glyph / rectangular | 75 / 0 | 75 / 0 | 75 / 0 | 0 / 75 |
| Masks: bubble interior / skipped | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| White-fill blocks | 68 | 0 | 68 | 0 |
| Composite duplicates not masked | 2 | 2 | 2 | 2 |
| Masked px | 124,721 | 124,721 | 181,216 | 718,173 |
| Render regions / shrink / below 10 px / overflow / unfit | 75 / 32 / 6 / 4 / 0 | 75 / 32 / 6 / 4 / 0 | 75 / 32 / 6 / 4 / 0 | 75 / 32 / 6 / 4 / 0 |
| Median font size | 16 | 16 | 16 | 16 |
| Synthetic: detection recall | 100% | 100% | 100% | 100% |
| Synthetic: residual ink ratio | 6.4% | 6.5% | 6.4% | 11.3% |
| Synthetic: text-region MAE | 7.97 | 8.09 | 7.97 | 13.44 |
| Synthetic: over-erased px (visible) | 2,832 | 2,658 | 2,832 | 9,667 |
| Synthetic: px touched away from text | 2,832 | 6,407 | 2,832 | 227,139 |
| Seconds per page | 13.3 | 20.1 | 14.3 | 19.9 |
| Time: detect / OCR / inpaint model / verify (s) | 7.4 / 21.9 / 47.0 / 28.7 | 9.9 / 24.9 / 93.7 / 31.0 | 7.9 / 23.7 / 51.0 / 30.4 | 8.7 / 25.6 / 91.5 / 32.3 |

Synthetic pages, as residual ink / visible over-erased px:

| Page | `rtdetr-default` | `rtdetr-lama` | `rect-masks-default` | `rect-masks-lama` |
| --- | --- | --- | --- | --- |
| `syn-white-bubbles` | 0.0% / 252 | 0.0% / 62 | 0.0% / 252 | 0.0% / 33 |
| `syn-conjoined-bubbles` | 0.0% / 84 | 0.0% / 24 | 0.0% / 84 | 0.0% / 11 |
| `syn-textured-narration` | 12.0% / 2,496 | 12.2% / 2,572 | 12.0% / 2,496 | 16.1% / 2,448 |
| `syn-sfx` | 20.1% / 0 | 20.1% / 0 | 20.1% / 0 | 40.4% / 7,175 |
| `syn-webtoon-strip` | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 |

## Findings

The harness changes nothing in the pipeline. These are observations for
follow-up work.

1. **Step 6 flags almost every block.** The verdict is 98.7% on every run,
   although the synthetic pages show 0% residual ink in white bubbles.
   manga-ocr widens ASCII to full-width forms (`jaconv.h2z(ascii=True)`), and
   `has_cjk` counts the full-width block U+FF00-FFEF as CJK. As a result,
   rendered English comes back as full-width Latin and fails: "Right me." was
   read as "Ｒｅｓｔｒｙｅ．". Use `kana_kanji_rate` until Step 6 is fixed.
2. **The kana/kanji rate has a noise floor.** manga-ocr reads some short
   English as kana. On `syn-white-bubbles` and `syn-webtoon-strip`, 1 of 4
   blocks is counted each, with 0% residual ink. On the real pages, treat a
   difference of one or two blocks per page as noise.
3. **White fill paints over bubble outlines.** Most blocks get the plain
   `glyph` strategy: 3 of 4 on `syn-white-bubbles`, and 57 of 75 overall,
   because no closed bubble interior is found around the RT-DETR box. Where
   that glyph mask reaches the bubble outline, the white-fill fast path paints
   the outline white. That is 252 visible px on `syn-white-bubbles` and 84 on
   `syn-conjoined-bubbles`. LaMa, given the same masks, redraws most of the
   outline (62 and 24 px).
4. **Residual ink is concentrated on textured backgrounds.** All of it comes
   from narration on screentone or a dark gradient (12%) and outlined SFX over
   hatching (20%). White bubbles and the webtoon strip come out clean.
5. **Rectangular masks cost quality on texture.** On the LaMa route,
   rectangular masks grow masked pixels 5.8x. They double residual ink on SFX
   (20% to 40%) and add 7,175 visibly damaged px there. On white bubbles they
   touch large areas, but only by a level or two, so over-erasure stays low.
   On the FLUX route, rectangular mode only grows the white-fill area (masked
   px 181,216 vs 124,721). The per-block masks and every synthetic score stay
   the same as the default.
6. **The white-fill fast path is the main speed win.** It handles 68 of the
   75 masked blocks, which halves the inpainter time against LaMa-only (47 s
   vs 94 s).
7. **Typesetting.** With the stub's text length, 32 of 75 regions shrink below
   the preferred 16 px, 6 fall below 10 px, and 4 move to an overflow box.
   These counts do not depend on the mask path.

## Noise

- **Timings.** These baselines were run twice with the same flags. Timings
  differed by up to about 45% between the two runs: `rect-masks-default`
  took 20.5 s per page in the first run and 14.3 s in the second. Other
  processes shared the CPU. Compare timings only with care.
- **Masks and synthetic scores.** Mask counts, render counts and the
  synthetic truth metrics were identical in both runs.
- **Visible over-erasure on screentone.** On `syn-textured-narration`, about
  2,500 px count as over-erased on every route. Regenerated tone dots do not
  line up with the original pattern, so they differ by more than 40 levels
  even where the fill looks right. Watch that page's change rather than its
  level.
