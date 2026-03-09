import axios from 'axios';
import i18n from '../i18n';

const api = axios.create({
  baseURL: '/api',
  timeout: 15000,
});

const DASHBOARD_AUTH_STORAGE_KEY = 'memory-palace.dashboardAuth';

// Handle URI encoding for resource IDs which might contain special chars
const encodeId = (id) => encodeURIComponent(id);

const normalizeMaintenanceAuth = (runtimeConfig) => {
  if (!runtimeConfig || typeof runtimeConfig !== 'object') return null;
  const rawKey = runtimeConfig.maintenanceApiKey ?? runtimeConfig.mcpApiKey;
  const key = typeof rawKey === 'string' ? rawKey.trim() : '';
  if (!key) return null;
  const rawMode =
    runtimeConfig.maintenanceApiKeyMode ??
    runtimeConfig.mcpApiKeyMode ??
    'header';
  const mode =
    String(rawMode).trim().toLowerCase() === 'bearer' ? 'bearer' : 'header';
  return { key, mode };
};

const readWindowRuntimeMaintenanceAuth = () => {
  if (typeof window === 'undefined') return null;
  const runtimeConfig =
    window.__MEMORY_PALACE_RUNTIME__ || window.__MCP_RUNTIME_CONFIG__ || null;
  return normalizeMaintenanceAuth(runtimeConfig);
};

const readStoredMaintenanceAuth = () => {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(DASHBOARD_AUTH_STORAGE_KEY);
    if (!raw) return null;
    return normalizeMaintenanceAuth(JSON.parse(raw));
  } catch (_error) {
    return null;
  }
};

export const getMaintenanceAuthState = () => {
  const runtimeAuth = readWindowRuntimeMaintenanceAuth();
  if (runtimeAuth) {
    return { ...runtimeAuth, source: 'runtime' };
  }

  const storedAuth = readStoredMaintenanceAuth();
  if (storedAuth) {
    return { ...storedAuth, source: 'stored' };
  }

  return null;
};

export const saveStoredMaintenanceAuth = (key, mode = 'header') => {
  if (typeof window === 'undefined') return null;
  const normalized = normalizeMaintenanceAuth({
    maintenanceApiKey: key,
    maintenanceApiKeyMode: mode,
  });
  if (!normalized) return null;
  window.localStorage.setItem(
    DASHBOARD_AUTH_STORAGE_KEY,
    JSON.stringify({
      maintenanceApiKey: normalized.key,
      maintenanceApiKeyMode: normalized.mode,
    })
  );
  return getMaintenanceAuthState();
};

export const clearStoredMaintenanceAuth = () => {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(DASHBOARD_AUTH_STORAGE_KEY);
};

const readRuntimeMaintenanceAuth = () => {
  const authState = getMaintenanceAuthState();
  if (!authState) return null;
  return { key: authState.key, mode: authState.mode };
};

const normalizeProtectedPath = (pathname) => {
  if (!pathname || typeof pathname !== 'string') return '';
  if (pathname.startsWith('/api/')) return pathname.slice('/api'.length);
  return pathname;
};

const isProtectedPath = (pathname) => {
  const normalizedPath = normalizeProtectedPath(pathname);
  if (normalizedPath.startsWith('/maintenance/')) return true;
  if (normalizedPath.startsWith('/review/')) return true;
  if (normalizedPath.startsWith('/browse/')) return true;
  return false;
};

const isProtectedApiRequest = (config) => {
  const url = config?.url;
  if (!url || typeof url !== 'string') return false;
  if (typeof window === 'undefined') return false;
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.origin !== window.location.origin) return false;
    return isProtectedPath(parsed.pathname, config?.method);
  } catch (_error) {
    return false;
  }
};

api.interceptors.request.use((config) => {
  const runtimeAuth = readRuntimeMaintenanceAuth();
  if (!runtimeAuth || !isProtectedApiRequest(config)) {
    return config;
  }

  const headers = config.headers || {};
  if (runtimeAuth.mode === 'bearer') {
    headers.Authorization = `Bearer ${runtimeAuth.key}`;
  } else {
    headers['X-MCP-API-Key'] = runtimeAuth.key;
  }
  return { ...config, headers };
});

const normalizeMemoryNode = (node) => {
  if (!node || typeof node !== 'object') return null;
  return {
    ...node,
    gist_text: node.gist_text || null,
    gist_method: node.gist_method || null,
    gist_quality:
      node.gist_quality == null || Number.isNaN(Number(node.gist_quality))
        ? null
        : Number(node.gist_quality),
    source_hash: node.source_hash || null,
  };
};

