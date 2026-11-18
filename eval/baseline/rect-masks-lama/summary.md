# Pipeline eval: wp5-ink-masks-rect-masks-lama

- Created: 2026-11-18T12:07:36Z, commit `0c09aec693d5`
- Detector `rtdetr`, mask mode `rectangular`, inpainter `lama_large` (used: `lama_large` x11), OCR `manga-ocr`, translator `stub`, device `cpu`
- ComfyUI reachable: False
- Pages: total 11, scored 11

## Overall

| Metric | Value |
| --- | --- |
| Pages scored | 11 |
| Blocks detected | 89 |
| Blocks to erase | 87 |
| CJK residue, Step 6 verdict | 28.1% |
| CJK residue, kana/kanji only | 28.7% |
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
| Mask reason: flood leaked (open bubble) | n/a |
| Mask reason: no paper seed | n/a |
| Mask reason: flood fill too small | n/a |
| Mask reason: flood uncovered the text | n/a |
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
| Seconds per page | 8.891 |
| Time: detect (s) | 9.335 |
| Time: ocr (s) | 20.298 |
| Time: translate (s) | 0.005 |
| Time: mask (s) | 2.939 |
| Time: inpaint_model (s) | 38.343 |
| Time: render (s) | 2.046 |
| Time: verify (s) | 24.652 |

## By tag

| Tag | Pages | Blocks | Residue (kana/kanji) | Px outside mask | Shrink | Overflow | Residual ink | Ink recall | Resid in->out mask |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adversarial | 3 | 10 | 30.0% | 0 | 0 | 0 | 5.8% | 96.5% | 2.7% -> 3.1% |
| bookwalker | 2 | 50 | 27.1% | 0 | 24 | 3 | n/a | n/a | n/a -> n/a |
| conjoined-bubbles | 3 | 52 | 26.0% | 0 | 24 | 3 | 0.0% | 100.0% | 0.0% -> 0.0% |
| horizontal-text | 5 | 17 | 23.5% | 0 | 0 | 0 | 3.5% | 97.9% | 1.6% -> 1.8% |
| light-on-dark | 1 | 3 | 33.3% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| narration | 3 | 21 | 33.3% | 0 | 8 | 1 | 3.6% | 100.0% | 3.6% -> 0.0% |
| sfx | 5 | 70 | 27.9% | 0 | 32 | 4 | 3.6% | 100.0% | 3.6% -> 0.0% |
| textured-background | 7 | 76 | 28.4% | 0 | 32 | 4 | 1.8% | 100.0% | 1.8% -> 0.0% |
| vertical-text | 9 | 83 | 30.9% | 0 | 32 | 4 | 2.9% | 98.3% | 1.3% -> 1.5% |
| webtoon-strip | 1 | 4 | 0.0% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| white-bubble | 8 | 80 | 30.8% | 0 | 32 | 4 | 2.0% | 97.9% | 0.2% -> 1.8% |

## Pages

| Page | Status | Blocks | Residue kana/kanji (Step 6) | Px outside mask | Masks glyph/interior/rect/skip +router | Shrink/overflow/unfit | Inpainter | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | ok | 4 | 2/4 (2/4) | 0 | 0/0/4/0 +2r | 0/0/0 | lama_large | 8.181 |
| syn-conjoined-bubbles | ok | 2 | 0/2 (0/2) | 0 | 0/0/2/0 +2r | 0/0/0 | lama_large | 1.763 |
| syn-textured-narration | ok | 3 | 1/3 (1/3) | 0 | 0/0/3/0 +3r | 0/0/0 | lama_large | 2.155 |
| syn-sfx | ok | 2 | 0/2 (0/2) | 0 | 0/0/2/0 +2r | 0/0/0 | lama_large | 1.855 |
| syn-webtoon-strip | ok | 4 | 0/4 (0/4) | 0 | 0/0/4/0 +4r | 0/0/0 | lama_large | 2.817 |
| syn-tone-rotated | ok | 4 | 0/4 (0/4) | 0 | 0/0/4/0 +4r | 0/0/0 | lama_large | 2.575 |
| syn-faint-lettering | ok | 3 | 1/3 (1/3) | 0 | 0/0/3/0 +3r | 0/0/0 | lama_large | 2.609 |
| syn-text-touching-outline | ok | 3 | 2/3 (2/3) | 0 | 0/0/3/0 +1r | 0/0/0 | lama_large | 9.3 |
| real-single-page-01 | ok | 14 | 6/14 (6/14) | 0 | 0/0/13/0 +9r | 8/1/0 | lama_large | 12.567 |
| real-bookwalker-spread-01 | ok | 25 | 7/23 (7/25) | 0 | 0/0/22/0 +13r | 14/2/0 | lama_large | 21.858 |
| real-bookwalker-spread-02 | ok | 25 | 6/25 (6/25) | 0 | 0/0/25/0 +16r | 10/1/0 | lama_large | 32.122 |
