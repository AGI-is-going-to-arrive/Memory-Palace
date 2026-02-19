import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  timeout: 15000,
});

// Handle URI encoding for resource IDs which might contain special chars
const encodeId = (id) => encodeURIComponent(id);

const readRuntimeMaintenanceAuth = () => {
  if (typeof window === 'undefined') return null;
  const runtimeConfig =
    window.__MEMORY_PALACE_RUNTIME__ || window.__MCP_RUNTIME_CONFIG__ || null;
  if (!runtimeConfig || typeof runtimeConfig !== 'object') return null;
  const rawKey = runtimeConfig.maintenanceApiKey ?? runtimeConfig.mcpApiKey;
  const key = typeof rawKey === 'string' ? rawKey.trim() : '';
  if (!key) return null;
  const rawMode = runtimeConfig.maintenanceApiKeyMode ?? runtimeConfig.mcpApiKeyMode ?? 'header';
  const mode = String(rawMode).trim().toLowerCase() === 'bearer' ? 'bearer' : 'header';
  return { key, mode };
};

const normalizeProtectedPath = (pathname) => {
  if (!pathname || typeof pathname !== 'string') return '';
  if (pathname.startsWith('/api/')) return pathname.slice('/api'.length);
  return pathname;
};

const isProtectedPath = (pathname, method) => {
  const normalizedPath = normalizeProtectedPath(pathname);
  if (normalizedPath.startsWith('/maintenance/')) return true;
  if (normalizedPath.startsWith('/review/')) return true;
  if (normalizedPath.startsWith('/browse/')) {
    const normalizedMethod = String(method || 'get').trim().toLowerCase();
    return !['get', 'head', 'options'].includes(normalizedMethod);
  }
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

export const extractApiError = (error, fallback = 'Request failed') => {
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) {
    return detail;
  }
  if (detail && typeof detail === 'object') {
    const parts = [];
    const pushPart = (value) => {
      if (typeof value !== 'string') return;
      const normalized = value.trim();
      if (!normalized || parts.includes(normalized)) return;
      parts.push(normalized);
    };

    pushPart(detail.error);
    pushPart(detail.reason);
    if (typeof detail.operation === 'string' && detail.operation.trim()) {
      pushPart(`operation=${detail.operation.trim()}`);
    }
    pushPart(detail.message);
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
    return message;
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