const normalizeMemoryChild = (child) => {
  if (!child || typeof child !== 'object') return child;
  return {
    ...child,
    gist_text: child.gist_text || null,
    gist_method: child.gist_method || null,
    gist_quality:
      child.gist_quality == null || Number.isNaN(Number(child.gist_quality))
        ? null
        : Number(child.gist_quality),
    source_hash: child.source_hash || null,
  };
};

const normalizeMemoryNodePayload = (payload) => {
  const safe = payload && typeof payload === 'object' ? payload : {};
  return {
    ...safe,
    node: normalizeMemoryNode(safe.node),
    children: Array.isArray(safe.children)
      ? safe.children.map((child) => normalizeMemoryChild(child))
      : [],
    breadcrumbs: Array.isArray(safe.breadcrumbs) ? safe.breadcrumbs : [],
  };
};

const normalizeApiErrorCode = (value) => {
  if (typeof value !== 'string') return null;
  const normalized = value.trim().toLowerCase();
  if (!normalized) return null;
  if (!/^[a-z0-9][a-z0-9_.-]*$/.test(normalized)) return null;
  return normalized;
};

const translateApiErrorCode = (value) => {
  const code = normalizeApiErrorCode(value);
  if (!code) return null;
  const translated = i18n.t(`apiErrors.codes.${code}`);
  return translated && translated !== `apiErrors.codes.${code}` ? translated : null;
};

const translateApiErrorMessage = (value) => {
  if (typeof value !== 'string') return null;
  const normalized = value.trim();
  if (!normalized) return null;
  const codeTranslation = translateApiErrorCode(normalized);
  if (codeTranslation) return codeTranslation;
  const lowered = normalized.toLowerCase();
  if (lowered === 'network error') {
    return i18n.t('apiErrors.networkError');
  }
  if (lowered === 'failed to fetch') {
    return i18n.t('apiErrors.failedToFetch');
  }
  const statusCodeMatch = /^request failed with status code (\d{3})$/i.exec(normalized);
  if (statusCodeMatch) {
    return i18n.t('apiErrors.statusCode', { status: statusCodeMatch[1] });
  }
  const timeoutMatch = /^timeout of (\d+)ms exceeded$/i.exec(normalized);
  if (timeoutMatch) {
    return i18n.t('apiErrors.timeoutExceeded', { ms: timeoutMatch[1] });
  }
  const translated = i18n.t(`apiErrors.messages.${normalized.toLowerCase()}`);
  return translated && translated !== `apiErrors.messages.${normalized.toLowerCase()}`
    ? translated
    : null;
};

export const extractApiErrorCode = (error) => {
  const detail = error?.response?.data?.detail;
  const codes = [];
  const pushCode = (value) => {
    const code = normalizeApiErrorCode(value);
    if (!code || codes.includes(code)) return;
    codes.push(code);
  };

  if (typeof detail === 'string') {
    pushCode(detail);
  } else if (detail && typeof detail === 'object') {
    pushCode(detail.code);
    pushCode(detail.error);
    pushCode(detail.reason);
  }
  return codes[0] || null;
};

export const extractApiError = (
  error,
  fallback = i18n.t('apiErrors.requestFailed')
) => {
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) {
    return translateApiErrorMessage(detail) || detail;
  }
  if (detail && typeof detail === 'object') {
    const parts = [];
    const pushPart = (value) => {
      if (typeof value !== 'string') return;
      const normalized = value.trim();
      if (!normalized || parts.includes(normalized)) return;
      parts.push(normalized);
    };

    pushPart(translateApiErrorCode(detail.error) || detail.error);
    pushPart(translateApiErrorCode(detail.reason) || detail.reason);
    if (typeof detail.operation === 'string' && detail.operation.trim()) {
      pushPart(i18n.t('apiErrors.operation', { operation: detail.operation.trim() }));
    }
    pushPart(translateApiErrorMessage(detail.message) || detail.message);
    const errorCode = normalizeApiErrorCode(detail.error);
    const reasonCode = normalizeApiErrorCode(detail.reason);
    const isAuthError =
      error?.response?.status === 401
      || errorCode === 'maintenance_auth_failed'
      || errorCode === 'mcp_sse_auth_failed'
      || reasonCode === 'invalid_or_missing_api_key'
      || reasonCode === 'api_key_not_configured'
      || reasonCode === 'insecure_local_override_requires_loopback';
    if (isAuthError) {
      pushPart(i18n.t('apiErrors.authHint'));
    }
    if (parts.length > 0) {
      return parts.join(' | ');
    }
    try {
      return JSON.stringify(detail);
    } catch (_error) {
      return fallback;
    }
  }
  const message = error?.message;
  if (typeof message === 'string' && message.trim()) {
    return translateApiErrorMessage(message) || message;
  }
  return fallback;
};

