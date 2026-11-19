# Pipeline eval: base-rtdetr-default

- Created: 2026-11-19T15:28:33Z, commit `add7f0beeb5e`
- Detector `rtdetr`, mask mode `auto`, inpainter `flux2_klein_lanpaint` (used: `flux2_klein_lanpaint` x8, `lama_large` x3), OCR `manga-ocr`, translator `stub`, device `cpu`
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
| Pages with inpainter fallback | 3 |
| Masks: contour-safe glyph | 85 |
| Masks: bubble interior | 0 |
| Masks: rectangular | 0 |
| Masks: skipped (no safe mask) | 0 |
| Masks: other (incl. oracle ink) | 0 |
| Router-filled blocks (no model) | 80 |
| Blocks queued to the model | 5 |
| Fill tier: flat blocks | 44 |
| Fill tier: smooth blocks | 1 |
| Fill tier: lattice blocks | 35 |
| Composite duplicates not masked | 2 |
| Masked px | 169217 |
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
| Translation units with a length budget | 89 |
| Length budget per unit (chars, mean) | 31.9 |
| Length budget per unit (chars, min) | 6 |
| Pages sent with a numbered page image | 0 |
| Translation provider requests | 0 |
| Units re-asked for a shorter translation | 0 |
| Re-asked units that came back shorter | 0 |
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
| Pre-render flag: agreement with truth residue | 0.64 |
| Pre-render flag: agreement (fill failure only) | 0.8 |
| Pre-render flag: blocks with truth residue | 9 |
| Pre-render flag: blocks flagged | 2 |
| Pre-render flag: false positives | 0 |
| Pre-render flag: false negatives | 9 |
| Residual escalation: blocks escalated | 2 |
| Residual escalation: second flags (not retried) | 0 |
| Seconds per page | 3.992 |
| Time: harness re-OCR (s) | 25.097 |
| Time: detect (s) | 8.878 |
| Time: ocr (s) | 20.987 |
| Time: translate (s) | 0.003 |
| Time: mask (s) | 5.677 |
| Time: inpaint_model (s) | 2.823 |
| Time: render (s) | 4.151 |
| Time: verify (s) | 0 |

## By tag

| Tag | Pages | Blocks | Residue (kana/kanji) | Px outside mask | Shrink | Overflow | Residual ink | Ink recall | Resid in->out mask |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adversarial | 3 | 10 | 20.0% | 0 | 0 | 0 | 4.5% | 96.2% | 1.3% -> 3.2% |
| bookwalker | 2 | 50 | 16.7% | 0 | 21 | 0 | n/a | n/a | n/a -> n/a |
| conjoined-bubbles | 3 | 52 | 16.0% | 0 | 21 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| horizontal-text | 5 | 17 | 11.8% | 0 | 0 | 0 | 2.7% | 97.7% | 0.8% -> 1.9% |
| light-on-dark | 1 | 3 | 0.0% | 0 | 0 | 0 | 0.1% | 100.0% | 0.0% -> 0.1% |
| narration | 3 | 21 | 14.3% | 0 | 6 | 0 | 1.3% | 99.5% | 1.1% -> 0.1% |
| sfx | 5 | 70 | 16.2% | 0 | 27 | 0 | 1.8% | 97.4% | 1.1% -> 0.7% |
| textured-background | 7 | 76 | 17.6% | 0 | 27 | 0 | 1.1% | 98.7% | 0.8% -> 0.4% |
| vertical-text | 9 | 83 | 16.1% | 0 | 27 | 0 | 2.3% | 98.1% | 0.7% -> 1.6% |
| webtoon-strip | 1 | 4 | 0.0% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| white-bubble | 8 | 80 | 16.7% | 0 | 27 | 0 | 2.2% | 97.9% | 0.3% -> 1.9% |

## Pages

| Page | Status | Blocks | Residue kana/kanji (Step 6) | Px outside mask | Masks glyph/interior/rect/skip +router | Shrink/overflow/unfit | Inpainter | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | ok | 4 | 0/4 (0/4) | 0 | 4/0/0/0 +4r | 0/0/0 | flux2_klein_lanpaint | 2.186 |
| syn-conjoined-bubbles | ok | 2 | 0/2 (0/2) | 0 | 2/0/0/0 +2r | 0/0/0 | flux2_klein_lanpaint | 3.047 |
| syn-textured-narration | ok | 3 | 0/3 (0/3) | 0 | 3/0/0/0 +3r | 0/0/0 | flux2_klein_lanpaint | 1.862 |
| syn-sfx | ok | 2 | 0/2 (0/2) | 0 | 2/0/0/0 +2r | 0/0/0 | flux2_klein_lanpaint | 1.477 |
| syn-webtoon-strip | ok | 4 | 0/4 (0/4) | 0 | 4/0/0/0 +4r | 0/0/0 | flux2_klein_lanpaint | 2.877 |
| syn-tone-rotated | ok | 4 | 0/4 (0/4) | 0 | 4/0/0/0 +4r | 0/0/0 | flux2_klein_lanpaint | 2.164 |
| syn-faint-lettering | ok | 3 | 2/3 (2/3) | 0 | 3/0/0/0 +3r | 0/0/0 | flux2_klein_lanpaint | 2.369 |
| syn-text-touching-outline | ok | 3 | 0/3 (0/3) | 0 | 3/0/0/0 +3r | 0/0/0 | flux2_klein_lanpaint | 1.938 |
| real-single-page-01 | ok | 14 | 3/14 (3/14) | 0 | 13/0/0/0 +12r | 6/0/0 | lama_large (fallback) | 6.723 |
| real-bookwalker-spread-01 | ok | 25 | 5/23 (5/25) | 0 | 22/0/0/0 +20r | 14/0/0 | lama_large (fallback) | 9.055 |
| real-bookwalker-spread-02 | ok | 25 | 3/25 (3/25) | 0 | 25/0/0/0 +23r | 7/0/0 | lama_large (fallback) | 10.212 |
