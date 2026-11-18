# Pipeline evaluation harness

`scripts/eval_pipeline.py` scores the backend translation pipeline on a fixed
set of pages. Use it to measure a quality change (masking, fill tiers,
typesetting, text color) before and after it lands.

The harness runs the real `TranslationPipeline.translate` page by page: detect,
OCR, translate, inpaint, render, then the Step 6 re-OCR check. It observes each
stage by wrapping the stage's function for the length of the run. It never
changes what the pipeline does: the same masks, inpainter and render options
reach the same code.

## Run it

From the repository root:

```powershell
uv run --project backend --locked python scripts/eval_pipeline.py --corpus eval/corpus.json --out eval/runs/my-change
```

`npm run eval:pipeline -- --out eval/runs/my-change` does the same. Each run
writes `run.json` (all numbers, per page and summed) and `summary.md` (a short
report). `eval/runs/` is gitignored.

To compare two runs, pass two run folders or `run.json` files:

```powershell
uv run --project backend --locked python scripts/eval_pipeline.py --compare eval/baseline/rtdetr-lama eval/runs/my-change --out eval/runs/my-change/compare.md
```

The comparison prints every summary metric for run A and run B, with B - A and
whether that change is better or worse, and then a per-page table. It flags a
page whose image changed between the two runs.

Models load the same way the backend loads them. To reuse another checkout's
downloads, set `MANGA_BACKEND_MODELS_DIR` to its `backend/models_cache`. CPU is
the default device. Use `--device cuda` (or set `EVAL_DEVICE`) on a GPU build.

### Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--detector` | `rtdetr` | `rtdetr`, `animetext-yolo`, or `truth`. `truth` uses the ground-truth boxes of the synthetic pages, so mask, fill and render metrics do not depend on detector misses. |
| `--mask-mode` | `auto` | `auto` follows the pipeline: rectangular masks only for `animetext-yolo`. `rectangular` and `contour` force one mask path on any detector's boxes. |
| `--inpainter` | `flux2_klein_lanpaint` | The backend default. Without ComfyUI it falls back to `lama_large`, and the run records the fallback. |
| `--translator` | `stub` | A deterministic offline stand-in (see below). Pass a provider name (`gemini`, `openai`, `local`, ...) to use a real one. |
| `--pages`, `--tags` | all | Score only some pages. |
| `--save-images` | off | Write `mask.png`, `inpainted.png` and `rendered.png` per page under `<out>/images/`. Never commit these. |
| `--no-verify` | off | Skip Step 6. This also removes the residue metrics. |
| `--no-warmup` | off | Time the first page cold. By default the first page runs once untimed so model warm-up does not skew it. |
| `--strict` | off | Exit 1 when a listed private page is missing. |

Render options match the `POST /translate` defaults (`bg_opacity` 0, preferred
font size 16, and so on). A unit test keeps them in sync with
`backend/src/manga_backend/api/deps.py`.

## Corpus

`eval/corpus.json` lists the pages. Each entry has an `id`, `tags`, and either
`synthetic` (a page drawn in code) or `path` (an image file, relative to the
manifest):

```json
{ "id": "real-bookwalker-spread-01", "path": "pages/real-bookwalker-spread-01.png",
  "tags": ["bookwalker", "white-bubble", "vertical-text"] }
```

Known tags: `white-bubble`, `textured-background`, `sfx`, `vertical-text`,
`horizontal-text`, `conjoined-bubbles`, `webtoon-strip`, `bookwalker`,
`light-on-dark`, `narration`. The loader rejects any other tag, so a typo
cannot quietly create a new group.

### Synthetic pages

Five pages are drawn in code, so the harness and its tests run anywhere:
white bubbles, conjoined bubbles, narration on screentone and on a dark
gradient, outlined SFX over hatching, and a 640x2400 color webtoon strip. Each
one keeps its clean plate (the page without lettering), the exact ink pixels
and the text boxes. That makes three extra scores possible: detection recall,
residual ink after inpainting, and pixels erased away from any text.

Lettering uses the system CJK font that the renderer also uses (MS Gothic on
Windows). Without one, it draws deterministic stroke clusters instead. The
font is recorded as `glyph_source`, and the page hash changes with it, so a
comparison across machines shows "image differs".

### Private pages

Real pages are not committed. They live in `eval/pages/`, which is gitignored,
and `npm run verify:repository` fails if anything under it is tracked. To fill
the folder:

1. Save page images (PNG, JPEG or WebP) into `eval/pages/`. Screenshots of a
   reader viewport work; crop browser chrome away if you can.
2. Name them after the ids in `eval/corpus.json`, or add new entries with the
   right tags.
3. Keep ids stable. The comparison matches pages by id, and checks the image
   hash so a replaced image is not compared as if it were the same page.

