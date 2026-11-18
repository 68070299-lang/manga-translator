import { describe, expect, it, vi } from 'vitest';
import { type ProgressNotifier, TranslationQueue } from '../../extension/lib/translation-queue';

describe('TranslationQueue', () => {
  it('deduplicates identical keys and notifies every subscriber', async () => {
    const queue = new TranslationQueue({ concurrency: 1 });
    const progressOne = vi.fn();
    const progressTwo = vi.fn();
    let release: () => void = () => {};

    const task = vi.fn(
      async (notify: ProgressNotifier) =>
        new Promise<string>((resolve) => {
          release = () => {
            notify('Translating...', 50);
            resolve('translated');
          };
        }),
    );

    const first = queue.enqueue('same-image', task, { onProgress: progressOne });
    const second = queue.enqueue('same-image', task, { onProgress: progressTwo });
    await Promise.resolve();

    expect(first).toBe(second);
    expect(task).toHaveBeenCalledTimes(1);
    expect(queue.runningCount).toBe(1);

    release();
    await expect(first).resolves.toBe('translated');

    expect(progressOne).toHaveBeenCalledWith('Translating...', 50);
    expect(progressTwo).toHaveBeenCalledWith('Translating...', 50);
    expect(queue.inFlightCount).toBe(0);
  });

  it('respects the configured concurrency and drains FIFO work', async () => {
    const queue = new TranslationQueue({ concurrency: 2 });
    const active: string[] = [];
    let peak = 0;
    const releases: Array<() => void> = [];

    const task = (id: string) =>
      queue.enqueue(
        id,
        async () =>
          new Promise<string>((resolve) => {
            active.push(id);
            peak = Math.max(peak, active.length);
            releases.push(() => {
              active.splice(active.indexOf(id), 1);
              resolve(id);
            });
          }),
      );

    const first = task('one');
    const second = task('two');
    const third = task('three');
    await Promise.resolve();

    expect(active).toEqual(['one', 'two']);
    expect(queue.pendingCount).toBe(1);
    expect(peak).toBe(2);

    releases.shift()?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(active).toEqual(['two', 'three']);

    releases.shift()?.();
    releases.shift()?.();
    await expect(Promise.all([first, second, third])).resolves.toEqual(['one', 'two', 'three']);
  });

  it('removes failed jobs so a later retry can run', async () => {
    const queue = new TranslationQueue({ concurrency: 1 });
    await expect(
      queue.enqueue('retryable', async () => {
        throw new Error('temporary failure');
      }),
    ).rejects.toThrow('temporary failure');

    expect(queue.has('retryable')).toBe(false);
    await expect(queue.enqueue('retryable', async () => 'recovered')).resolves.toBe('recovered');
  });

  it('keeps distinct keys independent', async () => {
    const queue = new TranslationQueue({ concurrency: 1 });
    const first = queue.enqueue('image:normal', async () => 'normal');
    const force = queue.enqueue('image:force:1', async () => 'force');

    await expect(first).resolves.toBe('normal');
    await expect(force).resolves.toBe('force');
  });
});
