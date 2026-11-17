> **Historical design notes - superseded by docs/RUNTIME_CONTRACT.md and README.md; detector/OCR/translator options and message shapes below are not current.**

# Manga Translation Chrome Extension - Implementation Plan

## Overview
Build a Chrome Extension that captures manga images from web pages and translates them using LLM-powered OCR and translation APIs, with a configurable provider system and visual progress feedback.

---

## Phase 1: Extension Scaffold

### 1.1 Folder Structure
```
extension/
+-- manifest.json
+-- popup/
|   +-- popup.html
|   +-- popup.js
|   +-- popup.css
+-- options/
|   +-- options.html
|   +-- options.js
+-- background/
|   +-- service-worker.js
+-- content/
|   +-- content.js
|   +-- content.css
+-- lib/
|   +-- api.js          # Provider abstraction
|   +-- cache.js        # Caching utilities
|   +-- utils.js        # Shared helpers
+-- icons/
    +-- icon16.png
    +-- icon48.png
    +-- icon128.png
```

### 1.2 Manifest V3 Configuration
```json
{
  "manifest_version": 3,
  "name": "Manga Translator",
  "version": "1.0.0",
  "description": "Translate manga images in-browser",
  "permissions": [
    "activeTab",
    "scripting",
    "storage",
    "tabs",
    "contextMenus"
  ],
  "host_permissions": ["<all_urls>"],
  "background": {
    "service_worker": "background/service-worker.js"
  },
  "content_scripts": [{
    "matches": ["<all_urls>"],
    "js": ["content/content.js"],
    "css": ["content/content.css"],
    "run_at": "document_idle"
  }],
  "action": {
    "default_popup": "popup/popup.html",
    "default_icon": {
      "16": "icons/icon16.png",
      "48": "icons/icon48.png",
      "128": "icons/icon128.png"
    }
  },
  "options_page": "options/options.html"
}
```

---

## Phase 2: Image Capture System

### 2.1 DOM Image Discovery
- Scan for `<img>` elements
- Scan for `<canvas>` elements  
- Extract CSS `background-image` URLs
- Filter by minimum dimensions (e.g., 200x200px)

### 2.2 Lazy-Load Detection
```javascript
const observer = new MutationObserver((mutations) => {
  mutations.forEach((mutation) => {
    mutation.addedNodes.forEach((node) => {
      if (node.nodeName === 'IMG') {
        processNewImage(node);
      }
    });
  });
});
observer.observe(document.body, { childList: true, subtree: true });
```

### 2.3 Image Extraction Priority
1. **Canvas Method** (fastest) -- Create canvas, draw image, `toBlob()`
2. **Background Fetch** (CORS bypass) -- Send URL to service worker, fetch there
3. **Screenshot Fallback** -- `chrome.tabs.captureVisibleTab()`, crop to image bounds

---

## Phase 3: Provider Abstraction Layer

### 3.1 Supported Providers
| Provider | Base URL | Model Format | Auth |
|----------|----------|--------------|------|
| Gemini | `generativelanguage.googleapis.com/v1` | `gemini-3.0-flash` | `?key=API_KEY` |
| OpenRouter | `openrouter.ai/api/v1` | `google/gemini-2.0-flash` | `Bearer TOKEN` |
| Custom | User-defined | User-defined | `Bearer TOKEN` |

### 3.2 Unified API Interface
```javascript
class TranslationProvider {
  constructor(config) {
    this.provider = config.provider;    // 'gemini' | 'openrouter' | 'custom'
    this.apiKey = config.apiKey;
    this.model = config.model;
    this.baseUrl = config.baseUrl;
  }

  async translateImage(imageBlob, targetLang) { /* ... */ }
  async ocrImage(imageBlob) { /* ... */ }
}
```

### 3.3 Settings Schema
```javascript
const settings = {
  provider: "gemini",
  apiKey: "",
  model: "gemini-2.0-flash",
  customBaseUrl: "",
  targetLang: "en",
  fontSize: 14,
  fontFamily: "CC Wild Words",
  autoMode: true,
  showProgress: true
};
```

---

## Phase 4: Popup UI Design

