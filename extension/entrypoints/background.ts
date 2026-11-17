import { defineBackground } from 'wxt/utils/define-background';
import { registerBackground } from '../background';

// MV3 service worker: all listeners are registered synchronously inside
// registerBackground() so Chrome can wake the worker for any event.
export default defineBackground(() => {
  registerBackground();
});
