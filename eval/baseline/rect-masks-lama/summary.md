# Pipeline eval: rect-masks-lama

- Created: 2026-11-18T03:41:25Z, commit `1b0b4cfe4700`
- Detector `rtdetr`, mask mode `rectangular`, inpainter `lama_large` (used: `lama_large` x8), OCR `manga-ocr`, translator `stub`, device `cpu`
- ComfyUI reachable: False
- Pages: total 8, scored 8

## Overall

| Metric | Value |
| --- | --- |
| Pages scored | 8 |
| Blocks detected | 79 |
| Blocks to erase | 77 |
| CJK residue, Step 6 verdict | 98.7% |
| CJK residue, kana/kanji only | 11.7% |
| Px changed outside mask | 0 |
| Px inpainter changed outside its mask | 0 |
| Pages with inpainter fallback | 0 |
| Masks: contour-safe glyph | 0 |
| Masks: bubble interior | 0 |
| Masks: rectangular | 75 |
| Masks: skipped (no safe mask) | 0 |
| White-fill blocks | 0 |
| Composite duplicates not masked | 2 |
| Masked px | 718173 |
| Render regions | 75 |
| Shrink events (< preferred size) | 32 |
| Below min readable size | 6 |
| Overflow events | 4 |
| Unfit regions skipped | 0 |
| Median font size | 16 |
| Synthetic: detection recall | 100.0% |
| Synthetic: text-region MAE | 13.441 |
| Synthetic: residual ink ratio | 11.3% |
| Synthetic: over-erased px (visible) | 9667 |
| Synthetic: px touched away from text | 227139 |
| Seconds per page | 19.878 |
| Time: detect (s) | 8.664 |
| Time: ocr (s) | 25.596 |
| Time: translate (s) | 0.002 |
| Time: mask (s) | 0.011 |
| Time: inpaint_model (s) | 91.544 |
| Time: render (s) | 0.866 |
| Time: verify (s) | 32.285 |

## By tag

| Tag | Pages | Blocks | Residue (kana/kanji) | Px outside mask | Shrink | Overflow | Residual ink |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bookwalker | 2 | 50 | 6.2% | 0 | 24 | 3 | n/a |
| conjoined-bubbles | 3 | 52 | 6.0% | 0 | 24 | 3 | 0.0% |
| horizontal-text | 2 | 7 | 28.6% | 0 | 0 | 0 | 8.1% |
| light-on-dark | 1 | 3 | 33.3% | 0 | 0 | 0 | 16.1% |
| narration | 2 | 17 | 23.5% | 0 | 8 | 1 | 16.1% |
| sfx | 4 | 66 | 9.4% | 0 | 32 | 4 | 40.4% |
| textured-background | 5 | 69 | 10.4% | 0 | 32 | 4 | 28.2% |
| vertical-text | 6 | 73 | 11.3% | 0 | 32 | 4 | 5.4% |
| webtoon-strip | 1 | 4 | 25.0% | 0 | 0 | 0 | 0.0% |
| white-bubble | 6 | 74 | 11.1% | 0 | 32 | 4 | 0.0% |

## Pages

| Page | Status | Blocks | Residue kana/kanji (Step 6) | Px outside mask | Masks glyph/interior/rect/skip +white | Shrink/overflow/unfit | Inpainter | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | ok | 4 | 1/4 (4/4) | 0 | 0/0/4/0 +0w | 0/0/0 | lama_large | 12.973 |
| syn-conjoined-bubbles | ok | 2 | 0/2 (2/2) | 0 | 0/0/2/0 +0w | 0/0/0 | lama_large | 9.601 |
| syn-textured-narration | ok | 3 | 1/3 (3/3) | 0 | 0/0/3/0 +0w | 0/0/0 | lama_large | 13.658 |
| syn-sfx | ok | 2 | 0/2 (2/2) | 0 | 0/0/2/0 +0w | 0/0/0 | lama_large | 9.98 |
| syn-webtoon-strip | ok | 4 | 1/4 (4/4) | 0 | 0/0/4/0 +0w | 0/0/0 | lama_large | 25.737 |
| real-single-page-01 | ok | 14 | 3/14 (14/14) | 0 | 0/0/13/0 +0w | 8/1/0 | lama_large | 18.959 |
| real-bookwalker-spread-01 | ok | 25 | 3/23 (24/25) | 0 | 0/0/22/0 +0w | 14/2/0 | lama_large | 35.181 |
| real-bookwalker-spread-02 | ok | 25 | 0/25 (25/25) | 0 | 0/0/25/0 +0w | 10/1/0 | lama_large | 32.941 |
