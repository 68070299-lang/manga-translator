# Pipeline eval: base-rtdetr-lama

- Created: 2026-11-18T14:47:32Z, commit `f36508215496`
- Detector `rtdetr`, mask mode `auto`, inpainter `lama_large` (used: `lama_large` x11), OCR `manga-ocr`, translator `stub`, device `cpu`
- ComfyUI reachable: False
- Pages: total 11, scored 11

## Overall

| Metric | Value |
| --- | --- |
| Pages scored | 11 |
| Blocks detected | 89 |
| Blocks to erase | 87 |
| CJK residue, Step 6 verdict | 16.9% |
| CJK residue, kana/kanji only | 17.2% |
| Px changed outside mask | 0 |
| Px inpainter changed outside its mask | 0 |
| Pages with inpainter fallback | 0 |
| Masks: contour-safe glyph | 85 |
| Masks: bubble interior | 0 |
| Masks: rectangular | 0 |
| Masks: skipped (no safe mask) | 0 |
| Masks: other (incl. oracle ink) | 0 |
| Router-filled blocks (no model) | 82 |
| Blocks queued to the model | 3 |
| Fill tier: flat blocks | 44 |
| Fill tier: smooth blocks | 1 |
| Fill tier: lattice blocks | 37 |
| Composite duplicates not masked | 2 |
| Masked px | 169214 |
| Mask reason: text_free block | 20 |
| Mask reason: flood leaked (open bubble) | 35 |
| Mask reason: no paper seed | n/a |
| Mask reason: flood fill too small | n/a |
| Mask reason: flood uncovered the text | n/a |
| Mask reason: no contour found | n/a |
| Mask reason: contour touches ROI edge | n/a |
| Mask reason: area test failed | n/a |
| Mask reason: coverage below 35% | n/a |
| Mask reason: interior found, clipped ink empty | n/a |
| Render regions | 85 |
| Shrink events (< anchored target) | 27 |
| Below min readable size | 6 |
| Overflow events | 0 |
| Unfit regions skipped | 0 |
| Median font size | 16 |
| Font size spread per page (mean) | 2.182 |
| Dialogue font size spread per page (mean) | 1.8 |
| Synthetic: detection recall | 100.0% |
| Synthetic: text-region MAE | 1.358 |
| Synthetic: residual ink ratio | 1.8% |
| Synthetic: ink-mask recall | 98.0% |
| Synthetic: residual ink in mask (fill failure) | 0.5% |
| Synthetic: residual ink outside mask (mask miss) | 1.4% |
| Synthetic: residual ink after low-pass | 1.8% |
| Synthetic: tone flattened in text boxes (px) | 684 |
| Synthetic: rendered ink outside bubble interior (px) | 0 |
| Synthetic: OCR character error rate | 0.092 |
| Synthetic: over-erased px (visible) | 364 |
| Synthetic: px touched away from text | 4068 |
| Seconds per page | 4.978 |
| Time: detect (s) | 8.392 |
| Time: ocr (s) | 16.066 |
| Time: translate (s) | 0.001 |
| Time: mask (s) | 3.936 |
| Time: inpaint_model (s) | 1.271 |
| Time: render (s) | 4.082 |
| Time: verify (s) | 20.296 |

## By tag

| Tag | Pages | Blocks | Residue (kana/kanji) | Px outside mask | Shrink | Overflow | Residual ink | Ink recall | Resid in->out mask |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adversarial | 3 | 10 | 20.0% | 0 | 0 | 0 | 4.5% | 96.2% | 1.3% -> 3.2% |
| bookwalker | 2 | 50 | 16.7% | 0 | 21 | 0 | n/a | n/a | n/a -> n/a |
| conjoined-bubbles | 3 | 52 | 16.0% | 0 | 21 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| horizontal-text | 5 | 17 | 11.8% | 0 | 0 | 0 | 2.7% | 97.7% | 0.8% -> 1.9% |
| light-on-dark | 1 | 3 | 0.0% | 0 | 0 | 0 | 0.1% | 100.0% | 0.0% -> 0.1% |
| narration | 3 | 21 | 23.8% | 0 | 6 | 0 | 1.3% | 99.5% | 1.1% -> 0.1% |
| sfx | 5 | 70 | 19.1% | 0 | 27 | 0 | 1.8% | 97.4% | 1.1% -> 0.7% |
| textured-background | 7 | 76 | 20.3% | 0 | 27 | 0 | 1.1% | 98.7% | 0.8% -> 0.4% |
| vertical-text | 9 | 83 | 18.5% | 0 | 27 | 0 | 2.3% | 98.1% | 0.7% -> 1.6% |
| webtoon-strip | 1 | 4 | 0.0% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| white-bubble | 8 | 80 | 19.2% | 0 | 27 | 0 | 2.2% | 97.9% | 0.3% -> 1.9% |

## Pages

| Page | Status | Blocks | Residue kana/kanji (Step 6) | Px outside mask | Masks glyph/interior/rect/skip +router | Shrink/overflow/unfit | Inpainter | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | ok | 4 | 0/4 (0/4) | 0 | 4/0/0/0 +4r | 0/0/0 | lama_large | 2.877 |
| syn-conjoined-bubbles | ok | 2 | 0/2 (0/2) | 0 | 2/0/0/0 +2r | 0/0/0 | lama_large | 2.428 |
| syn-textured-narration | ok | 3 | 0/3 (0/3) | 0 | 3/0/0/0 +3r | 0/0/0 | lama_large | 2.204 |
| syn-sfx | ok | 2 | 0/2 (0/2) | 0 | 2/0/0/0 +2r | 0/0/0 | lama_large | 1.81 |
| syn-webtoon-strip | ok | 4 | 0/4 (0/4) | 0 | 4/0/0/0 +4r | 0/0/0 | lama_large | 2.609 |
| syn-tone-rotated | ok | 4 | 0/4 (0/4) | 0 | 4/0/0/0 +4r | 0/0/0 | lama_large | 2.561 |
| syn-faint-lettering | ok | 3 | 2/3 (2/3) | 0 | 3/0/0/0 +3r | 0/0/0 | lama_large | 2.403 |
| syn-text-touching-outline | ok | 3 | 0/3 (0/3) | 0 | 3/0/0/0 +3r | 0/0/0 | lama_large | 2.413 |
| real-single-page-01 | ok | 14 | 5/14 (5/14) | 0 | 13/0/0/0 +13r | 6/0/0 | lama_large | 8.363 |
| real-bookwalker-spread-01 | ok | 25 | 5/23 (5/25) | 0 | 22/0/0/0 +21r | 14/0/0 | lama_large | 13.177 |
| real-bookwalker-spread-02 | ok | 25 | 3/25 (3/25) | 0 | 25/0/0/0 +23r | 7/0/0 | lama_large | 13.912 |
