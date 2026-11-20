/**
 * Reactive form models for the options page, one per settings card.
 *
 * Each form mirrors the saved settings (`load`) and turns the edited values
 * back into settings fields (`toSettings`). Select values are plain strings
 * here; `toSettings` narrows them to the settings types, which is safe because
 * `load` only ever puts a listed option (or an empty string) into a select.
 */
import { DEFAULT_SETTINGS } from '../../lib/settings';
import type { LocalBackendSettings, RenderSettings, Settings } from '../../shared/types';
import {
  type Choice,
  DETECTOR_CHOICES,
  INPAINTER_CHOICES,
  LOCAL_TRANSLATOR_CHOICES,
  OCR_CHOICES,
  PROVIDER_CHOICES,
  TEXT_ALIGNMENT_CHOICES,
  TEXT_DIRECTION_CHOICES,
} from '../choices';

/** `value` when it is one of `choices`, otherwise `fallback` (a select never shows blank). */
export function pickChoice(choices: readonly Choice[], value: unknown, fallback: string): string {
  return choices.some((choice) => choice.value === value) ? (value as string) : fallback;
}

/** `choices`, plus the current value when it is not listed, so it is never silently dropped. */
export function withCurrentValue(choices: readonly Choice[], value: string): readonly Choice[] {
  return value === '' || choices.some((choice) => choice.value === value)
    ? choices
    : [...choices, { value, label: value }];
}

export class ProviderForm {
  provider = $state<string>(DEFAULT_SETTINGS.provider);
  model = $state('');
  apiKey = $state('');
  baseUrl = $state('');

  load(settings: Settings): void {
    this.provider = pickChoice(PROVIDER_CHOICES, settings.provider, DEFAULT_SETTINGS.provider);
    this.model = settings.model;
    this.apiKey = settings.apiKey;
    this.baseUrl = settings.baseUrl;
  }

  toSettings(): Pick<Settings, 'provider' | 'model' | 'apiKey' | 'baseUrl'> {
    return {
      provider: this.provider as Settings['provider'],
      model: this.model.trim(),
      apiKey: this.apiKey,
      baseUrl: this.baseUrl,
    };
  }
}

export class LocalBackendForm {
  url = $state('');
  authToken = $state('');
  detector = $state<string>(DEFAULT_SETTINGS.localBackend.detector);
  ocr = $state<string>(DEFAULT_SETTINGS.localBackend.ocr);
  inpainter = $state<string>(DEFAULT_SETTINGS.localBackend.inpainter);
  translator = $state('');
  llmBaseUrl = $state('');
  llmModel = $state('');
  llmApiKey = $state('');

  load(settings: Settings): void {
    const { localBackend, localLlm } = settings;
    this.url = localBackend.url;
    this.authToken = localBackend.authToken || '';
    this.detector = pickChoice(
      DETECTOR_CHOICES,
      localBackend.detector,
      DEFAULT_SETTINGS.localBackend.detector,
    );
    this.ocr = pickChoice(OCR_CHOICES, localBackend.ocr, DEFAULT_SETTINGS.localBackend.ocr);
    this.inpainter = pickChoice(
      INPAINTER_CHOICES,
      localBackend.inpainter,
      DEFAULT_SETTINGS.localBackend.inpainter,
    );
    this.translator = pickChoice(LOCAL_TRANSLATOR_CHOICES, localBackend.translator ?? '', '');
    this.llmBaseUrl = localLlm?.baseUrl || '';
    this.llmModel = localLlm?.model || '';
    this.llmApiKey = localLlm?.apiKey || '';
  }

  /** Backend fields to merge over the stored `localBackend`; the URL is returned trimmed. */
  backendToSettings(): Pick<
    LocalBackendSettings,
    'url' | 'authToken' | 'detector' | 'ocr' | 'inpainter' | 'translator'
  > {
    return {
      url: this.url.trim().replace(/\/+$/, ''),
      authToken: this.authToken,
      detector: this.detector as LocalBackendSettings['detector'],
      ocr: this.ocr as LocalBackendSettings['ocr'],
      inpainter: this.inpainter as LocalBackendSettings['inpainter'],
      translator: (this.translator || '') as LocalBackendSettings['translator'],
    };
  }

