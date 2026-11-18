# Pipeline eval: wp1-identity-rect-masks-default

- Created: 2026-11-18T07:17:24Z, commit `9d179d644643`
- Detector `rtdetr`, mask mode `rectangular`, inpainter `flux2_klein_lanpaint` (used: `flux2_klein_lanpaint` x6, `lama_large` x5), OCR `manga-ocr`, translator `stub`, device `cpu`
- ComfyUI reachable: False
- Pages: total 11, scored 11

## Overall

| Metric | Value |
| --- | --- |
| Pages scored | 11 |
| Blocks detected | 89 |
| Blocks to erase | 87 |
| CJK residue, Step 6 verdict | 13.5% |
| CJK residue, kana/kanji only | 13.8% |
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
| Masked px | 290274 |
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
| Synthetic: text-region MAE | 11.438 |
| Synthetic: residual ink ratio | 10.3% |
| Synthetic: ink-mask recall | 88.7% |
| Synthetic: residual ink in mask (fill failure) | 6.0% |
| Synthetic: residual ink outside mask (mask miss) | 4.4% |
| Synthetic: residual ink after low-pass | 38.2% |
| Synthetic: tone flattened in text boxes (px) | 46138 |
| Synthetic: rendered ink outside bubble interior (px) | 0 |
| Synthetic: OCR character error rate | 0.092 |
| Synthetic: over-erased px (visible) | 8709 |
| Synthetic: px touched away from text | 8709 |
| Seconds per page | 8.115 |
| Time: detect (s) | 9.689 |
| Time: ocr (s) | 16.801 |
| Time: translate (s) | 0.004 |
| Time: mask (s) | 0.278 |
| Time: inpaint_model (s) | 40.325 |
| Time: render (s) | 0.826 |
| Time: verify (s) | 21.273 |

## By tag

| Tag | Pages | Blocks | Residue (kana/kanji) | Px outside mask | Shrink | Overflow | Residual ink | Ink recall | Resid in->out mask |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adversarial | 3 | 10 | 20.0% | 0 | 0 | 0 | 16.9% | 86.3% | 8.6% -> 8.3% |
| bookwalker | 2 | 50 | 12.5% | 0 | 24 | 3 | n/a | n/a | n/a -> n/a |
| conjoined-bubbles | 3 | 52 | 12.0% | 0 | 24 | 3 | 0.0% | 100.0% | 0.0% -> 0.0% |
| horizontal-text | 5 | 17 | 17.6% | 0 | 0 | 0 | 12.5% | 91.8% | 7.6% -> 5.0% |
| light-on-dark | 1 | 3 | 0.0% | 0 | 0 | 0 | 12.0% | 100.0% | 12.0% -> 0.0% |
| narration | 3 | 21 | 9.5% | 0 | 8 | 1 | 13.8% | 91.3% | 12.3% -> 1.5% |
| sfx | 5 | 70 | 11.8% | 0 | 32 | 4 | 17.9% | 66.6% | 11.3% -> 6.6% |
| textured-background | 7 | 76 | 10.8% | 0 | 32 | 4 | 15.3% | 83.3% | 12.0% -> 3.3% |
| vertical-text | 9 | 83 | 13.6% | 0 | 32 | 4 | 10.4% | 93.1% | 6.3% -> 4.1% |
| webtoon-strip | 1 | 4 | 25.0% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| white-bubble | 8 | 80 | 15.4% | 0 | 32 | 4 | 7.0% | 95.3% | 2.6% -> 4.3% |

## Pages

| Page | Status | Blocks | Residue kana/kanji (Step 6) | Px outside mask | Masks glyph/interior/rect/skip +white | Shrink/overflow/unfit | Inpainter | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | ok | 4 | 1/4 (1/4) | 0 | 4/0/0/0 +4w | 0/0/0 | flux2_klein_lanpaint | 2.485 |
| syn-conjoined-bubbles | ok | 2 | 0/2 (0/2) | 0 | 2/0/0/0 +2w | 0/0/0 | flux2_klein_lanpaint | 1.894 |
| syn-textured-narration | ok | 3 | 0/3 (0/3) | 0 | 3/0/0/0 +2w | 0/0/0 | lama_large (fallback) | 10.098 |
| syn-sfx | ok | 2 | 0/2 (0/2) | 0 | 2/0/0/0 +0w | 0/0/0 | lama_large (fallback) | 6.797 |
| syn-webtoon-strip | ok | 4 | 1/4 (1/4) | 0 | 4/0/0/0 +4w | 0/0/0 | flux2_klein_lanpaint | 2.667 |
| syn-tone-rotated | ok | 4 | 0/4 (0/4) | 0 | 4/0/0/0 +4w | 0/0/0 | flux2_klein_lanpaint | 2.361 |
| syn-faint-lettering | ok | 3 | 0/3 (0/3) | 0 | 3/0/0/0 +3w | 0/0/0 | flux2_klein_lanpaint | 1.993 |
| syn-text-touching-outline | ok | 3 | 2/3 (2/3) | 0 | 3/0/0/0 +3w | 0/0/0 | flux2_klein_lanpaint | 2.319 |
| real-single-page-01 | ok | 14 | 2/14 (2/14) | 0 | 13/0/0/0 +11w | 8/1/0 | lama_large (fallback) | 12.877 |
| real-bookwalker-spread-01 | ok | 25 | 5/23 (5/25) | 0 | 22/0/0/0 +21w | 14/2/0 | lama_large (fallback) | 22.684 |
| real-bookwalker-spread-02 | ok | 25 | 1/25 (1/25) | 0 | 25/0/0/0 +24w | 10/1/0 | lama_large (fallback) | 23.087 |
