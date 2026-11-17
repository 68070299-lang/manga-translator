/**
 * Small FIFO scheduler for service-worker translation jobs.
 *
 * The queue deliberately keeps its state in memory. Cache storage remains the
 * source of truth when a Manifest V3 worker is suspended and restarted.
 */
(function(root) {
  'use strict';

  class TranslationQueue {
    constructor({ concurrency = 1 } = {}) {
      if (!Number.isFinite(concurrency) || concurrency < 1) {
        throw new TypeError('Translation queue concurrency must be at least 1');
      }

      this.concurrency = Math.floor(concurrency);
      this.pending = [];
      this.running = 0;
      this.inFlight = new Map();
    }

    enqueue(key, task, subscriber = null) {
      if (typeof task !== 'function') {
        throw new TypeError('Translation queue task must be a function');
      }

      const normalizedKey = String(key || '');
      if (!normalizedKey) {
        throw new TypeError('Translation queue key must not be empty');
      }

      const existing = this.inFlight.get(normalizedKey);
      if (existing) {
        this.addSubscriber(existing, subscriber);
        return existing.promise;
      }

      let resolvePromise;
      let rejectPromise;
      const promise = new Promise((resolve, reject) => {
        resolvePromise = resolve;
        rejectPromise = reject;
      });

      const entry = {
        key: normalizedKey,
        task,
        promise,
        resolve: resolvePromise,
        reject: rejectPromise,
        subscribers: new Set()
      };

      this.addSubscriber(entry, subscriber);
      this.inFlight.set(normalizedKey, entry);
      this.pending.push(entry);
      this.drain();

      return promise;
    }

    addSubscriber(entry, subscriber) {
      if (subscriber && typeof subscriber === 'object') {
        entry.subscribers.add(subscriber);
      }
    }

    notify(entry, status, percent) {
      entry.subscribers.forEach(subscriber => {
        if (typeof subscriber.onProgress !== 'function') return;
        try {
          subscriber.onProgress(status, percent);
        } catch (error) {
          // A closed tab or a subscriber bug must not fail the shared job.
          console.warn('[TranslationQueue] progress subscriber failed:', error?.message || error);
        }
      });
    }

    drain() {
      while (this.running < this.concurrency && this.pending.length > 0) {
        const entry = this.pending.shift();
        this.running += 1;

        Promise.resolve()
          .then(() => entry.task((status, percent) => this.notify(entry, status, percent)))
          .then(
            result => {
              entry.resolve(result);
              this.finish(entry);
            },
            error => {
              entry.reject(error);
              this.finish(entry);
            }
          );
      }
    }

    finish(entry) {
      this.running -= 1;
      if (this.inFlight.get(entry.key) === entry) {
        this.inFlight.delete(entry.key);
      }
      this.drain();
    }

    has(key) {
      return this.inFlight.has(String(key || ''));
    }

    get pendingCount() {
      return this.pending.length;
    }

    get runningCount() {
      return this.running;
    }

    get inFlightCount() {
      return this.inFlight.size;
    }
  }

  const exported = { TranslationQueue };
  if (root) root.MangaTranslatorTranslationQueue = exported;
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
})(typeof globalThis !== 'undefined' ? globalThis : this);
