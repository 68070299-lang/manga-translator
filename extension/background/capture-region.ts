/**
 * CAPTURE_REGION: screenshot the sender's visible tab and return only the
 * selected crop as PNG. The full screenshot is never stored or sent.
 */
import { browser } from 'wxt/browser';
import { calibrateCaptureRegion } from '../lib/region-capture';
import { base64ToBlob, blobToBase64, errorMessage } from '../lib/utils';
import type { CaptureRegionRequest, WorkerResponseMap } from '../shared/messages';

/** The part of the runtime message sender that region capture needs. */
export interface CaptureSender {
  tab?: { id?: number; windowId?: number; active?: boolean };
  frameId?: number;
}

export async function handleCaptureRegion(
  message: Pick<CaptureRegionRequest, 'rect' | 'viewport'>,
  sender: CaptureSender,
): Promise<WorkerResponseMap['CAPTURE_REGION']> {
  const tab = sender.tab;
  if (!tab?.id || sender.frameId) {
    return { success: false, error: 'Region capture must start from the top page of a tab' };
  }
  // captureVisibleTab reads whichever tab is active, which must be the requester.
  if (!tab.active) {
    return { success: false, error: 'Region capture needs the tab to be visible' };
  }

  try {
    const dataUrl = await browser.tabs.captureVisibleTab(tab.windowId as number, { format: 'png' });
    const bitmap = await createImageBitmap(base64ToBlob(dataUrl, 'image/png'));
    try {
      // The scale comes from screenshot size / viewport size, not devicePixelRatio.
      const region = calibrateCaptureRegion(message.rect, message.viewport, bitmap);
      const canvas = new OffscreenCanvas(region.width, region.height);
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Could not create a canvas context for the capture');
      context.drawImage(
        bitmap,
        region.x,
        region.y,
        region.width,
        region.height,
        0,
        0,
        region.width,
        region.height,
      );
      const cropped = await canvas.convertToBlob({ type: 'image/png' });
      return {
        success: true,
        base64: await blobToBase64(cropped),
        width: region.width,
        height: region.height,
      };
    } finally {
      bitmap.close();
    }
  } catch (error) {
    console.error('[Service Worker] Region capture error:', error);
    return { success: false, error: errorMessage(error) };
  }
}