### 4.1 Layout Structure
```
+---------------------------------+
|  [Web] Manga Translator            |
+---------------------------------+
|  Provider: [Gemini v]           |
|  Model:    [gemini-2.0-flash  ] |
|  API Key:  [****************  ] |
+---------------------------------+
|  Target:   [English v]          |
+---------------------------------+
|  Font Size: <====o====> 14px    |
|  Font:     [CC Wild Words v]    |
+---------------------------------+
|  [x] Auto-translate on load       |
|  [x] Show progress overlay        |
+---------------------------------+
|  [Translate Page] [Clear Cache] |
+---------------------------------+
```

### 4.2 Model Input Hints (per provider)
- **Gemini**: `gemini-2.0-flash`, `gemini-2.5-pro`
- **OpenRouter**: `google/gemini-2.0-flash`, `anthropic/claude-3.5-sonnet`
- **Custom**: Any model name supported by your endpoint

### 4.3 Font Options
- Sans-serif (default)
- CC Wild Words
- Anime Ace
- Manga Temple
- Back Issues

### 4.4 Font Size Limits
- Min: 8px
- Max: 24px
- Default: 14px

---

## Phase 5: Translation Pipeline

### 5.1 Pipeline Flow
```
Image Blob
    |
    v
+-----------------+
| Detect buble , OCR (mangaocr , paddelVL)     | <- Extract Japanese text from image
|
+--------+--------+
         |
         v
+-----------------+
| Translation     | <- LLM translates with manga context
| (Same provider) |
+--------+--------+
         |
         v
+-----------------+
| Render Overlay  | <- Position translated text on image
+--------+--------+
         |
         v
    Translated Image
```

### 5.2 OCR Prompt (for Vision LLMs)
```
Extract all Japanese text from this manga panel.
Return as JSON: [{"bbox": [x1,y1,x2,y2], "text": "日本語テキスト"}]
Include speech bubbles, narration boxes, and sound effects.
```

### 5.3 Translation Prompt
```
Translate this manga dialogue from Japanese to English.
Preserve character voice and tone.
Keep translations concise to fit speech bubbles.
Input: {extracted_text}
Return as JSON with same structure, "translated" field added.
```

### 5.4 Progress Overlay (Bottom-Left)
```css
.translate-progress {
  position: absolute;
  bottom: 10px;
  left: 10px;
  background: rgba(0, 0, 0, 0.85);
  color: #fff;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  z-index: 10000;
  display: flex;
  align-items: center;
  gap: 8px;
}

.progress-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid #fff;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}
```

### 5.5 Status Messages
1. `Detecting text...` (Detection phase)
2. `Extracting text...` (OCR phase)
3. `Translating...` (Translation phase)
4. `Cleaning image...` (Inpainting phase)
5. `Rendering...` (Overlay phase)
6. `[done] Complete` (Done)

---

## Phase 5B: Advanced Pipeline Configuration (Local Backend Mode)

### 5B.1 Full Pipeline Flow (SOTA)
```
Image Blob
    |
    v
+-----------------+
| Text Detection  | <- Find speech bubbles & text regions
| (CTD/RT-DETR)   |   Returns: bboxes, masks
+--------+--------+
         |
         v
+-----------------+
| OCR             | <- Extract text from each region
| (manga-ocr,paddleVL)     |   Returns: text per region
+--------+--------+
         |
         v
+-----------------+
| Translation API     | <- LLM translates with full page context
| (Gemini/GPT-4o) |   Returns: translated text
+--------+--------+
         |
         v
+-----------------+
| Inpainting      | <- Remove original text from image
| (LaMa/AOT-GAN)  |   Returns: cleaned image
+--------+--------+
         |
         v
+-----------------+
| Text Rendering  | <- Draw translated text on cleaned image
| (SVG/Canvas)    |   Returns: final translated image
+--------+--------+
         |
         v
    Translated Image
```


### 5B.3 Text Detection Models

