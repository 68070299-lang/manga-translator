import { defineContentScript } from 'wxt/utils/define-content-script';
import './style.css';

// Placeholder: replaced by the content-script port (extension/content/*).
export default defineContentScript({
  matches: ['<all_urls>'],
  runAt: 'document_idle',
  main() {},
});
