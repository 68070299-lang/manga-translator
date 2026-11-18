# Baselines

Four runs of `scripts/eval_pipeline.py` on `eval/corpus.json` (11 pages), made
on commit `f048e045` (clean tree). That commit is **WP4, the verified
fill-tier router**: the white-or-model choice is gone; every block is now
filled by the cheapest tier that reproduces a held-out band of its context
ring (tier 0 flat, tier 1 smooth plane, tier 2 periodic lattice copy), and only
blocks no tier can verify reach the model (crop LaMa, FLUX above it). Each
folder holds `run.json` and `summary.md`. No images are committed.

| Folder | Flags | What it measures |
| --- | --- | --- |
| `rtdetr-default` | `--inpainter flux2_klein_lanpaint` | The backend default route: RT-DETR boxes, contour-safe masks, router tiers, model fallback. |
| `rtdetr-lama` | `--inpainter lama_large` | Same masks; blocks that fail every tier go to crop LaMa. |
| `rect-masks-default` | `--mask-mode rectangular --inpainter flux2_klein_lanpaint` | Same as `rtdetr-default` (see "rect-masks-default is now the contour route" below). |
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
- **Inpainter: LaMa stands in for FLUX.** ComfyUI was not running, so the
  8 model-queued blocks on the FLUX route fell back to `lama_large`
  (`fallback_pages` 4; the other 7 pages needed no model at all). Read the
  `*-default` runs as "FLUX route with LaMa fill".
- **Detector: RT-DETR only.** The AnimeText YOLO weights are gated and not on
  disk; `--mask-mode rectangular` emulates that mask path on RT-DETR boxes.

## What the router chose (87 blocks to erase)

| Tiers | `rtdetr-default` | `rtdetr-lama` | `rect-masks-default` | `rect-masks-lama` |
| --- | --- | --- | --- | --- |
| flat | 39 | 39 | 39 | 16 |
| smooth | 1 | 1 | 1 | 1 |
| lattice | 37 | 37 | 37 | 42 |
| model | 8 | 8 | 8 | 26 |
| Blocks queued to the model | 8 of 87 | 8 of 87 | 8 of 87 | 26 of 87 |

Per block type: flat won every white-bubble and webtoon block; smooth won the
dark gradient narration; lattice won the screentone narration (integer and
rotated non-integer), the SFX blocks over hatching on the contour routes, and
most toned real-page blocks (BookWalker spreads: 11 and 14 lattice blocks).
What escalates: 1 white-bubble block with a rtdetr box close to the outline,
the lettering-crossing-outline blocks on the rect route, and real-page blocks
whose surroundings are genuinely unmodellable art. The previous behavior sent
78 blocks to the flat white path and 7-9 (or all 85 on `rtdetr-lama`) to the
model.

## Headline numbers

