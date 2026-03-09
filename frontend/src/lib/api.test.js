import { beforeEach, describe, expect, it } from 'vitest';
import i18n from '../i18n';
import { extractApiError } from './api';

describe('extractApiError', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('returns plain string detail directly', () => {
    const error = {
      response: {
        data: {
          detail: 'Not Found',
        },
      },
    };
    expect(extractApiError(error)).toBe('Not Found');
  });

  it('returns structured detail with error, reason, and operation', () => {
    const error = {
      response: {
        data: {
          detail: {
            error: 'index_job_enqueue_failed',
            reason: 'queue_full',
            operation: 'retry_rebuild_index',
          },
        },
      },
    };

    expect(extractApiError(error)).toBe(
      'index_job_enqueue_failed | queue_full | operation=retry_rebuild_index',
    );
  });

  it('deduplicates repeated structured fields', () => {
    const error = {
      response: {
        data: {
          detail: {
            error: 'queue_full',
            reason: 'queue_full',
            message: 'queue_full',
          },
        },
      },
    };
    expect(extractApiError(error)).toBe('queue_full');
  });

  it('adds an actionable hint for auth failures', () => {
    const error = {
      response: {
        status: 401,
        data: {
          detail: {
            error: 'maintenance_auth_failed',
            reason: 'invalid_or_missing_api_key',
          },
        },
      },
    };
    expect(extractApiError(error)).toBe(
      'maintenance_auth_failed | invalid_or_missing_api_key | Click "Set API key" in the top-right corner, or configure MCP_API_KEY / MCP_API_KEY_ALLOW_INSECURE_LOCAL first.',
    );
  });

  it('returns fallback message when no structured detail exists', () => {
    const error = { message: '' };
    expect(extractApiError(error, 'fallback-message')).toBe('fallback-message');
  });

  it('localizes generic network errors in zh-CN', async () => {
    await i18n.changeLanguage('zh-CN');
    expect(extractApiError({ message: 'Network Error' })).toBe('网络异常');
  });

  it('localizes generic status-code errors in zh-CN', async () => {
    await i18n.changeLanguage('zh-CN');
    expect(extractApiError({ message: 'Request failed with status code 500' })).toBe('请求失败（状态码 500）');
  });
});
