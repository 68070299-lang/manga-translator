# Pipeline eval: wp4-fill-router-rect-masks-default

- Created: 2026-11-18T09:19:20Z, commit `f048e045e079`
- Detector `rtdetr`, mask mode `rectangular`, inpainter `flux2_klein_lanpaint` (used: `lama_large` x4, `flux2_klein_lanpaint` x7), OCR `manga-ocr`, translator `stub`, device `cpu`
- ComfyUI reachable: False
- Pages: total 11, scored 11

## Overall

| Metric | Value |
| --- | --- |
| Pages scored | 11 |
| Blocks detected | 89 |
| Blocks to erase | 87 |
| CJK residue, Step 6 verdict | 21.3% |
| CJK residue, kana/kanji only | 21.8% |
| Px changed outside mask | 0 |
| Px inpainter changed outside its mask | 0 |
| Pages with inpainter fallback | 4 |
| Masks: contour-safe glyph | 85 |
| Masks: bubble interior | 0 |
| Masks: rectangular | 0 |
| Masks: skipped (no safe mask) | 0 |
| Masks: other (incl. oracle ink) | 0 |
| Router-filled blocks (no model) | 77 |
| Blocks queued to the model | 8 |
| Fill tier: flat blocks | 39 |
| Fill tier: smooth blocks | 1 |
| Fill tier: lattice blocks | 37 |
| Composite duplicates not masked | 2 |
| Masked px | 194315 |
| Mask reason: text_free block | 22 |
| Mask reason: no contour found | 39 |
| Mask reason: contour touches ROI edge | n/a |
| Mask reason: area test failed | n/a |
| Mask reason: coverage below 35% | n/a |
| Mask reason: interior found, clipped ink empty | n/a |
| Render regions | 85 |
| Shrink events (< preferred size) | 32 |
| Below min readable size | 6 |
| Overflow events | 4 |
| Unfit regions skipped | 0 |
| Median font size | 16 |
| Font size spread per page (mean) | 2.546 |
| Synthetic: detection recall | 100.0% |
| Synthetic: text-region MAE | 5.037 |
| Synthetic: residual ink ratio | 7.7% |
| Synthetic: ink-mask recall | 84.8% |
| Synthetic: residual ink in mask (fill failure) | 1.3% |
| Synthetic: residual ink outside mask (mask miss) | 6.5% |
| Synthetic: residual ink after low-pass | 8.6% |
| Synthetic: tone flattened in text boxes (px) | 1305 |
| Synthetic: rendered ink outside bubble interior (px) | 0 |
| Synthetic: OCR character error rate | 0.092 |
| Synthetic: over-erased px (visible) | 908 |
| Synthetic: px touched away from text | 1035 |
| Seconds per page | 11.214 |
| Time: detect (s) | 15.755 |
| Time: ocr (s) | 44.184 |
| Time: translate (s) | 0.006 |
| Time: mask (s) | 3.284 |
| Time: inpaint_model (s) | 6.525 |
| Time: render (s) | 1.613 |
| Time: verify (s) | 51.726 |

## By tag

| Tag | Pages | Blocks | Residue (kana/kanji) | Px outside mask | Shrink | Overflow | Residual ink | Ink recall | Resid in->out mask |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adversarial | 3 | 10 | 30.0% | 0 | 0 | 0 | 16.8% | 76.0% | 2.9% -> 13.9% |
| bookwalker | 2 | 50 | 16.7% | 0 | 24 | 3 | n/a | n/a | n/a -> n/a |
| conjoined-bubbles | 3 | 52 | 16.0% | 0 | 24 | 3 | 0.0% | 100.0% | 0.0% -> 0.0% |
| horizontal-text | 5 | 17 | 29.4% | 0 | 0 | 0 | 10.1% | 85.6% | 1.8% -> 8.3% |
| light-on-dark | 1 | 3 | 33.3% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| narration | 3 | 21 | 19.1% | 0 | 8 | 1 | 6.5% | 83.6% | 3.8% -> 2.7% |
| sfx | 5 | 70 | 19.1% | 0 | 32 | 4 | 12.2% | 58.9% | 4.5% -> 7.7% |
| textured-background | 7 | 76 | 20.3% | 0 | 32 | 4 | 10.0% | 75.6% | 2.5% -> 7.5% |
| vertical-text | 9 | 83 | 19.8% | 0 | 32 | 4 | 8.4% | 88.0% | 1.5% -> 7.0% |
| webtoon-strip | 1 | 4 | 25.0% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| white-bubble | 8 | 80 | 20.5% | 0 | 32 | 4 | 7.5% | 92.2% | 0.2% -> 7.3% |

## Pages

| Page | Status | Blocks | Residue kana/kanji (Step 6) | Px outside mask | Masks glyph/interior/rect/skip +router | Shrink/overflow/unfit | Inpainter | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | ok | 4 | 1/4 (1/4) | 0 | 4/0/0/0 +3r | 0/0/0 | lama_large (fallback) | 6.187 |
| syn-conjoined-bubbles | ok | 2 | 0/2 (0/2) | 0 | 2/0/0/0 +2r | 0/0/0 | flux2_klein_lanpaint | 2.64 |
| syn-textured-narration | ok | 3 | 1/3 (1/3) | 0 | 3/0/0/0 +3r | 0/0/0 | flux2_klein_lanpaint | 4.171 |
| syn-sfx | ok | 2 | 2/2 (2/2) | 0 | 2/0/0/0 +2r | 0/0/0 | flux2_klein_lanpaint | 4.375 |
| syn-webtoon-strip | ok | 4 | 1/4 (1/4) | 0 | 4/0/0/0 +4r | 0/0/0 | flux2_klein_lanpaint | 6.604 |
| syn-tone-rotated | ok | 4 | 0/4 (0/4) | 0 | 4/0/0/0 +4r | 0/0/0 | flux2_klein_lanpaint | 6.916 |
| syn-faint-lettering | ok | 3 | 1/3 (1/3) | 0 | 3/0/0/0 +3r | 0/0/0 | flux2_klein_lanpaint | 5.835 |
| syn-text-touching-outline | ok | 3 | 2/3 (2/3) | 0 | 3/0/0/0 +3r | 0/0/0 | flux2_klein_lanpaint | 5.977 |
| real-single-page-01 | ok | 14 | 3/14 (3/14) | 0 | 13/0/0/0 +12r | 8/1/0 | lama_large (fallback) | 26.255 |
| real-bookwalker-spread-01 | ok | 25 | 6/23 (6/25) | 0 | 22/0/0/0 +18r | 14/2/0 | lama_large (fallback) | 28.839 |
| real-bookwalker-spread-02 | ok | 25 | 2/25 (2/25) | 0 | 25/0/0/0 +23r | 10/1/0 | lama_large (fallback) | 25.552 |