Times are wall-clock totals over the 11 pages, except seconds per page. **The
timing window was degraded** (ambient desktop load; `detect`/`ocr`/`verify`
read about 1.5-2x the WP1 baselines' values on unchanged code paths) — treat
all timing rows as upper bounds and compare quality rows instead.

| Metric | `rtdetr-default` | `rtdetr-lama` | `rect-masks-default` | `rect-masks-lama` |
| --- | --- | --- | --- | --- |
| Inpainter used (pages) | fill_tiers x7, lama_large x4 (fallback) | lama_large x4, fill_tiers x7 | fill_tiers x7, lama_large x4 (fallback) | lama_large x9, fill_tiers x2 |
| Blocks detected / to erase | 89 / 87 | 89 / 87 | 89 / 87 | 89 / 87 |
| Router-filled blocks / queued to model | 77 / 8 | 77 / 8 | 77 / 8 | 59 / 26 |
| CJK residue, Step 6 verdict | 21.3% | 21.3% | 21.3% | 27.0% |
| CJK residue, kana/kanji only | 21.8% (19 blocks) | 21.8% | 21.8% | 27.6% (24) |
| Px changed outside mask | 0 | 0 | 0 | 0 |
| Px inpainter changed outside its mask | 0 | 0 | 0 | 0 |
| Masks: glyph-kind / rectangular-kind | 85 / 0 | 85 / 0 | 85 / 0 | 0 / 85 |
| Composite duplicates not masked | 2 | 2 | 2 | 2 |
| Masked px | 194,315 | 194,315 | 194,315 | 914,494 |
| Render regions / shrink / below 10 px / overflow / unfit | 85 / 32 / 6 / 4 / 0 | 85 / 32 / 6 / 4 / 0 | 85 / 32 / 6 / 4 / 0 | 85 / 32 / 6 / 4 / 0 |
| Synthetic: detection recall | 100% | 100% | 100% | 100% |
| Synthetic: ink-mask recall | 84.8% | 84.8% | 84.8% | 98.7% |
| Synthetic: residual ink ratio | 7.7% | 7.7% | 7.7% | 2.2% |
| Synthetic: residual ink in mask (fill failure) | 1.3% | 1.3% | 1.3% | 1.0% |
| Synthetic: residual ink outside mask (mask miss) | 6.5% | 6.5% | 6.5% | 1.2% |
| Synthetic: residual ink after low-pass | 8.6% | 8.6% | 8.6% | 1.4% |
| Synthetic: tone flattened in text boxes (px) | 1,305 | 1,305 | 1,305 | 163 |
| Synthetic: text-region MAE | 5.04 | 5.04 | 5.04 | 2.41 |
| Synthetic: OCR character error rate | 0.092 | 0.092 | 0.092 | 0.092 |
| Synthetic: over-erased px (visible) | 908 | 908 | 908 | 707 |
| Synthetic: px touched away from text | 1,035 | 1,035 | 1,035 | 71,455 |
| Synthetic: rendered ink outside bubble interior (px) | 0 | 0 | 0 | 0 |
| Seconds per page (busy window) | 8.4 | 10.2 | 11.2 | 14.0 |
| Time: detect / OCR / mask / inpaint model / render / verify (s) | 11.2 / 32.5 / 2.2 / 6.1 / 1.2 / 39.0 | 15.1 / 37.3 / 3.1 / 5.8 / 1.6 / 49.4 | 15.8 / 44.2 / 3.3 / 6.5 / 1.6 / 51.7 | 14.5 / 36.9 / 4.3 / 52.1 / 1.5 / 44.5 |

The three contour routes are byte-identical in every quality row: same masks,
same router, and the 8 model blocks are filled by the same LaMa fallback.
Their only differences are which name the model was requested under and
timing noise. `rect-masks-default` is one of them (see below).

## What moved against the WP1 baselines (commit `9d179d6`)

Rows that moved because of the router, per route (`rtdetr-default` /
`rtdetr-lama` / `rect-masks-lama`):

- Residual ink **in mask (fill failure)**: 5.4% / 5.9% / 12.9% → **1.3% /
  1.3% / 1.0%** — the router's tiers are near-exact where they win.
- **Text-region MAE**: 11.57 / 11.71 / 16.29 → **5.04 / 5.04 / 2.41**.
- **Tone flattened (hole-in-tone)**: 44,541 / 40,056 / 120 px → **1,305 /
  1,305 / 163 px** (H2's white-fill flattening is gone; the remaining 1,305 px
  sit in `syn-tone-rotated`'s SFX block, filled by the model fallback, plus
  dot-edge phase slips of the rotated lattice — `rect-masks-lama`'s 163 is a
  small regression from 120 for the same phase-slip reason).
- **Over-erased px**: 8,709 / 8,616 / 16,888 → **908 / 908 / 707** — LaMa no
  longer repaints large visible areas away from the mask.
- **Residual after low-pass**: 36.4% / 33.7% / 1.4% → **8.6% / 8.6% / 1.4%**
  — the phase-mismatch smear on SFX is mostly gone.
- **Blocks to the model on the LaMa route**: 85 → **8**; `inpaint_model`
  89.3 s → ~6 s. On `rect-masks-lama`: 85 → 26, 128.1 s → 52.1 s.
- Ink-mask recall is unchanged everywhere (the router does not touch masks):
  84.8% / 84.8% / 98.7%.
- `seconds_per_page` (busy window): 8.2 / 12.8 / 20.8 → 8.4 / 10.2 / 14.0;
  the router adds ~0.2-0.4 s of CPU per page (`mask` stage) and removes most
  model calls. The WP1 numbers were measured in a calmer window.
- **Moved the wrong way — CJK residue (Step 6 / kana-kanji)**: 14.9% /
  16.1% / 12.6% → **21.8% / 21.8% / 27.6%**. Not leftover ink: every truth
  metric above improved, and per-block inspection of the flagged blocks shows
  either manga-ocr misreading short rendered English as kana (the documented
  noise floor) or reading through composite-parent boxes whose gaps were
  never covered by any child rect (a pre-existing mask gap, WP5's territory).
  Reproduced texture around the replacement text makes the re-OCR noisier
  than LaMa's smooth smear did. WP3 owns the Step-6 decision; the fill-side
  evidence (residual ink down everywhere) says the lettering itself is gone.

Synthetic pages, residual ink / visible over-erased px:

| Page | `rtdetr-default` | `rtdetr-lama` | `rect-masks-default` | `rect-masks-lama` |
| --- | --- | --- | --- | --- |
| `syn-white-bubbles` | 0.0% / 183 | 0.0% / 183 | 0.0% / 183 | 0.0% / 58 |
| `syn-conjoined-bubbles` | 0.0% / 84 | 0.0% / 84 | 0.0% / 84 | 0.0% / 60 |
| `syn-textured-narration` | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 |
| `syn-sfx` | 11.4% / 0 | 11.4% / 0 | 11.4% / 0 | 0.0% / 0 |
| `syn-webtoon-strip` | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 | 0.0% / 0 |
| `syn-tone-rotated` | 13.0% / 539 | 13.0% / 539 | 13.0% / 539 | 7.2% / 445 |
| `syn-faint-lettering` | 15.7% / 102 | 15.7% / 102 | 15.7% / 102 | 0.0% / 0 |
| `syn-text-touching-outline` | 21.8% / 0 | 21.8% / 0 | 21.8% / 0 | 10.1% / 144 |

Reading the adversarial pages: the remaining `syn-tone-rotated` residual
(13.0%) is almost entirely out-of-mask (its mask recall is unchanged);
`syn-faint-lettering` keeps its out-of-mask faint strokes; and
`syn-text-touching-outline` keeps the outline-crossing strokes — all three are
WP5's mask-miss half, untouched by design. The in-mask halves collapsed
(10.3% → ~0 on tone-rotated; 11.1% → 0 on faint; 0% stayed 0 on
touching-outline).

## rect-masks-default is now the contour route

Before WP4, the FLUX route's `--mask-mode rectangular` grew the white-fill
area around the glyphs (masked px 290,274 vs 194,315), which was its only
difference from `rtdetr-default`. The white path is gone, so the flag no
longer changes anything on the FLUX route: `rect-masks-default` is
`rtdetr-default`. The route is kept for continuity; a follow-up may redefine
it to build real rectangles on the FLUX route (that is a mask-semantics
decision, WP5's scope). `rect-masks-lama` still measures real rectangular
masks.

## What the tiers fixed (H1-H5 follow-up)

- **H2 (tone passes the white test) is fixed where it fired**: tone blocks
  now go to the lattice tier and come back in phase — hole-in-tone fell from
  44,541 px to 1,305 on the default route, and the rotated non-integer tone
  (`syn-tone-rotated`) reproduces within dot-edge phase slips (fill error
  mean ~3.7 levels on a synthetic patch).
- **The fill-failure half of H1 (SFX over hatching)**: `syn-sfx` residual
  fell 20.1% → 11.4% on the contour routes (lattice reproduces the
  hatching under the letters) and 40.4% → 0.0% on `rect-masks-lama`.
  The out-of-mask outline half remains for WP5.
- **The H4-rejected light-on-dark block** was all fill-side; it is now
  tier-1/0 filled (residual 12.0% → 0.0%, MAE 21.7 → 0.5 on that tag).

## Noise

- **Quality rows are deterministic.** The three contour routes scored
  byte-identical quality numbers, and two independent runs of the same route
  (the gate run and this baseline run, hours apart) matched on every count.
  The router is pure CPU numpy/OpenCV with no randomness.
- **Timings.** This window was degraded: `detect`, `ocr` and `verify` — code
  paths WP4 does not touch — read about 1.5-2x the WP1 baselines on the same
  work. Treat the timing rows as upper bounds. The one real timing change is
  the `mask` stage (+0.2-0.4 s/page of router CPU) against a large drop in
  `inpaint_model`. If `render` or `mask` moves another ~20% against these
  numbers on unchanged code, rerun before trusting it.
- **kana/kanji floor.** Still about 1 block per page of manga-ocr noise on
  clean pages, but the floor is higher around reproduced texture (see the
  CJK-residue note above): treat it as a Step-6 diagnostic, not a fill
  signal; check `residual ink` first, which is ground-truthed.
- **Private pages.** The three pages in `eval/pages/` are fixed; `--compare`
  between runs on this machine scopes itself to pages scored in both.
