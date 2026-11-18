# Pipeline eval: wp4-fill-router-rect-masks-lama

- Created: 2026-11-18T09:22:32Z, commit `f048e045e079`
- Detector `rtdetr`, mask mode `rectangular`, inpainter `lama_large` (used: `lama_large` x11), OCR `manga-ocr`, translator `stub`, device `cpu`
- ComfyUI reachable: False
- Pages: total 11, scored 11

## Overall

| Metric | Value |
| --- | --- |
| Pages scored | 11 |
| Blocks detected | 89 |
| Blocks to erase | 87 |
| CJK residue, Step 6 verdict | 27.0% |
| CJK residue, kana/kanji only | 27.6% |
| Px changed outside mask | 0 |
| Px inpainter changed outside its mask | 0 |
| Pages with inpainter fallback | 0 |
| Masks: contour-safe glyph | 0 |
| Masks: bubble interior | 0 |
| Masks: rectangular | 85 |
| Masks: skipped (no safe mask) | 0 |
| Masks: other (incl. oracle ink) | 0 |
| Router-filled blocks (no model) | 59 |
| Blocks queued to the model | 26 |
| Fill tier: flat blocks | 16 |
| Fill tier: smooth blocks | 1 |
| Fill tier: lattice blocks | 42 |
| Composite duplicates not masked | 2 |
| Masked px | 914494 |
| Mask reason: text_free block | n/a |
| Mask reason: no contour found | n/a |
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
| Synthetic: text-region MAE | 2.413 |
| Synthetic: residual ink ratio | 2.2% |
| Synthetic: ink-mask recall | 98.7% |
| Synthetic: residual ink in mask (fill failure) | 1.0% |
| Synthetic: residual ink outside mask (mask miss) | 1.2% |
| Synthetic: residual ink after low-pass | 1.4% |
| Synthetic: tone flattened in text boxes (px) | 163 |
| Synthetic: rendered ink outside bubble interior (px) | 0 |
| Synthetic: OCR character error rate | 0.092 |
| Synthetic: over-erased px (visible) | 707 |
| Synthetic: px touched away from text | 71455 |
| Seconds per page | 14.008 |
| Time: detect (s) | 14.488 |
| Time: ocr (s) | 36.897 |
| Time: translate (s) | 0.007 |
| Time: mask (s) | 4.319 |
| Time: inpaint_model (s) | 52.114 |
| Time: render (s) | 1.548 |
| Time: verify (s) | 44.459 |

## By tag

| Tag | Pages | Blocks | Residue (kana/kanji) | Px outside mask | Shrink | Overflow | Residual ink | Ink recall | Resid in->out mask |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adversarial | 3 | 10 | 20.0% | 0 | 0 | 0 | 5.8% | 96.5% | 2.7% -> 3.1% |
| bookwalker | 2 | 50 | 27.1% | 0 | 24 | 3 | n/a | n/a | n/a -> n/a |
| conjoined-bubbles | 3 | 52 | 26.0% | 0 | 24 | 3 | 0.0% | 100.0% | 0.0% -> 0.0% |
| horizontal-text | 5 | 17 | 23.5% | 0 | 0 | 0 | 3.5% | 97.9% | 1.6% -> 1.8% |
| light-on-dark | 1 | 3 | 33.3% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| narration | 3 | 21 | 33.3% | 0 | 8 | 1 | 3.6% | 100.0% | 3.6% -> 0.0% |
| sfx | 5 | 70 | 27.9% | 0 | 32 | 4 | 3.6% | 100.0% | 3.6% -> 0.0% |
| textured-background | 7 | 76 | 28.4% | 0 | 32 | 4 | 1.8% | 100.0% | 1.8% -> 0.0% |
| vertical-text | 9 | 83 | 28.4% | 0 | 32 | 4 | 2.9% | 98.3% | 1.3% -> 1.5% |
| webtoon-strip | 1 | 4 | 25.0% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| white-bubble | 8 | 80 | 29.5% | 0 | 32 | 4 | 2.0% | 97.9% | 0.2% -> 1.8% |

## Pages

| Page | Status | Blocks | Residue kana/kanji (Step 6) | Px outside mask | Masks glyph/interior/rect/skip +router | Shrink/overflow/unfit | Inpainter | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | ok | 4 | 1/4 (1/4) | 0 | 0/0/4/0 +2r | 0/0/0 | lama_large | 13.706 |
| syn-conjoined-bubbles | ok | 2 | 0/2 (0/2) | 0 | 0/0/2/0 +2r | 0/0/0 | lama_large | 3.455 |
| syn-textured-narration | ok | 3 | 1/3 (1/3) | 0 | 0/0/3/0 +3r | 0/0/0 | lama_large | 3.952 |
| syn-sfx | ok | 2 | 0/2 (0/2) | 0 | 0/0/2/0 +2r | 0/0/0 | lama_large | 3.181 |
| syn-webtoon-strip | ok | 4 | 1/4 (1/4) | 0 | 0/0/4/0 +4r | 0/0/0 | lama_large | 5.097 |
| syn-tone-rotated | ok | 4 | 0/4 (0/4) | 0 | 0/0/4/0 +4r | 0/0/0 | lama_large | 5.199 |
| syn-faint-lettering | ok | 3 | 1/3 (1/3) | 0 | 0/0/3/0 +3r | 0/0/0 | lama_large | 5.035 |
| syn-text-touching-outline | ok | 3 | 1/3 (1/3) | 0 | 0/0/3/0 +1r | 0/0/0 | lama_large | 13.304 |
| real-single-page-01 | ok | 14 | 6/14 (6/14) | 0 | 0/0/13/0 +9r | 8/1/0 | lama_large | 23.411 |
| real-bookwalker-spread-01 | ok | 25 | 7/23 (7/25) | 0 | 0/0/22/0 +13r | 14/2/0 | lama_large | 34.122 |
| real-bookwalker-spread-02 | ok | 25 | 6/25 (6/25) | 0 | 0/0/25/0 +16r | 10/1/0 | lama_large | 43.627 |
