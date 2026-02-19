import { describe, expect, it } from 'vitest';
import { extractApiError } from './api';

describe('extractApiError', () => {
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

  it('returns fallback message when no structured detail exists', () => {
    const error = { message: '' };
    expect(extractApiError(error, 'fallback-message')).toBe('fallback-message');
  });
});
