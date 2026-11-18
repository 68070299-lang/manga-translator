/**
 * Small FIFO scheduler for service-worker translation jobs.
 *
 * The queue deliberately keeps its state in memory. Cache storage remains the
 * source of truth when a Manifest V3 worker is suspended and restarted.
 */

/** Callback a running job uses to report `(status, percent)` to all subscribers. */
export type ProgressNotifier = (status: string, percent: number) => void;

export type QueueTask<T> = (notify: ProgressNotifier) => T | PromiseLike<T>;

export interface QueueSubscriber {
  onProgress?: ProgressNotifier;
}

interface QueueEntry<T = unknown> {
  key: string;
  task: QueueTask<T>;
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
  subscribers: Set<QueueSubscriber>;
}

export class TranslationQueue {
  readonly concurrency: number;
  private readonly pending: QueueEntry[] = [];
  private running = 0;
  private readonly inFlight = new Map<string, QueueEntry>();

  constructor({ concurrency = 1 }: { concurrency?: number } = {}) {
    if (!Number.isFinite(concurrency) || concurrency < 1) {
      throw new TypeError('Translation queue concurrency must be at least 1');
    }
    this.concurrency = Math.floor(concurrency);
  }

  /**
   * Queue a job. A second `enqueue` with a key that is already queued or
   * running subscribes to that job and returns the same promise.
   */
  enqueue<T>(
    key: string,
    task: QueueTask<T>,
    subscriber: QueueSubscriber | null = null,
  ): Promise<T> {
    if (typeof task !== 'function') {
      throw new TypeError('Translation queue task must be a function');
    }

    const normalizedKey = String(key || '');
    if (!normalizedKey) {
      throw new TypeError('Translation queue key must not be empty');
    }

    const existing = this.inFlight.get(normalizedKey);
    if (existing) {
      addSubscriber(existing, subscriber);
      return existing.promise as Promise<T>;
    }

    // Not Promise.withResolvers: that needs Chrome 119 and the floor is 110.
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((onResolve, onReject) => {
      resolve = onResolve;
      reject = onReject;
    });
    const entry: QueueEntry<T> = {
      key: normalizedKey,
      task,
      promise,
      resolve,
      reject,
      subscribers: new Set(),
    };

    addSubscriber(entry as QueueEntry, subscriber);
    this.inFlight.set(normalizedKey, entry as QueueEntry);
    this.pending.push(entry as QueueEntry);
    this.drain();

    return promise;
  }

  private notify(entry: QueueEntry, status: string, percent: number): void {
    for (const subscriber of entry.subscribers) {
      if (typeof subscriber.onProgress !== 'function') continue;
      try {
        subscriber.onProgress(status, percent);
      } catch (error) {
        // A closed tab or a subscriber bug must not fail the shared job.
        console.warn(
          '[TranslationQueue] progress subscriber failed:',
          error instanceof Error ? error.message : error,
        );
      }
    }
  }

  private drain(): void {
    while (this.running < this.concurrency) {
      const entry = this.pending.shift();
      if (!entry) return;
      this.running += 1;

      Promise.resolve()
        .then(() => entry.task((status, percent) => this.notify(entry, status, percent)))
        .then(
          (result) => {
            entry.resolve(result);
            this.finish(entry);
          },
          (error) => {
            entry.reject(error);
            this.finish(entry);
          },
        );
    }
  }

  private finish(entry: QueueEntry): void {
    this.running -= 1;
    if (this.inFlight.get(entry.key) === entry) {
      this.inFlight.delete(entry.key);
    }
    this.drain();
  }

  /** Whether a job with this key is queued or running. */
  has(key: string): boolean {
    return this.inFlight.has(String(key || ''));
  }

  get pendingCount(): number {
    return this.pending.length;
  }

  get runningCount(): number {
    return this.running;
  }

  get inFlightCount(): number {
    return this.inFlight.size;
  }
}

function addSubscriber(entry: QueueEntry, subscriber: QueueSubscriber | null): void {
  if (subscriber && typeof subscriber === 'object') {
    entry.subscribers.add(subscriber);
  }
}