// ============ Review API (Session & Snapshot) ============

export const getSessions = () => api.get('/review/sessions').then(res => res.data);

export const getSnapshots = (sessionId) => 
  api.get(`/review/sessions/${sessionId}/snapshots`).then(res => res.data);

export const getDiff = (sessionId, resourceId) => 
  api.get(`/review/sessions/${sessionId}/diff/${encodeId(resourceId)}`).then(res => res.data);

export const rollbackResource = (sessionId, resourceId) => 
  api.post(`/review/sessions/${sessionId}/rollback/${encodeId(resourceId)}`, {}).then(res => res.data);

export const approveSnapshot = (sessionId, resourceId) => 
  api.delete(`/review/sessions/${sessionId}/snapshots/${encodeId(resourceId)}`).then(res => res.data);

export const clearSession = (sessionId) => 
  api.delete(`/review/sessions/${sessionId}`).then(res => res.data);

// ============ Catalog API (SQLite/URI Model) ============

export const getMemoryNode = (params, requestConfig = {}) => {
  const mergedParams = {
    ...(requestConfig?.params || {}),
    ...(params || {}),
  };
  return api
    .get('/browse/node', { ...requestConfig, params: mergedParams })
    .then(res => normalizeMemoryNodePayload(res.data));
};

export const createMemoryNode = (payload) =>
  api.post('/browse/node', payload).then(res => res.data);

export const updateMemoryNode = (path, domain, payload) =>
  api.put('/browse/node', payload, { params: { path, domain } }).then(res => res.data);

export const deleteMemoryNode = (path, domain) =>
  api.delete('/browse/node', { params: { path, domain } }).then(res => res.data);

// ============ Observability API ============

export const getObservabilitySummary = () =>
  api.get('/maintenance/observability/summary').then(res => res.data);

export const runObservabilitySearch = (payload) =>
  api.post('/maintenance/observability/search', payload).then(res => res.data);

export const getIndexWorkerStatus = () =>
  api.get('/maintenance/index/worker').then(res => res.data);

export const getIndexJob = (jobId) =>
  api.get(`/maintenance/index/job/${encodeId(jobId)}`).then(res => res.data);

export const cancelIndexJob = (jobId, payload = {}) =>
  api.post(`/maintenance/index/job/${encodeId(jobId)}/cancel`, payload).then(res => res.data);

export const retryIndexJob = (jobId, payload = {}) =>
  api.post(`/maintenance/index/job/${encodeId(jobId)}/retry`, payload).then(res => res.data);

export const triggerIndexRebuild = (params = {}) =>
  api.post('/maintenance/index/rebuild', null, { params }).then(res => res.data);

export const triggerMemoryReindex = (memoryId, params = {}) =>
  api.post(`/maintenance/index/reindex/${memoryId}`, null, { params }).then(res => res.data);

export const triggerSleepConsolidation = (params = {}) =>
  api.post('/maintenance/index/sleep-consolidation', null, { params }).then(res => res.data);

// ============ Vitality Cleanup API ============

export const triggerVitalityDecay = (params = {}) =>
  api.post('/maintenance/vitality/decay', null, { params }).then(res => res.data);

export const queryVitalityCleanupCandidates = (payload) =>
  api.post('/maintenance/vitality/candidates/query', payload).then(res => res.data);

export const prepareVitalityCleanup = (payload) =>
  api.post('/maintenance/vitality/cleanup/prepare', payload).then(res => res.data);

export const confirmVitalityCleanup = (payload) =>
  api.post('/maintenance/vitality/cleanup/confirm', payload).then(res => res.data);

// ============ Orphan Maintenance API ============

export const listOrphanMemories = () =>
  api.get('/maintenance/orphans').then(res => res.data);

export const getOrphanMemoryDetail = (memoryId) =>
  api.get(`/maintenance/orphans/${encodeId(memoryId)}`).then(res => res.data);

export const deleteOrphanMemory = (memoryId) =>
  api.delete(`/maintenance/orphans/${encodeId(memoryId)}`).then(res => res.data);
