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
| `--mask-mode` | `auto` | `auto` follows the pipeline: rectangular masks only for `animetext-yolo`. `rectangular` and `contour` force one mask path on any detector's boxes. `oracle` masks the diluted truth ink instead, so the run measures the fill alone (synthetic pages only; real pages are skipped). |
| `--fill` | `auto` | `oracle` pastes the clean plate inside the pipeline's own declared erase mask (block masks plus white-fill masks), so the run measures the mask alone (synthetic pages only). No inpainter model runs. |
| `--inpainter` | `flux2_klein_lanpaint` | The backend default. Without ComfyUI it falls back to `lama_large`, and the run records the fallback. |
| `--translator` | `stub` | A deterministic offline stand-in (see below). Pass a provider name (`gemini`, `openai`, `local`, ...) to use a real one. |
| `--pages`, `--tags` | all | Score only some pages. |
| `--save-images` | off | Write `mask.png`, `inpainted.png` and `rendered.png` per page under `<out>/images/`. Never commit these. |
| `--no-verify` | off | Skip Step 6. This also removes the residue metrics. |
| `--no-warmup` | off | Time the first page cold. By default the first page runs once untimed so model warm-up does not skew it. |
| `--strict` | off | Exit 1 when a listed private page is missing. |

The two oracle modes are ablations, not pipeline changes: they answer "with a
perfect mask, how good is the fill?" (`--mask-mode oracle`) and "with a perfect
fill, how much lettering does the mask miss?" (`--fill oracle`). On the
`--fill oracle` route the recorded `inpaint.used` is `oracle-fill` and no
inpainter model is loaded, so runs are fast.

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
`light-on-dark`, `narration`, `adversarial`. The loader rejects any other
tag, so a typo cannot quietly create a new group.

### Synthetic pages

Eight pages are drawn in code, so the harness and its tests run anywhere:
white bubbles, conjoined bubbles, narration on screentone and on a dark
gradient, outlined SFX over hatching, a 640x2400 color webtoon strip, and
three adversarial pages (rotated non-integer-period screentone, faint
light-gray lettering, lettering that crosses the bubble outline). Each
one keeps its clean plate (the page without lettering), the exact ink pixels
and the text boxes. That makes three extra scores possible: detection recall,
residual ink after inpainting, and pixels erased away from any text.

The adversarial pages exist to break methods tuned on the first five: the
classic pages draw tone as perfect axis-aligned integer lattices, which
flatters any periodic-background assumption, and all their lettering is
pitch black or pure white, which the fixed dark/bright thresholds pass by.

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
| `cjk_residue.step6_rate` | Harness re-OCR `verification_passed` | Share of blocks the harness's post-render re-OCR pass flags as still showing Han, kana or Hangul. Production no longer runs Step 6 (WP3); the harness keeps this pass after the page timer (`timings.verify_harness`) for continuity. |
| `cjk_residue.kana_kanji_rate` | Harness re-OCR `verified_text` | The same re-OCR text, NFKC-folded and counted only when it contains kana or kanji. It covers only blocks that were erased and rendered. |
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
| `truth.ink_mask_recall` | synthetic only | Share of truth ink pixels covered by the final erase mask (block masks plus white-fill masks). |
| `truth.residual_in_mask_ratio` | synthetic only | Share of truth ink pixels that differ from the clean plate *inside* the erase mask: a fill failure. |
| `truth.residual_out_of_mask_ratio` | synthetic only | Share of truth ink pixels that differ *outside* the erase mask: lettering the mask never covered. `residual_in + residual_out = residual_ink_ratio`. |
| `truth.residual_ink_lp_ratio` | synthetic only | Residual ink compared after a Gaussian low-pass at the tone period (sigma 5, threshold 24 levels). Tone painted out of phase drops out; surviving strokes are real fill failures. |
| `truth.hole_in_tone_px` | synthetic only | Pixels inside the text boxes where the clean plate is textured (dots, hatching) but the output is locally flat, which `over_erased_px` cannot see because it stays near the text. Truth ink pixels do not count (leftover lettering is residual ink); the hole is the texture that vanished between the strokes. White fill over screentone is the classic cause. |
| `truth.render_ink_outside_bubble_px` | synthetic only | Rendered ink between the truth bubble interior (box shrunk 4 px) and 12 px beyond the box: typeset text crossing the bubble outline. Lettering without a bubble never counts. |
| `truth.ocr_cer` | synthetic only | Character error rate of the OCR pass against `TruthBox.text`, matched by box IoU (NFKC, whitespace ignored). |
| `truth.text_mae` | synthetic only | Mean absolute error against the clean plate inside the text boxes. |
| `truth.over_erased_px` | synthetic only | Pixels more than 4 px away from any text box that inpainting changed by more than 40 levels: visibly damaged art, such as erased bubble outlines or line art. |
| `truth.touched_away_px` | synthetic only | The same area, counting any change at all. Large masks over flat areas raise this even when the change is invisible. |
| `mask.reason_codes` | per-block mask builders | For every block that ended without a bubble interior (`glyph`, `no-closed-bubble`, `unsafe-sfx`), why: `text_free` (never tried, masks.py:316), `no_contour`, `contour_touches_roi_edge` (masks.py:278), `area_test_failed` (masks.py:273), `coverage_below_35` (masks.py:303), or `clipped_ink_empty` (interior found but it clipped away every glyph). Computed in the harness by mirroring the same helpers; pipeline return values are unchanged. |
| `render.font_size_spread` | per-page render fits | Max minus min of the final dialogue font sizes on the page; the typesetting harmonization metric. |
| `translate.budget_chars` (per page), `translate.budget_chars_min/max/mean` (summary) | `PageContext` + `fit.room_capacity` | One character budget per translation unit, in reading order: the unit's WP7 room capacity at the page's lettering size (or, for a room too small to hold a phrase at that size, its capacity at the 10 px readable floor). `translate.budgeted` counts the units that got one. |
| `translate.page_image` | `supports_page_image` | Pages whose provider request carried the downscaled page with a reading-order number drawn at each unit. Text-only routes (the local OpenAI-compatible route among them) keep the text-only prompt. |
| `translate.requests` | `translation.TranslationReport` | Provider requests the translate step made: 1 for the page, 2 when one re-ask was needed. Zero on stub runs, which never enter the step. |
| `translate.reask_units`, `translate.reask_shortened` | `TranslationReport` | Units whose translation could not be set at the minimum readable size (and so were re-asked once for a shorter version), and how many of those came back with usable text. `translate.reask_failed` marks a re-ask the provider refused. |
| `residual_flag.*` | `pipeline/residual.py` | Pre-render residual check agreement against synthetic truth (`agreement`, `in_mask_agreement`, `truth_blocks`, `flagged_blocks`, `false_positives`, `false_negatives`) plus escalation stats (`escalated_blocks`, `second_flags`, `mask_grown_px`). |
| `timings.*` | stage wrappers | Seconds in detect, OCR, translate, mask building, inpainter model, render and production verify (0.0 since WP3). `timings.verify_harness` records the harness's own post-render re-OCR outside `seconds_per_page`; `other` covers the rest, such as bubble geometry. |

