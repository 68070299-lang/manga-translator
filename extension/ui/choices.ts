/** Option lists for the popup and options `<select>` controls (values are persisted). */

export interface Choice {
  value: string;
  label: string;
}

export const TARGET_LANGUAGES: readonly string[] = [
  'English',
  'Spanish',
  'French',
  'German',
  'Portuguese',
  'Italian',
  'Russian',
  'Chinese',
  'Korean',
  'Vietnamese',
  'Thai',
  'Indonesian',
];

const SHARED_FONTS: readonly Choice[] = [
  { value: 'sans-serif', label: 'Sans-serif' },
  { value: "'Anime Ace', sans-serif", label: 'Anime Ace (if installed)' },
  { value: "'Manga Temple', sans-serif", label: 'Manga Temple (if installed)' },
  { value: "'Back Issues', sans-serif", label: 'Back Issues (if installed)' },
  { value: "'Comic Sans MS', cursive", label: 'Comic Sans MS' },
];

/** Bundled fonts come first; their `value` is a bundled font's `cssValue`. */
export const POPUP_FONT_CHOICES: readonly Choice[] = [
  { value: "'CC Wild Words', sans-serif", label: 'CC Wild Words' },
  ...SHARED_FONTS,
];

export const OPTIONS_FONT_CHOICES: readonly Choice[] = [
  { value: "'CC Wild Words', sans-serif", label: 'CC Wild Words (Bundled, default)' },
  ...SHARED_FONTS,
];

export const PROVIDER_CHOICES: readonly Choice[] = [
  { value: 'gemini', label: 'Gemini' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'local', label: 'Local LLM (llama.cpp / vLLM)' },
];

export const DETECTOR_CHOICES: readonly Choice[] = [
  { value: 'default', label: 'Default (RT-DETR)' },
  { value: 'rtdetr', label: 'RT-DETR' },
  { value: 'animetext-yolo', label: 'AnimeText YOLO12' },
];

export const OCR_CHOICES: readonly Choice[] = [
  { value: 'manga-ocr', label: 'manga-ocr' },
  { value: 'paddle-ocr', label: 'paddle-ocr' },
  { value: 'google-ocr', label: 'google-ocr' },
];

export const INPAINTER_CHOICES: readonly Choice[] = [
  {
    value: 'flux2_klein_lanpaint',
    label: 'FLUX.2 Klein Distilled + LanPaint (Quality)',
  },
  { value: 'flux2_klein_q4', label: 'FLUX.2 Klein Q4 (sd-cli, optional)' },
  { value: 'lama_large', label: 'LaMa (Fast)' },
  { value: 'none', label: 'None' },
];

export const LOCAL_TRANSLATOR_CHOICES: readonly Choice[] = [
  { value: '', label: 'Use provider setting' },
  { value: 'none', label: 'None (OCR only)' },
  { value: 'gemini', label: 'Gemini' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'local', label: 'Local LLM (llama.cpp / vLLM)' },
];

export const GOOGLE_LANGUAGE_HINT_CHOICES: readonly Choice[] = [
  { value: '', label: 'Auto-detect' },
  { value: 'ja', label: 'Japanese' },
  { value: 'zh', label: 'Chinese' },
  { value: 'ko', label: 'Korean' },
  { value: 'en', label: 'English' },
];

export const TEXT_ALIGNMENT_CHOICES: readonly Choice[] = [
  { value: 'left', label: 'Left' },
  { value: 'center', label: 'Center' },
  { value: 'right', label: 'Right' },
];

export const TEXT_DIRECTION_CHOICES: readonly Choice[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'horizontal', label: 'Horizontal' },
  { value: 'vertical', label: 'Vertical' },
];
