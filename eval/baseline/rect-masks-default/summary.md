# Pipeline eval: rect-masks-default

- Created: 2026-11-18T05:16:39Z, commit `c1af6c2297dc`
- Detector `rtdetr`, mask mode `rectangular`, inpainter `flux2_klein_lanpaint` (used: `flux2_klein_lanpaint` x3, `lama_large` x5), OCR `manga-ocr`, translator `stub`, device `cpu`
- ComfyUI reachable: False
- Pages: total 8, scored 8

## Overall

| Metric | Value |
| --- | --- |
| Pages scored | 8 |
| Blocks detected | 79 |
| Blocks to erase | 77 |
| CJK residue, Step 6 verdict | 12.7% |
| CJK residue, kana/kanji only | 13.0% |
| Px changed outside mask | 0 |
| Px inpainter changed outside its mask | 0 |
| Pages with inpainter fallback | 5 |
| Masks: contour-safe glyph | 75 |
| Masks: bubble interior | 0 |
| Masks: rectangular | 0 |
| Masks: skipped (no safe mask) | 0 |
| White-fill blocks | 68 |
| Composite duplicates not masked | 2 |
| Masked px | 181216 |
| Render regions | 75 |
| Shrink events (< preferred size) | 32 |
| Below min readable size | 6 |
| Overflow events | 4 |
| Unfit regions skipped | 0 |
| Median font size | 16 |
| Synthetic: detection recall | 100.0% |
| Synthetic: text-region MAE | 7.974 |
| Synthetic: residual ink ratio | 6.4% |
| Synthetic: over-erased px (visible) | 2832 |
| Synthetic: px touched away from text | 2832 |
| Seconds per page | 22.138 |
| Time: detect (s) | 12.366 |
| Time: ocr (s) | 43.203 |
| Time: translate (s) | 0.008 |
| Time: mask (s) | 0.612 |
| Time: inpaint_model (s) | 67.57 |
| Time: render (s) | 1.246 |
| Time: verify (s) | 52.017 |

## By tag

| Tag | Pages | Blocks | Residue (kana/kanji) | Px outside mask | Shrink | Overflow | Residual ink |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bookwalker | 2 | 50 | 12.5% | 0 | 24 | 3 | n/a |
| conjoined-bubbles | 3 | 52 | 12.0% | 0 | 24 | 3 | 0.0% |
| horizontal-text | 2 | 7 | 14.3% | 0 | 0 | 0 | 6.0% |
| light-on-dark | 1 | 3 | 0.0% | 0 | 0 | 0 | 12.0% |
| narration | 2 | 17 | 11.8% | 0 | 8 | 1 | 12.0% |
| sfx | 4 | 66 | 12.5% | 0 | 32 | 4 | 20.1% |
| textured-background | 5 | 69 | 11.9% | 0 | 32 | 4 | 16.1% |
| vertical-text | 6 | 73 | 12.7% | 0 | 32 | 4 | 4.0% |
| webtoon-strip | 1 | 4 | 25.0% | 0 | 0 | 0 | 0.0% |
| white-bubble | 6 | 74 | 13.9% | 0 | 32 | 4 | 0.0% |

## Pages

| Page | Status | Blocks | Residue kana/kanji (Step 6) | Px outside mask | Masks glyph/interior/rect/skip +white | Shrink/overflow/unfit | Inpainter | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | ok | 4 | 1/4 (1/4) | 0 | 4/0/0/0 +4w | 0/0/0 | flux2_klein_lanpaint | 4.271 |
| syn-conjoined-bubbles | ok | 2 | 0/2 (0/2) | 0 | 2/0/0/0 +2w | 0/0/0 | flux2_klein_lanpaint | 3.843 |
| syn-textured-narration | ok | 3 | 0/3 (0/3) | 0 | 3/0/0/0 +2w | 0/0/0 | lama_large (fallback) | 16.787 |
| syn-sfx | ok | 2 | 0/2 (0/2) | 0 | 2/0/0/0 +0w | 0/0/0 | lama_large (fallback) | 13.935 |
| syn-webtoon-strip | ok | 4 | 1/4 (1/4) | 0 | 4/0/0/0 +4w | 0/0/0 | flux2_klein_lanpaint | 8.692 |
| real-single-page-01 | ok | 14 | 2/14 (2/14) | 0 | 13/0/0/0 +11w | 8/1/0 | lama_large (fallback) | 30.926 |
| real-bookwalker-spread-01 | ok | 25 | 5/23 (5/25) | 0 | 22/0/0/0 +21w | 14/2/0 | lama_large (fallback) | 49.06 |
| real-bookwalker-spread-02 | ok | 25 | 1/25 (1/25) | 0 | 25/0/0/0 +24w | 10/1/0 | lama_large (fallback) | 49.592 |
