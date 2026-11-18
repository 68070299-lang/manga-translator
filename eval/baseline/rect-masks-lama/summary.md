# Pipeline eval: wp1-identity-rect-masks-lama

- Created: 2026-11-18T07:23:49Z, commit `9d179d644643`
- Detector `rtdetr`, mask mode `rectangular`, inpainter `lama_large` (used: `lama_large` x11), OCR `manga-ocr`, translator `stub`, device `cpu`
- ComfyUI reachable: False
- Pages: total 11, scored 11

## Overall

| Metric | Value |
| --- | --- |
| Pages scored | 11 |
| Blocks detected | 89 |
| Blocks to erase | 87 |
| CJK residue, Step 6 verdict | 12.4% |
| CJK residue, kana/kanji only | 12.6% |
| Px changed outside mask | 0 |
| Px inpainter changed outside its mask | 0 |
| Pages with inpainter fallback | 0 |
| Masks: contour-safe glyph | 0 |
| Masks: bubble interior | 0 |
| Masks: rectangular | 85 |
| Masks: skipped (no safe mask) | 0 |
| Masks: other (incl. oracle ink) | 0 |
| White-fill blocks | 0 |
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
| Synthetic: text-region MAE | 16.29 |
| Synthetic: residual ink ratio | 14.1% |
| Synthetic: ink-mask recall | 98.7% |
| Synthetic: residual ink in mask (fill failure) | 12.9% |
| Synthetic: residual ink outside mask (mask miss) | 1.2% |
| Synthetic: residual ink after low-pass | 1.4% |
| Synthetic: tone flattened in text boxes (px) | 120 |
| Synthetic: rendered ink outside bubble interior (px) | 0 |
| Synthetic: OCR character error rate | 0.092 |
| Synthetic: over-erased px (visible) | 16888 |
| Synthetic: px touched away from text | 288495 |
| Seconds per page | 20.77 |
| Time: detect (s) | 11.937 |
| Time: ocr (s) | 34.83 |
| Time: translate (s) | 0.005 |
| Time: mask (s) | 0.014 |
| Time: inpaint_model (s) | 128.144 |
| Time: render (s) | 1.762 |
| Time: verify (s) | 51.686 |

## By tag

| Tag | Pages | Blocks | Residue (kana/kanji) | Px outside mask | Shrink | Overflow | Residual ink | Ink recall | Resid in->out mask |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adversarial | 3 | 10 | 20.0% | 0 | 0 | 0 | 18.8% | 96.5% | 15.7% -> 3.1% |
| bookwalker | 2 | 50 | 6.2% | 0 | 24 | 3 | n/a | n/a | n/a -> n/a |
| conjoined-bubbles | 3 | 52 | 6.0% | 0 | 24 | 3 | 0.0% | 100.0% | 0.0% -> 0.0% |
| horizontal-text | 5 | 17 | 23.5% | 0 | 0 | 0 | 14.5% | 97.9% | 12.6% -> 1.8% |
| light-on-dark | 1 | 3 | 33.3% | 0 | 0 | 0 | 16.1% | 100.0% | 16.1% -> 0.0% |
| narration | 3 | 21 | 19.1% | 0 | 8 | 1 | 21.9% | 100.0% | 21.9% -> 0.0% |
| sfx | 5 | 70 | 8.8% | 0 | 32 | 4 | 34.0% | 100.0% | 34.0% -> 0.0% |
| textured-background | 7 | 76 | 10.8% | 0 | 32 | 4 | 25.6% | 100.0% | 25.6% -> 0.0% |
| vertical-text | 9 | 83 | 12.3% | 0 | 32 | 4 | 12.1% | 98.3% | 10.5% -> 1.5% |
| webtoon-strip | 1 | 4 | 25.0% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| white-bubble | 8 | 80 | 12.8% | 0 | 32 | 4 | 5.7% | 97.9% | 3.9% -> 1.8% |

## Pages

| Page | Status | Blocks | Residue kana/kanji (Step 6) | Px outside mask | Masks glyph/interior/rect/skip +white | Shrink/overflow/unfit | Inpainter | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | ok | 4 | 1/4 (1/4) | 0 | 0/0/4/0 +0w | 0/0/0 | lama_large | 10.882 |
| syn-conjoined-bubbles | ok | 2 | 0/2 (0/2) | 0 | 0/0/2/0 +0w | 0/0/0 | lama_large | 7.793 |
| syn-textured-narration | ok | 3 | 1/3 (1/3) | 0 | 0/0/3/0 +0w | 0/0/0 | lama_large | 10.211 |
| syn-sfx | ok | 2 | 0/2 (0/2) | 0 | 0/0/2/0 +0w | 0/0/0 | lama_large | 7.57 |
| syn-webtoon-strip | ok | 4 | 1/4 (1/4) | 0 | 0/0/4/0 +0w | 0/0/0 | lama_large | 23.131 |
| syn-tone-rotated | ok | 4 | 0/4 (0/4) | 0 | 0/0/4/0 +0w | 0/0/0 | lama_large | 14.459 |
| syn-faint-lettering | ok | 3 | 1/3 (1/3) | 0 | 0/0/3/0 +0w | 0/0/0 | lama_large | 11.548 |
| syn-text-touching-outline | ok | 3 | 1/3 (1/3) | 0 | 0/0/3/0 +0w | 0/0/0 | lama_large | 11.512 |
| real-single-page-01 | ok | 14 | 3/14 (3/14) | 0 | 0/0/13/0 +0w | 8/1/0 | lama_large | 28.392 |
| real-bookwalker-spread-01 | ok | 25 | 3/23 (3/25) | 0 | 0/0/22/0 +0w | 14/2/0 | lama_large | 46.107 |
| real-bookwalker-spread-02 | ok | 25 | 0/25 (0/25) | 0 | 0/0/25/0 +0w | 10/1/0 | lama_large | 56.868 |