### Caveats

- **Step 6 before and after `0c03191`.** manga-ocr widens ASCII to full-width
  forms. Before that commit, Step 6 counted full-width forms as CJK, so
  almost every block with rendered English failed it. Runs on older code show
  a Step 6 rate near 99%. Since then Step 6 counts only Han, kana and Hangul
  letters, so its rate tracks `kana_kanji_rate`. The two differ only because
  `kana_kanji_rate` leaves out blocks that were not erased and rendered.
- **The kana/kanji rate has a noise floor.** manga-ocr sometimes reads short
  rendered English as kana; for example, "We." came back as それは、. On the
  synthetic white-bubble pages, 2 of 10 rendered blocks were counted this way
  although no ink was left (residual ink 0%). Read a change of one or two blocks as noise.
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
  the same machine and device. Even then, the same configuration varied by
  up to about 45% between two baseline runs on a shared CPU.
- **Different page sets.** When two runs scored different pages (for example,
  a machine without the private pages compared with a committed baseline),
  `--compare` computes the overall table over the pages scored in both runs.

## Baselines

`eval/baseline/` holds committed runs of the current code (numbers only, no
images), with notes on how they were made. Compare a change against the
matching baseline, and add a new baseline when the pipeline intentionally
changes.

## Reading-order labels (optional)

The pipeline sorts detected blocks into reading order before OCR and
translation (panels first — found via paper-colored gutters — then the
lettering inside each panel; right to left for manga pages, left to right
for tall webtoon strips, height >= 1.6 x width). `scripts/eval_reading_order.py`
scores that order against an expected order per page:

- synthetic pages use the page generator's ground-truth boxes ordered by the
  documented convention, so they need no labels;
- real pages need a hand-labelled file, by default
  `eval/reading-order-labels.json` (gitignored like the page images):

```json
{
  "real-single-page-01": [
    { "text": "ちょっとロバートぉ！！", "bbox": [39, 43, 82, 136] },
    { "text": "え！？", "bbox": [350, 39, 388, 99] }
  ]
}
```

Each entry lists that page's text regions in the intended reading order,
with `bbox` in `[x1, y1, x2, y2]` pixels. A detected block matches a label
when the boxes' overlap covers at least half of the smaller box (RT-DETR
text boxes are usually larger than a hand-labelled text box); the report is
Kendall's tau over the matched blocks plus an exact-match flag, so
detection misses do not penalize the order of the blocks that were found.
Run it with::

```powershell
uv run --project backend --locked python scripts/eval_reading_order.py
uv run --project backend --locked python scripts/eval_reading_order.py --detector truth
```

`--detector truth` uses the synthetic ground-truth boxes (no model load);
the default runs the real RT-DETR and needs `MANGA_BACKEND_MODELS_DIR` like
the main harness. `--direction rtl|ltr` overrides the aspect-ratio rule.
Unlabelled real pages are reported but do not enter the summary.