| Model | Source | Best For | Config Key |
|-------|--------|----------|------------|
| **default** | manga-image-translator | General manga | `detector: "default"` |
| **CTD** | [dmMaze/comic-text-detector](https://github.com/dmMaze/comic-text-detector) | Dense text, complex layouts | `detector: "ctd"` |
| **craft** | manga-image-translator | Scene text | `detector: "craft"` |

**Detector Configuration:**
```json
{
  "detector": {
    "detector": "default",
    "detection_size": 1536,
    "text_threshold": 0.5,
    "box_threshold": 0.7,
    "unclip_ratio": 2.3,
    "det_rotate": false,
    "det_invert": false
  }
}
```

### 5B.4 OCR Models

| Model | Source | Language | Config Key |
|-------|--------|----------|------------|
| **manga-ocr** | [kha-white/manga-ocr](https://github.com/kha-white/manga-ocr) | Japanese | `ocr: "manga-ocr"` |
| **PaddleOCR-VL-Manga** | [jzhang533/PaddleOCR-VL-For-Manga](https://github.com/jzhang533/PaddleOCR-VL-For-Manga) | Japanese (70% accuracy) | `ocr: "paddle-vl"` |
| **PPOCRv5** | [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | Multi-language | `ocr: "48px"` |
| **Pororo** | [yunwoong7/korean_ocr_using_pororo](https://github.com/yunwoong7/korean_ocr_using_pororo) | Korean | `ocr: "pororo"` |
| **Gemini Vision** | Cloud API | All languages | `ocr: "gemini"` |

**OCR Configuration:**
```json
{
  "ocr": {
    "ocr": "manga-ocr",
    "use_mocr_merge": true,
    "min_text_length": 1,
    "ignore_bubble": 0
  }
}
```

### 5B.5 Inpainting Models

| Model | Source | Quality | Speed |
|-------|--------|---------|-------|
| **lama_large** | [ogkalu/lama-manga-onnx-dynamic](https://huggingface.co/ogkalu/lama-manga-onnx-dynamic) | Best for manga | Slower |
| **none** | Skip inpainting | N/A | Instant |

**Inpainter Configuration:**
```json
{
  "inpainter": {
    "inpainter": "lama_large"
  }
}
```

### 5B.6 Translation  API

| Model | API Key Required |
|-------|------|------------------|----------|
| **gemini**  | `GEMINI_API_KEY` | Context-aware translation |
| **openai** | `OPENAI_API_KEY` | High quality |
| **openrouter/compitable_openai_api** | "KEY_OA_complitable"


5B.7 Local Backend Setup

method to set up how to run  model local see this repo:[ogkalu2/comic-translate]

**API Endpoints:**
- Swagger UI: `http://localhost:8000/docs`
- Translation API: `POST http://localhost:8001/translate`

### 5B.8 Extension <-> Local Backend Integration

```javascript
// lib/api.js - Add local backend provider
class LocalBackendProvider {
  constructor(config) {
    this.baseUrl = config.localBackendUrl || 'http://localhost:8001';
    this.settings = config.pipelineSettings;
  }

  async translateImage(imageBlob) {
    const formData = new FormData();
    formData.append('image', imageBlob, 'image.png');
    formData.append('target_lang', this.settings.targetLang);
    formData.append('detector', this.settings.detector);
    formData.append('ocr', this.settings.ocr);
    formData.append('translator', this.settings.translator);
    formData.append('inpainter', this.settings.inpainter);
    
    const response = await fetch(`${this.baseUrl}/translate`, {
      method: 'POST',
      body: formData
    });
    
    if (!response.ok) {
      throw new Error(`Backend error: ${response.status}`);
    }
    
    return await response.blob();
  }
}
```

### 5B.9 Runtime Options

| Runtime | Platform | GPU Support | Install |
|---------|----------|-------------|---------|
| **onnxruntime** | All | CPU only | `pip install onnxruntime` |
| **onnxruntime-gpu** | Linux/Windows | NVIDIA CUDA | `pip install onnxruntime-gpu` |
| **onnxruntime-directml** | Windows | AMD/Intel/NVIDIA | `pip install onnxruntime-directml` |
| **PyTorch CUDA** | Linux/Windows | NVIDIA | `pip install torch --index-url .../cu121` |

---

## Phase 5C: Pipeline Settings UI

### 5C.1 Settings Page Layout
```
+-----------------------------------------+
|  [Settings] Translation Pipeline                |
+-----------------------------------------+
|   ( ) Local Backend       |
+-----------------------------------------+
|  v Translate API Settings                   |
|  +- Provider: [Gemini v]                |
|  +- Model:    [gemini-2.0-flash    ]    |
|  +- API Key:  [****************    ]    |
+-----------------------------------------+
|  v Local Backend Settings (collapsed)   |
|  +- Backend URL: [http://localhost:8001]|
|  +- Detector:    [default v]            |
|  +- OCR Model:   [manga-ocr v]          |
|  +- Inpainter:   [lama_large v]         |
|  +- Translator:  [gemini v]             |
+-----------------------------------------+
|  v Render Settings                      |
|  +- Font Size:   <===o===> 14px         |
|  +- Font Family: [CC Wild Words v]      |
|  +- Alignment:   [center v]             |
|  +- [ ] Uppercase text                    |
+-----------------------------------------+
```

### 5C.2 Extended Settings Schema
```javascript
const settings = {
  // Pipeline mode
  
  
  // Local backend settings (NEW)
  localBackend: {
    url: "http://localhost:8001",
    detector: "default",      // "default" | "ctd" | "craft"
    ocr: "manga-ocr",         // "manga-ocr" | "paddle-vl" | "48px" | "pororo"
    inpainter: "lama_large",  // "lama_large" | "none" (legacy "lama" is normalized)
    translator: "gemini",     // "gemini" | "openai" | "sugoi" | "sakura" | "nllb"
  },
  
  // Common settings
  targetLang: "en",
  
  // Render settings (extended)
  render: {
    fontSize: 14,
    fontFamily: "CC Wild Words",
    alignment: "center",      // "left" | "center" | "right"
    uppercase: false,
    fontColor: "",            // "" = auto-detect, or hex like "FFFFFF"
    direction: "auto",        // "auto" | "horizontal" | "vertical"
  },
  
  // Behavior
  autoMode: true,
  showProgress: true,
};
```

---

## Phase 6: Caching System

### 6.1 Cache Key Generation
```javascript
async function generateCacheKey(imageBlob, settings) {
  const imageHash = await hashBlob(imageBlob);
  const settingsHash = hashObject({
    provider: settings.provider,
    model: settings.model,
    targetLang: settings.targetLang
  });
  return `${imageHash}_${settingsHash}`;
}
```

### 6.2 Storage Strategy
- **Cache API** -- Store translated image blobs
- **chrome.storage.local** -- Store metadata and settings
- **IndexedDB** -- Optional, for larger datasets

### 6.3 Cache Invalidation
- On settings change (provider/model/language)
- Manual purge via popup button
- Optional TTL (e.g., 7 days)

---

## Message Flow Architecture

```
+-----------------+         +------------------+         +-----------------+
|  Content Script | ------> |  Service Worker  | ------> |   Translate API       |
|  (content.js)   | <------ |  (background.js) | <------ |   (Gemini, etc) |
+-----------------+         +------------------+         +-----------------+
        |                           |
        v                           v
+-----------------+         +------------------+
|  DOM Overlay    |         |  Chrome Storage  |
|  (SVG/Canvas)   |         |  (Cache/Settings)|
+-----------------+         +------------------+
```

### Message Types
```javascript
// Content -> Background
{ type: "TRANSLATE_IMAGE", payload: { imageUrl, imageBlob } }
{ type: "CHECK_CACHE", payload: { cacheKey } }

// Background -> Content
{ type: "TRANSLATION_PROGRESS", payload: { status, percent } }
{ type: "TRANSLATION_COMPLETE", payload: { translatedBlob, textData } }
{ type: "TRANSLATION_ERROR", payload: { error } }
```

---

## Implementation Order

| Step | Task | Priority |
|------|------|----------|
| 1 | Create manifest.json + folder structure | [High] High |
| 2 | Build popup.html with provider selection | [High] High |
| 3 | Implement settings persistence (chrome.storage) | [High] High |
| 4 | Add content script image discovery | [High] High |
| 5 | Build service worker message handling | [High] High |
| 7 | Add progress overlay to content script | [Medium] Medium |
| 8 | Implement OpenRouter provider | [Medium] Medium |
| 9 | Add caching layer | [Medium] Medium |
| 10 | **Add pipeline mode toggle (Cloud/Local)** | [Medium] Medium |
| 11 | **Add local backend URL configuration** | [Medium] Medium |
| 12 | **Implement LocalBackendProvider class** | [Medium] Medium |
| 13 | **Add detector model selection UI** | [Low] Low |
| 14 | **Add OCR model selection UI** | [Low] Low |
| 15 | **Add inpainter model selection UI** | [Low] Low |
| 16 | Add custom provider support | [Low] Low |
| 17 | Implement screenshot fallback | [Low] Low |
| 18 | Add font customization | [Low] Low |
| 19 | **Add Docker setup documentation** | [Low] Low |

---

## Phase 7: Testing

### 7.1 Unit Tests
```
tests/
+-- unit/
|   +-- api.test.js         # Provider abstraction tests
|   +-- cache.test.js       # Cache key generation, storage
|   +-- utils.test.js       # Helper function tests
|   +-- settings.test.js    # Settings validation
```

**Test Cases:**
- Provider configuration validation
- Cache key generation consistency
- Settings schema validation
- Image hash generation

### 7.2 Integration Tests
```
tests/
+-- integration/
|   +-- gemini.test.js      # Gemini API integration
|   +-- openrouter.test.js  # OpenRouter API integration
|   +-- messaging.test.js   # Content <-> Background messaging
|   +-- storage.test.js     # Chrome storage operations
```

**Test Cases:**
- API authentication flow
- OCR request/response handling
- Translation request/response handling
- Message passing between scripts
- Settings persistence and retrieval

### 7.3 E2E Tests (Playwright/Puppeteer)
```
tests/
+-- e2e/
|   +-- extension.spec.js   # Full extension workflow
|   +-- popup.spec.js       # Popup UI interactions
|   +-- content.spec.js     # Content script behavior
```

**Test Scenarios:**
| Test | Description |
|------|-------------|
| Load Extension | Verify extension loads without errors |
| Popup Opens | Click icon -> popup displays correctly |
| Save Settings | Change provider/model -> settings persist |
| Image Detection | Navigate to manga site -> images detected |
| Translation Flow | Click translate -> progress -> result displayed |
| Cache Hit | Translate same image twice -> second is instant |
| Toggle Original | Click overlay -> show/hide original text |
| Error Handling | Invalid API key -> error message shown |

### 7.4 Manual Test Checklist

#### Popup UI
- [ ] Provider dropdown changes model hint text
- [ ] API key field masks input
- [ ] Font size slider updates preview
- [ ] Settings save on change
- [ ] Clear cache button works

#### Content Script
- [ ] Images detected on page load
- [ ] Lazy-loaded images detected
- [ ] Progress overlay appears bottom-left
- [ ] Translated text renders correctly
- [ ] Original/translated toggle works

#### API Integration
- [ ] Gemini API returns valid OCR
- [ ] OpenRouter API returns valid translation
- [ ] Rate limit handling (429 errors)
- [ ] Network error recovery

### 7.5 Test Sites
| Site | Type | Test Focus |
|------|------|------------|
| manga-raw.club | Raw manga | Japanese OCR accuracy |
| mangadex.org | Multi-language | Image capture, lazy-load |
| webtoons.com | Webtoon format | Vertical scroll, long images |
| rawkuma.com | Protected images | Screenshot fallback |

### 7.6 Testing Tools
- **Jest** -- Unit tests
- **Playwright** -- E2E browser automation
- **Sinon** -- Mocking Chrome APIs
- **chrome-mock** -- Mock chrome.* namespace
- **MSW (Mock Service Worker)** -- API mocking

### 7.7 CI/CD Pipeline
```yaml
# .github/workflows/test.yml
name: Test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: npm ci
      - run: npm run test:unit
      - run: npm run test:integration
      - run: npx playwright install --with-deps
      - run: npm run test:e2e
```

---

## Phase 8: Git Workflow & Merge Strategy

### 8.1 Branch Structure
```
main                 # Production-ready code
+-- develop          # Integration branch
+-- feature/*        # New features
+-- bugfix/*         # Bug fixes
+-- hotfix/*         # Urgent production fixes
+-- release/*        # Release preparation
```

### 8.2 Branch Naming Convention
```
feature/image-capture
feature/gemini-integration
feature/popup-ui
bugfix/cache-key-collision
hotfix/api-auth-fix
release/v1.0.0
```

### 8.3 Git Flow
```
1. Create feature branch from develop
   git checkout develop
   git pull origin develop
   git checkout -b feature/new-feature

2. Commit changes (conventional commits)
   git add .
   git commit -m "feat(content): add image detection"

3. Push and create PR
   git push origin feature/new-feature

4. Merge to develop (squash or merge)
   git checkout develop
   git merge --no-ff feature/new-feature

5. Release flow
   git checkout -b release/v1.0.0 develop
   # bump version, final testing
   git checkout main
   git merge --no-ff release/v1.0.0
   git tag -a v1.0.0 -m "Release v1.0.0"
```

### 8.4 Commit Message Convention
```
<type>(<scope>): <description>

Types:
- feat:     New feature
- fix:      Bug fix
- docs:     Documentation
- style:    Formatting (no code change)
- refactor: Code restructure
- test:     Adding tests
- chore:    Build/config changes

Examples:
feat(popup): add provider selection dropdown
fix(content): resolve CORS image capture issue
docs(readme): add installation instructions
test(api): add Gemini integration tests
```

### 8.5 Merge Strategy

| Branch | Merge Into | Strategy | CI Required |
|--------|------------|----------|-------------|
| feature/* | develop | Squash merge | [OK] Tests pass |
| bugfix/* | develop | Squash merge | [OK] Tests pass |
| develop | release/* | Merge commit | [OK] All tests |
| release/* | main | Merge commit | [OK] All tests |
| hotfix/* | main + develop | Cherry-pick | [OK] Tests pass |

### 8.6 Pull Request Template
```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Feature
- [ ] Bug fix
- [ ] Documentation
- [ ] Refactor

## Checklist
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] No console errors
- [ ] Extension loads correctly
- [ ] Tested on target sites

## Screenshots (if UI changes)
```

### 8.7 Protected Branch Rules (GitHub)
```yaml
main:
  - Require pull request reviews (1+)
  - Require status checks (test, lint)
  - Require branches to be up to date
  - No force push
  - No deletions

develop:
  - Require status checks (test)
  - Require branches to be up to date
```

### 8.8 Release Checklist
- [ ] All features merged to develop
- [ ] Create release branch from develop
- [ ] Update version in manifest.json
- [ ] Update CHANGELOG.md
- [ ] Run full test suite
- [ ] Merge to main
- [ ] Create GitHub release with tag
- [ ] Build extension package (.zip)
- [ ] Submit to Chrome Web Store

---

## Open Questions


2. **Rendering Approach?**
   - Option A: SVG overlay (toggle-able, non-destructive)
   - Option B: Canvas replacement (cleaner look)
   - **Recommendation**: SVG overlay with original/translated toggle

3. **SFX Handling?**
   - Basic: Translate SFX as text
   - Advanced: Use inpainting + re-render
   - **Recommendation**: Start basic, add advanced later

---

## References

### Core Pipeline
| Repo | Purpose | Stars |
|------|---------|-------|
| [zyddnys/manga-image-translator](https://github.com/zyddnys/manga-image-translator) | SOTA full pipeline (detection, OCR, translation, inpainting) | 9.2k |
| [ogkalu2/comic-translate](https://github.com/ogkalu2/comic-translate) | Desktop app with GUI, multi-language | 2.3k |

### OCR Models
| Repo | Purpose |
|------|---------|
| [kha-white/manga-ocr](https://github.com/kha-white/manga-ocr) | Japanese manga OCR (Transformer-based) |
| [jzhang533/PaddleOCR-VL-For-Manga](https://github.com/jzhang533/PaddleOCR-VL-For-Manga) | VLM fine-tuned for manga (70% accuracy) |
| [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | Multi-language OCR (PPOCRv5) |

### Text Detection
| Repo | Purpose |
|------|---------|
| [dmMaze/comic-text-detector](https://github.com/dmMaze/comic-text-detector) | DBNet + YOLO for manga text detection |
| [dmMaze/BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) | Computer-aided manga translation |

### Inpainting Models
| Repo | Purpose |
|------|---------|
| [advimman/lama](https://github.com/advimman/lama) | LaMa inpainting (industry standard) |
| [dreMaz/AnimeMangaInpainting](https://huggingface.co/dreMaz/AnimeMangaInpainting) | LaMa fine-tuned on 300k manga/anime |
| [Sanster/lama-cleaner](https://github.com/Sanster/lama-cleaner) | Easy-to-use LaMa wrapper |

### Extension Examples
| Repo | Purpose |
|------|---------|
| [hcharbonnier/manga-image-translator-chrome-extension](https://github.com/hcharbonnier/manga-image-translator-chrome-extension) | Chrome extension connecting to manga-image-translator |
| [Kirogii/Manga-Translation-Suite](https://github.com/Kirogii/Manga-Translation-Suite) | Flask backend pattern |

---

*Created: January 20, 2026*
*Updated: January 20, 2026 -- Added Phase 5B (Pipeline Configuration), Phase 5C (Pipeline Settings UI)*
