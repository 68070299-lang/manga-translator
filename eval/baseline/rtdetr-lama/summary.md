# Pipeline eval: wp1-identity-rtdetr-lama

- Created: 2026-11-18T06:58:55Z, commit `9d179d644643`
- Detector `rtdetr`, mask mode `auto`, inpainter `lama_large` (used: `lama_large` x11), OCR `manga-ocr`, translator `stub`, device `cpu`
- ComfyUI reachable: False
- Pages: total 11, scored 11

## Overall

| Metric | Value |
| --- | --- |
| Pages scored | 11 |
| Blocks detected | 89 |
| Blocks to erase | 87 |
| CJK residue, Step 6 verdict | 15.7% |
| CJK residue, kana/kanji only | 16.1% |
| Px changed outside mask | 0 |
| Px inpainter changed outside its mask | 0 |
| Pages with inpainter fallback | 0 |
| Masks: contour-safe glyph | 85 |
| Masks: bubble interior | 0 |
| Masks: rectangular | 0 |
| Masks: skipped (no safe mask) | 0 |
| Masks: other (incl. oracle ink) | 0 |
| White-fill blocks | 0 |
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
| Synthetic: text-region MAE | 11.714 |
| Synthetic: residual ink ratio | 12.3% |
| Synthetic: ink-mask recall | 84.8% |
| Synthetic: residual ink in mask (fill failure) | 5.9% |
| Synthetic: residual ink outside mask (mask miss) | 6.5% |
| Synthetic: residual ink after low-pass | 33.7% |
| Synthetic: tone flattened in text boxes (px) | 40056 |
| Synthetic: rendered ink outside bubble interior (px) | 0 |
| Synthetic: OCR character error rate | 0.092 |
| Synthetic: over-erased px (visible) | 8616 |
| Synthetic: px touched away from text | 22161 |
| Seconds per page | 12.815 |
| Time: detect (s) | 8.698 |
| Time: ocr (s) | 17.604 |
| Time: translate (s) | 0.003 |
| Time: mask (s) | 0.204 |
| Time: inpaint_model (s) | 89.256 |
| Time: render (s) | 1.044 |
| Time: verify (s) | 24.087 |

## By tag

| Tag | Pages | Blocks | Residue (kana/kanji) | Px outside mask | Shrink | Overflow | Residual ink | Ink recall | Resid in->out mask |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adversarial | 3 | 10 | 20.0% | 0 | 0 | 0 | 22.2% | 76.0% | 8.2% -> 13.9% |
| bookwalker | 2 | 50 | 16.7% | 0 | 24 | 3 | n/a | n/a | n/a -> n/a |
| conjoined-bubbles | 3 | 52 | 16.0% | 0 | 24 | 3 | 0.0% | 100.0% | 0.0% -> 0.0% |
| horizontal-text | 5 | 17 | 17.6% | 0 | 0 | 0 | 15.7% | 85.6% | 7.4% -> 8.3% |
| light-on-dark | 1 | 3 | 0.0% | 0 | 0 | 0 | 12.2% | 100.0% | 12.2% -> 0.0% |
| narration | 3 | 21 | 9.5% | 0 | 8 | 1 | 13.9% | 83.6% | 11.2% -> 2.7% |
| sfx | 5 | 70 | 14.7% | 0 | 32 | 4 | 17.9% | 58.9% | 10.2% -> 7.7% |
| textured-background | 7 | 76 | 13.5% | 0 | 32 | 4 | 19.2% | 75.6% | 11.7% -> 7.5% |
| vertical-text | 9 | 83 | 16.1% | 0 | 32 | 4 | 13.1% | 88.0% | 6.2% -> 7.0% |
| webtoon-strip | 1 | 4 | 25.0% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| white-bubble | 8 | 80 | 17.9% | 0 | 32 | 4 | 10.2% | 92.2% | 2.9% -> 7.3% |

## Pages

| Page | Status | Blocks | Residue kana/kanji (Step 6) | Px outside mask | Masks glyph/interior/rect/skip +white | Shrink/overflow/unfit | Inpainter | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | ok | 4 | 1/4 (1/4) | 0 | 4/0/0/0 +0w | 0/0/0 | lama_large | 9.51 |
| syn-conjoined-bubbles | ok | 2 | 0/2 (0/2) | 0 | 2/0/0/0 +0w | 0/0/0 | lama_large | 6.841 |
| syn-textured-narration | ok | 3 | 0/3 (0/3) | 0 | 3/0/0/0 +0w | 0/0/0 | lama_large | 9.005 |
| syn-sfx | ok | 2 | 0/2 (0/2) | 0 | 2/0/0/0 +0w | 0/0/0 | lama_large | 6.488 |
| syn-webtoon-strip | ok | 4 | 1/4 (1/4) | 0 | 4/0/0/0 +0w | 0/0/0 | lama_large | 18.744 |
| syn-tone-rotated | ok | 4 | 0/4 (0/4) | 0 | 4/0/0/0 +0w | 0/0/0 | lama_large | 9.515 |
| syn-faint-lettering | ok | 3 | 0/3 (0/3) | 0 | 3/0/0/0 +0w | 0/0/0 | lama_large | 9.111 |
| syn-text-touching-outline | ok | 3 | 2/3 (2/3) | 0 | 3/0/0/0 +0w | 0/0/0 | lama_large | 9.192 |
| real-single-page-01 | ok | 14 | 2/14 (2/14) | 0 | 13/0/0/0 +0w | 8/1/0 | lama_large | 13.152 |
| real-bookwalker-spread-01 | ok | 25 | 8/23 (8/25) | 0 | 22/0/0/0 +0w | 14/2/0 | lama_large | 22.496 |
| real-bookwalker-spread-02 | ok | 25 | 0/25 (0/25) | 0 | 25/0/0/0 +0w | 10/1/0 | lama_large | 26.91 |
