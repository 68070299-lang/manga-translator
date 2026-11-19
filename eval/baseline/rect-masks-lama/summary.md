# Pipeline eval: base-rect-masks-lama

- Created: 2026-11-19T15:34:34Z, commit `add7f0beeb5e`
- Detector `rtdetr`, mask mode `rectangular`, inpainter `lama_large` (used: `lama_large` x11), OCR `manga-ocr`, translator `stub`, device `cpu`
- ComfyUI reachable: False
- Pages: total 11, scored 11

## Overall

| Metric | Value |
| --- | --- |
| Pages scored | 11 |
| Blocks detected | 89 |
| Blocks to erase | 87 |
| CJK residue, Step 6 verdict | 24.7% |
| CJK residue, kana/kanji only | 25.3% |
| Px changed outside mask | 0 |
| Px inpainter changed outside its mask | 0 |
| Pages with inpainter fallback | 0 |
| Masks: contour-safe glyph | 0 |
| Masks: bubble interior | 0 |
| Masks: rectangular | 85 |
| Masks: skipped (no safe mask) | 0 |
| Masks: other (incl. oracle ink) | 0 |
| Router-filled blocks (no model) | 58 |
| Blocks queued to the model | 27 |
| Fill tier: flat blocks | 16 |
| Fill tier: smooth blocks | 1 |
| Fill tier: lattice blocks | 41 |
| Composite duplicates not masked | 2 |
| Masked px | 914650 |
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
| Translation units with a length budget | 89 |
| Length budget per unit (chars, mean) | 31.9 |
| Length budget per unit (chars, min) | 6 |
| Pages sent with a numbered page image | 0 |
| Translation provider requests | 0 |
| Units re-asked for a shorter translation | 0 |
| Re-asked units that came back shorter | 0 |
| Render regions | 85 |
| Shrink events (< anchored target) | 25 |
| Below min readable size | 6 |
| Overflow events | 0 |
| Unfit regions skipped | 0 |
| Median font size | 16 |
| Font size spread per page (mean) | 2.091 |
| Dialogue font size spread per page (mean) | 1.7 |
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
| Pre-render flag: agreement with truth residue | 0.857 |
| Pre-render flag: agreement (fill failure only) | 0.857 |
| Pre-render flag: blocks with truth residue | 3 |
| Pre-render flag: blocks flagged | 1 |
| Pre-render flag: false positives | 0 |
| Pre-render flag: false negatives | 3 |
| Residual escalation: blocks escalated | 1 |
| Residual escalation: second flags (not retried) | 0 |
| Seconds per page | 7.147 |
| Time: harness re-OCR (s) | 25.976 |
| Time: detect (s) | 9.288 |
| Time: ocr (s) | 20.907 |
| Time: translate (s) | 0.002 |
| Time: mask (s) | 4.922 |
| Time: inpaint_model (s) | 38.017 |
| Time: render (s) | 3.917 |
| Time: verify (s) | 0 |

## By tag

| Tag | Pages | Blocks | Residue (kana/kanji) | Px outside mask | Shrink | Overflow | Residual ink | Ink recall | Resid in->out mask |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| adversarial | 3 | 10 | 20.0% | 0 | 0 | 0 | 5.8% | 96.5% | 2.7% -> 3.1% |
| bookwalker | 2 | 50 | 29.2% | 0 | 19 | 0 | n/a | n/a | n/a -> n/a |
| conjoined-bubbles | 3 | 52 | 28.0% | 0 | 19 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| horizontal-text | 5 | 17 | 11.8% | 0 | 0 | 0 | 3.5% | 97.9% | 1.6% -> 1.8% |
| light-on-dark | 1 | 3 | 0.0% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| narration | 3 | 21 | 28.6% | 0 | 6 | 0 | 3.6% | 100.0% | 3.6% -> 0.0% |
| sfx | 5 | 70 | 29.4% | 0 | 25 | 0 | 3.6% | 100.0% | 3.6% -> 0.0% |
| textured-background | 7 | 76 | 29.7% | 0 | 25 | 0 | 1.8% | 100.0% | 1.8% -> 0.0% |
| vertical-text | 9 | 83 | 27.2% | 0 | 25 | 0 | 2.9% | 98.3% | 1.3% -> 1.5% |
| webtoon-strip | 1 | 4 | 0.0% | 0 | 0 | 0 | 0.0% | 100.0% | 0.0% -> 0.0% |
| white-bubble | 8 | 80 | 28.2% | 0 | 25 | 0 | 2.0% | 97.9% | 0.2% -> 1.8% |

## Pages

| Page | Status | Blocks | Residue kana/kanji (Step 6) | Px outside mask | Masks glyph/interior/rect/skip +router | Shrink/overflow/unfit | Inpainter | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| syn-white-bubbles | ok | 4 | 0/4 (0/4) | 0 | 0/0/4/0 +2r | 0/0/0 | lama_large | 7.157 |
| syn-conjoined-bubbles | ok | 2 | 0/2 (0/2) | 0 | 0/0/2/0 +2r | 0/0/0 | lama_large | 1.908 |
| syn-textured-narration | ok | 3 | 0/3 (0/3) | 0 | 0/0/3/0 +3r | 0/0/0 | lama_large | 1.82 |
| syn-sfx | ok | 2 | 0/2 (0/2) | 0 | 0/0/2/0 +2r | 0/0/0 | lama_large | 1.694 |
| syn-webtoon-strip | ok | 4 | 0/4 (0/4) | 0 | 0/0/4/0 +4r | 0/0/0 | lama_large | 2.815 |
| syn-tone-rotated | ok | 4 | 0/4 (0/4) | 0 | 0/0/4/0 +4r | 0/0/0 | lama_large | 1.756 |
| syn-faint-lettering | ok | 3 | 2/3 (2/3) | 0 | 0/0/3/0 +3r | 0/0/0 | lama_large | 1.901 |
| syn-text-touching-outline | ok | 3 | 0/3 (0/3) | 0 | 0/0/3/0 +1r | 0/0/0 | lama_large | 8.208 |
| real-single-page-01 | ok | 14 | 6/14 (6/14) | 0 | 0/0/13/0 +8r | 6/0/0 | lama_large | 10.892 |
| real-bookwalker-spread-01 | ok | 25 | 8/23 (8/25) | 0 | 0/0/22/0 +13r | 12/0/0 | lama_large | 17.033 |
| real-bookwalker-spread-02 | ok | 25 | 6/25 (6/25) | 0 | 0/0/25/0 +16r | 7/0/0 | lama_large | 23.435 |
