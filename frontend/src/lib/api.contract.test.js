import { beforeEach, describe, expect, it, vi } from 'vitest';

const { mockApi, mockCreate, interceptorRef } = vi.hoisted(() => {
  const ref = { current: null };
  const apiInstance = {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
    interceptors: {
      request: {
        use: vi.fn((handler) => {
          ref.current = handler;
        }),
      },
    },
  };
  return {
    mockApi: apiInstance,
    mockCreate: vi.fn(() => apiInstance),
    interceptorRef: ref,
  };
});

vi.mock('axios', () => ({
  default: {
    create: mockCreate,
  },
}));

import {
  getMemoryNode,
  runObservabilitySearch,
  listOrphanMemories,
  getOrphanMemoryDetail,
  deleteOrphanMemory,
} from './api';

describe('api contract regression', () => {
  beforeEach(() => {
    mockApi.get.mockReset();
    mockApi.post.mockReset();
    mockApi.put.mockReset();
    mockApi.delete.mockReset();
    delete window.__MEMORY_PALACE_RUNTIME__;
    delete window.__MCP_RUNTIME_CONFIG__;
  });

  it('normalizes memory node gist fields from backend payload', async () => {
    mockApi.get.mockResolvedValue({
      data: {
        node: {
          uri: 'core://agent/index',
          gist_text: 'Index summary',
          gist_method: 'llm',
          gist_quality: '0.72',
          source_hash: 'abc',
        },
        children: [
          {
            uri: 'core://agent/index/child',
            gist_text: '',
            gist_method: '',
            gist_quality: 'NaN',
          },
        ],
        breadcrumbs: null,
      },
    });

    const result = await getMemoryNode({ path: 'agent/index', domain: 'core' });

    expect(mockApi.get).toHaveBeenCalledWith('/browse/node', {
      params: { path: 'agent/index', domain: 'core' },
    });
    expect(result.node.gist_quality).toBe(0.72);
    expect(result.node.source_hash).toBe('abc');
    expect(result.children[0].gist_text).toBeNull();
    expect(result.children[0].gist_method).toBeNull();
    expect(result.children[0].gist_quality).toBeNull();
    expect(result.children[0].source_hash).toBeNull();
    expect(result.breadcrumbs).toEqual([]);
  });

  it('preserves observability search response contract', async () => {
    mockApi.post.mockResolvedValue({
      data: {
        ok: true,
        mode_requested: 'hybrid',
        mode_applied: 'hybrid',
        degraded: false,
        results: [],
      },
    });

    const payload = { query: 'release plan', mode: 'hybrid', include_session: false };
    const result = await runObservabilitySearch(payload);

    expect(mockApi.post).toHaveBeenCalledWith('/maintenance/observability/search', payload);
    expect(result.ok).toBe(true);
    expect(result.mode_requested).toBe('hybrid');
    expect(result.mode_applied).toBe('hybrid');
    expect(result.degraded).toBe(false);
    expect(result.results).toEqual([]);
  });

  it('routes orphan maintenance APIs through unified client', async () => {
    mockApi.get.mockResolvedValueOnce({ data: [{ id: 1 }] });
    mockApi.get.mockResolvedValueOnce({ data: { id: 1, content: 'content' } });
    mockApi.delete.mockResolvedValueOnce({ data: { deleted: true } });

    const list = await listOrphanMemories();
    const detail = await getOrphanMemoryDetail(1);
    const deleted = await deleteOrphanMemory(1);

    expect(mockApi.get).toHaveBeenNthCalledWith(1, '/maintenance/orphans');
    expect(mockApi.get).toHaveBeenNthCalledWith(2, '/maintenance/orphans/1');
    expect(mockApi.delete).toHaveBeenCalledWith('/maintenance/orphans/1');
    expect(list).toEqual([{ id: 1 }]);
    expect(detail).toEqual({ id: 1, content: 'content' });
    expect(deleted).toEqual({ deleted: true });
  });

  it('does not inject maintenance key by default', () => {
    const interceptor = interceptorRef.current;

    const config = interceptor({
      url: '/maintenance/orphans',
      headers: {},
    });

    expect(config.headers?.Authorization).toBeUndefined();
    expect(config.headers?.['X-MCP-API-Key']).toBeUndefined();
  });

  it('supports runtime-only maintenance key injection without VITE env', () => {
    const interceptor = interceptorRef.current;
    window.__MEMORY_PALACE_RUNTIME__ = {
      maintenanceApiKey: 'runtime-key',
      maintenanceApiKeyMode: 'bearer',
    };

    const maintenanceConfig = interceptor({
      url: '/maintenance/orphans',
      headers: {},
    });
    const browseConfig = interceptor({
      url: '/browse/node',
      headers: {},
    });

    expect(maintenanceConfig.headers.Authorization).toBe('Bearer runtime-key');
    expect(maintenanceConfig.headers['X-MCP-API-Key']).toBeUndefined();
    expect(browseConfig.headers.Authorization).toBeUndefined();
    expect(browseConfig.headers['X-MCP-API-Key']).toBeUndefined();
  });

  it('does not inject key to cross-origin URLs even with protected path', () => {
    const interceptor = interceptorRef.current;
    window.__MEMORY_PALACE_RUNTIME__ = {
      maintenanceApiKey: 'runtime-key',
      maintenanceApiKeyMode: 'header',
    };

    const config = interceptor({
      url: 'https://evil.example/maintenance/orphans',
      headers: {},
      method: 'get',
    });

    expect(config.headers.Authorization).toBeUndefined();
    expect(config.headers['X-MCP-API-Key']).toBeUndefined();
  });

  it('injects runtime key for review and browse write requests', () => {
    const interceptor = interceptorRef.current;
    window.__MEMORY_PALACE_RUNTIME__ = {
      maintenanceApiKey: 'runtime-key',
      maintenanceApiKeyMode: 'header',
    };

    const reviewConfig = interceptor({
      url: '/review/sessions',
      headers: {},
      method: 'get',
    });
    const browseWriteConfig = interceptor({
      url: '/browse/node',
      headers: {},
      method: 'post',
    });
    const browseReadConfig = interceptor({
      url: '/browse/node',
      headers: {},
      method: 'get',
    });

    expect(reviewConfig.headers['X-MCP-API-Key']).toBe('runtime-key');
    expect(browseWriteConfig.headers['X-MCP-API-Key']).toBe('runtime-key');
    expect(browseReadConfig.headers['X-MCP-API-Key']).toBeUndefined();
  });

  it('merges getMemoryNode params safely with requestConfig', async () => {
    mockApi.get.mockResolvedValue({
      data: {
        node: { uri: 'core://agent/index' },
        children: [],
        breadcrumbs: [],
      },
    });

    await getMemoryNode(
      { path: 'agent/index', domain: 'core' },
      { params: { path: 'evil/path', domain: 'evil' }, signal: 'sig' }
    );

    expect(mockApi.get).toHaveBeenCalledWith('/browse/node', {
      signal: 'sig',
      params: { path: 'agent/index', domain: 'core' },
    });
  });
});
