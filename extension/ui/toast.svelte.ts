/** Transient message shown at the bottom of the popup and options pages. */
export type ToastType = 'info' | 'success' | 'error';

export interface ToastMessage {
  id: number;
  message: string;
  type: ToastType;
}

const SHOW_DELAY_MS = 10;
const VISIBLE_MS = 3000;
const FADE_MS = 300;

/**
 * One toast at a time: showing a new one replaces the current one. `visible`
 * drives the `.show` class so the CSS opacity transition runs in and out.
 */
export class ToastState {
  current = $state<ToastMessage | null>(null);
  visible = $state(false);

  #nextId = 0;
  #timers: ReturnType<typeof setTimeout>[] = [];

  show(message: string, type: ToastType = 'info'): void {
    this.#cancelTimers();
    this.#nextId += 1;
    this.current = { id: this.#nextId, message, type };
    this.visible = false;

    this.#later(() => {
      this.visible = true;
    }, SHOW_DELAY_MS);
    this.#later(() => {
      this.visible = false;
      this.#later(() => {
        this.current = null;
      }, FADE_MS);
    }, VISIBLE_MS);
  }

  /** Drop the toast and its timers (page teardown). */
  dispose(): void {
    this.#cancelTimers();
    this.current = null;
    this.visible = false;
  }

  #later(callback: () => void, delayMs: number): void {
    this.#timers.push(setTimeout(callback, delayMs));
  }

  #cancelTimers(): void {
    for (const timer of this.#timers) clearTimeout(timer);
    this.#timers = [];
  }
}
