# Pipeline eval: wp1-identity-rtdetr-default

- Created: 2026-11-18T06:54:48Z, commit `9d179d644643`
- Detector `rtdetr`, mask mode `auto`, inpainter `flux2_klein_lanpaint` (used: `flux2_klein_lanpaint` x6, `lama_large` x5), OCR `manga-ocr`, translator `stub`, device `cpu`
- ComfyUI reachable: False
- Pages: total 11, scored 11

## Overall

| Metric | Value |
| --- | --- |
| Pages scored | 11 |
| Blocks detected | 89 |
| Blocks to erase | 87 |
| CJK residue, Step 6 verdict | 14.6% |
| CJK residue, kana/kanji only | 14.9% |
| Px changed outside mask | 0 |
| Px inpainter changed outside its mask | 0 |
| Pages with inpainter fallback | 5 |
| Masks: contour-safe glyph | 85 |
| Masks: bubble interior | 0 |
| Masks: rectangular | 0 |
| Masks: skipped (no safe mask) | 0 |
| Masks: other (incl. oracle ink) | 0 |
| White-fill blocks | 78 |
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
| Synthetic: text-region MAE | 11.571 |
| Synthetic: residual ink ratio | 11.9% |
| Synthetic: ink-mask recall | 84.8% |
| Synthetic: residual ink in mask (fill failure) | 5.4% |
| Synthetic: residual ink outside mask (mask miss) | 6.5% |
| Synthetic: residual ink after low-pass | 36.4% |
| Synthetic: tone flattened in text boxes (px) | 44541 |
| Synthetic: rendered ink outside bubble interior (px) | 0 |
| Synthetic: OCR character error rate | 0.092 |
| Synthetic: over-erased px (visible) | 8709 |
| Synthetic: px touched away from text | 8709 |
| Seconds per page | 8.212 |
| Time: detect (s) | 9.495 |
| Time: ocr (s) | 17.192 |
| Time: translate (s) | 0.002 |
| Time: mask (s) | 0.269 |
| Time: inpaint_model (s) | 40.532 |
| Time: render (s) | 0.819 |
| Time: verify (s) | 21.968 |

## By tag

| Tag | Pages | Blocks | Residue (kana/kanji) | Px outside mask | Shrink | Overflow | Residual ink | Ink recall | Resid in->out mask |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adversarial | 3 | 10 | 20.0% | 0 | 0 | 0 | 21.1% | 76.0% | 7.1% -> 13.9% |
| bookwalker | 2 | 50 | 16.7% | 0 | 24 | 3 | n/a | n/a | n/a -> n/a |
| conjoined-bubbles | 3 | 52 | 16.0% | 0 | 24 | 3 | 0.0% | 100.0% | 0.0% -> 0.0% |
| horizontal-text | 5 | 17 | 17.6% | 0 | 0 | 0 | 15.0% | 85.6% | 6.7% -> 8.3% |
| light-on-dark | 1 | 3 | 0.0% | 0 | 0 | 0 | 12.0% | 100.0% | 12.0% -> 0.0% |
| narration | 3 | 21 | 4.8% | 0 | 8 | 1 | 13.8% | 83.6% | 11.1% -> 2.7% |
| sfx | 5 | 70 | 13.2% | 0 | 32 | 4 | 17.9% | 58.9% | 10.2% -> 7.7% |
| textured-background | 7 | 76 | 12.2% | 0 | 32 | 4 | 18.4% | 75.6% | 10.9% -> 7.5% |
| vertical-text | 9 | 83 | 14.8% | 0 | 32 | 4 | 12.5% | 88.0% | 5.6% -> 7.0% |
| webtoon-strip | 1 | 4 | 25.0% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| white-bubble | 8 | 80 | 16.7% | 0 | 32 | 4 | 9.5% | 92.2% | 2.2% -> 7.3% |

## Pages

| Page | Status | Blocks | Residue kana/kanji (Step 6) | Px outside mask | Masks glyph/interior/rect/skip +white | Shrink/overflow/unfit | Inpainter | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | ok | 4 | 1/4 (1/4) | 0 | 4/0/0/0 +4w | 0/0/0 | flux2_klein_lanpaint | 2.898 |
| syn-conjoined-bubbles | ok | 2 | 0/2 (0/2) | 0 | 2/0/0/0 +2w | 0/0/0 | flux2_klein_lanpaint | 2.202 |
| syn-textured-narration | ok | 3 | 0/3 (0/3) | 0 | 3/0/0/0 +2w | 0/0/0 | lama_large (fallback) | 9.84 |
| syn-sfx | ok | 2 | 0/2 (0/2) | 0 | 2/0/0/0 +0w | 0/0/0 | lama_large (fallback) | 7.013 |
| syn-webtoon-strip | ok | 4 | 1/4 (1/4) | 0 | 4/0/0/0 +4w | 0/0/0 | flux2_klein_lanpaint | 2.472 |
| syn-tone-rotated | ok | 4 | 0/4 (0/4) | 0 | 4/0/0/0 +4w | 0/0/0 | flux2_klein_lanpaint | 2.52 |
| syn-faint-lettering | ok | 3 | 0/3 (0/3) | 0 | 3/0/0/0 +3w | 0/0/0 | flux2_klein_lanpaint | 2.159 |
| syn-text-touching-outline | ok | 3 | 2/3 (2/3) | 0 | 3/0/0/0 +3w | 0/0/0 | flux2_klein_lanpaint | 2.135 |
| real-single-page-01 | ok | 14 | 1/14 (1/14) | 0 | 13/0/0/0 +11w | 8/1/0 | lama_large (fallback) | 12.968 |
| real-bookwalker-spread-01 | ok | 25 | 7/23 (7/25) | 0 | 22/0/0/0 +21w | 14/2/0 | lama_large (fallback) | 22.445 |
| real-bookwalker-spread-02 | ok | 25 | 1/25 (1/25) | 0 | 25/0/0/0 +24w | 10/1/0 | lama_large (fallback) | 23.681 |