  llmToSettings(): Settings['localLlm'] {
    return { baseUrl: this.llmBaseUrl, model: this.llmModel.trim(), apiKey: this.llmApiKey };
  }

  get llmVisible(): boolean {
    return this.translator === 'local';
  }
}

export class RenderForm {
  targetLang = $state<string>(DEFAULT_SETTINGS.targetLang);
  fontSize = $state<number>(DEFAULT_SETTINGS.fontSize);
  fontFamily = $state<string>(DEFAULT_SETTINGS.fontFamily);
  textAlignment = $state<string>(DEFAULT_SETTINGS.render.textAlignment);
  textDirection = $state<string>(DEFAULT_SETTINGS.render.textDirection);
  uppercase = $state(DEFAULT_SETTINGS.render.uppercase);
  lineSpacing = $state(DEFAULT_SETTINGS.render.lineSpacing);
  borderSize = $state(DEFAULT_SETTINGS.render.borderSize);
  textColor = $state(DEFAULT_SETTINGS.render.textColor);
  borderColor = $state(DEFAULT_SETTINGS.render.borderColor);
  bgColor = $state(DEFAULT_SETTINGS.render.bgColor);
  bgOpacity = $state(DEFAULT_SETTINGS.render.bgOpacity);
  glowEnabled = $state(DEFAULT_SETTINGS.render.glowEnabled);
  glowSize = $state(DEFAULT_SETTINGS.render.glowSize);
  glowColor = $state(DEFAULT_SETTINGS.render.glowColor);
  autoScale = $state(DEFAULT_SETTINGS.render.autoScale);

  load(settings: Settings): void {
    const { render } = settings;
    this.targetLang = settings.targetLang;
    this.fontSize = settings.fontSize;
    this.fontFamily = settings.fontFamily;
    this.textAlignment = pickChoice(
      TEXT_ALIGNMENT_CHOICES,
      render.textAlignment,
      DEFAULT_SETTINGS.render.textAlignment,
    );
    this.textDirection = pickChoice(
      TEXT_DIRECTION_CHOICES,
      render.textDirection,
      DEFAULT_SETTINGS.render.textDirection,
    );
    this.uppercase = render.uppercase;
    this.lineSpacing = render.lineSpacing;
    this.borderSize = render.borderSize;
    this.textColor = render.textColor;
    this.borderColor = render.borderColor;
    this.bgColor = render.bgColor;
    this.bgOpacity = render.bgOpacity;
    this.glowEnabled = render.glowEnabled;
    this.glowSize = render.glowSize;
    this.glowColor = render.glowColor;
    this.autoScale = render.autoScale;
  }

  toSettings(): Pick<Settings, 'targetLang' | 'fontSize' | 'fontFamily'> & {
    render: RenderSettings;
  } {
    return {
      targetLang: this.targetLang,
      fontSize: Math.trunc(Number(this.fontSize)),
      fontFamily: this.fontFamily,
      render: {
        textAlignment: this.textAlignment as RenderSettings['textAlignment'],
        textDirection: this.textDirection as RenderSettings['textDirection'],
        uppercase: this.uppercase,
        lineSpacing: Number(this.lineSpacing),
        borderSize: Number(this.borderSize),
        textColor: this.textColor,
        borderColor: this.borderColor,
        bgColor: this.bgColor,
        bgOpacity: Number(this.bgOpacity),
        glowEnabled: this.glowEnabled,
        glowSize: Math.trunc(Number(this.glowSize)),
        glowColor: this.glowColor,
        autoScale: this.autoScale,
      },
    };
  }
}

export class BehaviorForm {
  autoTranslate = $state(DEFAULT_SETTINGS.autoTranslate);
  showProgress = $state(DEFAULT_SETTINGS.showProgress);

  load(settings: Settings): void {
    this.autoTranslate = settings.autoTranslate;
    this.showProgress = settings.showProgress;
  }

  toSettings(): Pick<Settings, 'autoTranslate' | 'showProgress'> {
    return { autoTranslate: this.autoTranslate, showProgress: this.showProgress };
  }
}
