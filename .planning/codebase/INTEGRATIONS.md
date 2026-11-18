# Integrations

- Chrome APIs: `runtime`, `storage`, `tabs`, `contextMenus`, `action`, and
  `declarativeNetRequest`. `declarativeNetRequest` is a required manifest
  permission with a static `rule_resources` entry (`extension/public/rules/bookwalker-cors.json`, built to `rules/bookwalker-cors.json`)
  plus transient session rules for a fixed CDN referrer allowlist.
- Local backend: loopback HTTP `/health`, `/translate`, `/detect`, `/ocr`, and
  `/paddle-ocr` endpoints (`backend/src/manga_backend/api/routes/`; the HTTP
  contract is unchanged by the TypeScript/WXT rewrite).
- Cloud providers: Gemini, OpenAI-compatible, OpenRouter, and custom base URLs.
- Persistent cache: `chrome.storage.local`, with settings split between sync and
  local secret storage.
- External comparison docs: reverse-engineered Ichigo v0.0.94 behavior, used only
  as a feature reference rather than a runtime dependency.
