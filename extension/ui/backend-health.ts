/**
 * Local backend `/health` probes used by the popup indicator and the options
 * "Test Connection" button. Both refuse non-loopback URLs, so the access token
 * is never sent off-machine.
 */
import { buildLocalBackendUrl } from '../lib/settings';

export const BACKEND_HEALTH_TIMEOUT_MS = 5000;
export const BACKEND_TOKEN_HEADER = 'X-Manga-Translator-Token';
export const LOOPBACK_ONLY_MESSAGE =
  'Only http://localhost, http://127.0.0.1 or http://[::1] URLs are allowed';

export interface ModelStatusInfo {
  status: string;
}
export type ModelStatusMap = Record<string, ModelStatusInfo>;

/** Subset of the backend `/health` JSON the extension reads. */
interface HealthPayload {
  status?: string;
  models_loaded?: boolean;
  models?: ModelStatusMap;
  device?: string;
}

function tokenHeaders(authToken: string): { headers?: Record<string, string> } {
  return authToken ? { headers: { [BACKEND_TOKEN_HEADER]: authToken } } : {};
}

/** `GET /health` with a timeout; the caller turns the outcome into UI text. */
async function fetchHealth(healthUrl: string, authToken: string): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), BACKEND_HEALTH_TIMEOUT_MS);
  try {
    return await fetch(healthUrl, {
      method: 'GET',
      ...tokenHeaders(authToken),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeout);
  }
}

// ---------------------------------------------------------------------------
// Popup indicator
// ---------------------------------------------------------------------------

export type IndicatorState = 'pending' | 'connected' | 'disconnected';

export interface BackendIndicator {
  state: IndicatorState;
  label: string;
  /** Per-model load states when the backend reported them. */
  models: ModelStatusMap | null;
}

const OFFLINE: BackendIndicator = { state: 'disconnected', label: 'Backend offline', models: null };

/** Probe the backend and describe it for the popup's status dot and label. */
export async function checkLocalBackend(
  url: string | undefined,
  authToken: string,
): Promise<BackendIndicator> {
  try {
    // Throws for non-loopback URLs, so the token never leaves the machine.
    const healthUrl = buildLocalBackendUrl(url, '/health');
    const response = await fetchHealth(healthUrl, authToken);

    if (response.ok) {
      const data = (await response.json()) as HealthPayload | null;
      const models = data?.models ?? null;
      if (data?.status === 'loading') {
        return { state: 'pending', label: 'Backend loading models', models };
      }
      if (data?.status === 'degraded' || data?.models_loaded === false) {
        return { state: 'pending', label: 'Backend degraded', models };
      }
      return { state: 'connected', label: 'Backend connected', models };
    }
    if (response.status === 401 || response.status === 503) {
      // The server is up but refused the request: say why instead of "offline".
      return {
        state: 'disconnected',
        label: response.status === 401 ? 'Backend rejected token' : 'Backend auth not configured',
        models: null,
      };
    }
    return OFFLINE;
  } catch {
    return OFFLINE;
  }
}

// ---------------------------------------------------------------------------
// Options "Test Connection"
// ---------------------------------------------------------------------------

export interface ConnectionStatus {
  message: string;
  type: 'success' | 'error' | 'testing';
}

/** Probe an already validated loopback URL and describe the result in words. */
export async function testBackendConnection(
  url: string,
  authToken: string,
): Promise<ConnectionStatus> {
  try {
    const response = await fetchHealth(`${url.replace(/\/$/, '')}/health`, authToken);

    if (response.ok) {
      const data = (await response.json()) as HealthPayload;
      if (data.status === 'loading') {
        return {
          message: 'Backend is still loading models; try again shortly',
          type: 'error',
        };
      }
      if (data.status !== 'ok' || data.models_loaded === false) {
        return { message: 'Backend models are not ready', type: 'error' };
      }
      return { message: `Connected - ${data.device || 'ready'}`, type: 'success' };
    }
    if (response.status === 401) {
      return {
        message: 'Backend rejected the access token (401); check the Backend Access Token',
        type: 'error',
      };
    }
    if (response.status === 503) {
      return {
        message:
          'Backend has no access token configured (503); set BACKEND_AUTH_TOKEN and restart it',
        type: 'error',
      };
    }
    return { message: `Server returned ${response.status}`, type: 'error' };
  } catch (error) {
    const aborted = (error as { name?: string } | null)?.name === 'AbortError';
    return { message: aborted ? 'Connection timeout' : 'Connection failed', type: 'error' };
  }
}
