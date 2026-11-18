# Pipeline eval: rtdetr-lama

- Created: 2026-11-18T03:38:09Z, commit `1b0b4cfe4700`
- Detector `rtdetr`, mask mode `auto`, inpainter `lama_large` (used: `lama_large` x8), OCR `manga-ocr`, translator `stub`, device `cpu`
- ComfyUI reachable: False
- Pages: total 8, scored 8

## Overall

| Metric | Value |
| --- | --- |
| Pages scored | 8 |
| Blocks detected | 79 |
| Blocks to erase | 77 |
| CJK residue, Step 6 verdict | 98.7% |
| CJK residue, kana/kanji only | 15.6% |
| Px changed outside mask | 0 |
| Px inpainter changed outside its mask | 0 |
| Pages with inpainter fallback | 0 |
| Masks: contour-safe glyph | 75 |
| Masks: bubble interior | 0 |
| Masks: rectangular | 0 |
| Masks: skipped (no safe mask) | 0 |
| White-fill blocks | 0 |
| Composite duplicates not masked | 2 |
| Masked px | 124721 |
| Render regions | 75 |
| Shrink events (< preferred size) | 32 |
| Below min readable size | 6 |
| Overflow events | 4 |
| Unfit regions skipped | 0 |
| Median font size | 16 |
| Synthetic: detection recall | 100.0% |
| Synthetic: text-region MAE | 8.085 |
| Synthetic: residual ink ratio | 6.5% |
| Synthetic: over-erased px (visible) | 2658 |
| Synthetic: px touched away from text | 6407 |
| Seconds per page | 20.065 |
| Time: detect (s) | 9.874 |
| Time: ocr (s) | 24.856 |
| Time: translate (s) | 0.001 |
| Time: mask (s) | 0.203 |
| Time: inpaint_model (s) | 93.663 |
| Time: render (s) | 0.891 |
| Time: verify (s) | 30.961 |

## By tag

| Tag | Pages | Blocks | Residue (kana/kanji) | Px outside mask | Shrink | Overflow | Residual ink |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bookwalker | 2 | 50 | 16.7% | 0 | 24 | 3 | n/a |
| conjoined-bubbles | 3 | 52 | 16.0% | 0 | 24 | 3 | 0.0% |
| horizontal-text | 2 | 7 | 14.3% | 0 | 0 | 0 | 6.1% |
| light-on-dark | 1 | 3 | 0.0% | 0 | 0 | 0 | 12.2% |
| narration | 2 | 17 | 11.8% | 0 | 8 | 1 | 12.2% |
| sfx | 4 | 66 | 15.6% | 0 | 32 | 4 | 20.1% |
| textured-background | 5 | 69 | 14.9% | 0 | 32 | 4 | 16.2% |
| vertical-text | 6 | 73 | 15.5% | 0 | 32 | 4 | 4.1% |
| webtoon-strip | 1 | 4 | 25.0% | 0 | 0 | 0 | 0.0% |
| white-bubble | 6 | 74 | 16.7% | 0 | 32 | 4 | 0.0% |

## Pages

| Page | Status | Blocks | Residue kana/kanji (Step 6) | Px outside mask | Masks glyph/interior/rect/skip +white | Shrink/overflow/unfit | Inpainter | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | ok | 4 | 1/4 (4/4) | 0 | 4/0/0/0 +0w | 0/0/0 | lama_large | 14.711 |
| syn-conjoined-bubbles | ok | 2 | 0/2 (2/2) | 0 | 2/0/0/0 +0w | 0/0/0 | lama_large | 11.181 |
| syn-textured-narration | ok | 3 | 0/3 (3/3) | 0 | 3/0/0/0 +0w | 0/0/0 | lama_large | 14.807 |
| syn-sfx | ok | 2 | 0/2 (2/2) | 0 | 2/0/0/0 +0w | 0/0/0 | lama_large | 10.166 |
| syn-webtoon-strip | ok | 4 | 1/4 (4/4) | 0 | 4/0/0/0 +0w | 0/0/0 | lama_large | 26.742 |
| real-single-page-01 | ok | 14 | 2/14 (14/14) | 0 | 13/0/0/0 +0w | 8/1/0 | lama_large | 20.012 |
| real-bookwalker-spread-01 | ok | 25 | 8/23 (24/25) | 0 | 22/0/0/0 +0w | 14/2/0 | lama_large | 30.649 |
| real-bookwalker-spread-02 | ok | 25 | 0/25 (25/25) | 0 | 25/0/0/0 +0w | 10/1/0 | lama_large | 32.253 |
