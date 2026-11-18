import { defineContentScript } from 'wxt/utils/define-content-script';
import { bootContentScript } from '../../content/main';
import './style.css';

// Runs in the top frame only (no allFrames), on every page. Nothing beyond the
// per-hostname activation check happens until the site is enabled; see
// content/main.ts.
export default defineContentScript({
  matches: ['<all_urls>'],
  runAt: 'document_idle',
  // WXT otherwise window.postMessage()s "<extension id>:content:wxt:content-script-started"
  // on every page, letting any site detect the extension and read its ID.
  noScriptStartedPostMessage: true,
  main() {
    bootContentScript();
  },
});