A listed page that is missing is reported as `missing` and skipped. The run
still exits 0 unless you pass `--strict`. The current manifest names three
private pages: one single page and two BookWalker viewer spreads. No real
webtoon strip is listed yet; the synthetic strip covers that tag for now.

## Metrics

Per page, summed in `summary`, and summed again per tag in `summary.by_tag`:

| Metric | Source | Meaning |
| --- | --- | --- |
| `cjk_residue.step6_rate` | Step 6 `verification_passed` | Share of blocks the pipeline's own re-OCR check flags as still containing CJK. |
| `cjk_residue.kana_kanji_rate` | Step 6 `verified_text` | The same re-OCR text, NFKC-folded and counted only when it contains kana or kanji. It covers only blocks that were erased and rendered. |
| `inpaint.changed_outside_mask_px` | inpaint step input vs output | Pixels the inpainting step changed outside every mask it built. These masks are the per-block masks plus the white-fill masks. Expected 0. |
| `inpaint.model_changed_outside_mask_px` | inpainter call | Pixels the inpainter model changed outside the mask it was given. Expected 0. |
| `mask.kinds` | per-block mask builders | `contour_safe_glyph` (`glyph`, `bubble-clipped-glyph`, `contour-clipped-glyph`), `bubble_interior`, `rectangular`, `skipped` (no safe mask). Raw strategy names are in `mask.strategies`. |
| `mask.white_fill_blocks` | `prepare_flux_inpainting` | Blocks filled flat white without a model. This fast path exists only on the FLUX route, including when FLUX then falls back to LaMa. |
| `mask.suppressed_composites` | | Blocks dropped from masking as duplicates of an OCR composite region. |
| `inpaint.fallback`, `inpaint.used` | pipeline result | Which inpainter actually ran, for example `flux2_klein_lanpaint` falling back to `lama_large`. |
| `render.shrink_events` | `auto_fit_font_size` | Regions rendered below the preferred font size. |
| `render.below_min_readable` | | Regions rendered below 10 px. |
| `render.overflow_events` | `find_overflow_region` | Regions moved into a larger box to reach a readable size. |
| `render.unfit_skipped` | | Regions with no font size that fits, so they were not drawn. |
| `truth.recall` | synthetic only | Share of truth text boxes at least half covered by detected boxes. |
| `truth.residual_ink_ratio` | synthetic only | Share of lettering pixels that still differ from the clean plate by more than 40 levels after inpainting. |
| `truth.text_mae` | synthetic only | Mean absolute error against the clean plate inside the text boxes. |
| `truth.over_erased_px` | synthetic only | Pixels more than 4 px away from any text box that inpainting changed by more than 40 levels: visibly damaged art, such as erased bubble outlines or line art. |
| `truth.touched_away_px` | synthetic only | The same area, counting any change at all. Large masks over flat areas raise this even when the change is invisible. |
| `timings.*` | stage wrappers | Seconds in detect, OCR, translate, mask building, inpainter model, render and verify. `other` covers the rest, such as bubble geometry. |

### Caveats

- **The Step 6 verdict over-reports residue.** manga-ocr widens ASCII to
  full-width forms, and the pipeline's `has_cjk` counts full-width forms as
  CJK. So almost every block with rendered English fails Step 6. The harness
  reports that rate as the pipeline computes it. Use `kana_kanji_rate` to see
  Japanese that was actually left behind.
- **The kana/kanji rate has a noise floor.** manga-ocr sometimes reads short
  rendered English as kana; for example, "We." came back as それは、. On the
  synthetic pages, 2 of 11 rendered blocks were counted this way although no
  ink was left (residual ink 0%). Read a change of one or two blocks as noise.
  On synthetic pages, `truth.residual_ink_ratio` is the exact measure.
- **The stub translator.** It returns deterministic English-like text, about
  2.7 Latin letters per Japanese character. This keeps typesetting metrics
  comparable between runs without network calls. Pass `--translator` to
  measure with a real provider instead, but expect run-to-run variance.
- **AnimeText YOLO.** Its weights come from a gated Hugging Face repository.
  When they are missing, `--detector animetext-yolo` stops with a hint. Use
  `--detector rtdetr --mask-mode rectangular` to score its rectangular-mask
  path on RT-DETR boxes. Rectangular masks apply only to non-FLUX inpainters:
  the FLUX route always builds contour-safe masks, and `rectangular` changes
  only how its white fill is sampled and grown.
- **Timings** are wall-clock on one machine. Compare them only between runs on
  the same machine and device. Even then, identical stages varied by up to
  about 40% between back-to-back baseline runs on a shared CPU.
- **Different page sets.** When two runs scored different pages (for example,
  a machine without the private pages compared with a committed baseline),
  `--compare` computes the overall table over the pages scored in both runs.

## Baselines

`eval/baseline/` holds committed runs of the current code (numbers only, no
images), with notes on how they were made. Compare a change against the
matching baseline, and add a new baseline when the pipeline intentionally
changes.
