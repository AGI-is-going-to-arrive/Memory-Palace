import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../lib/api';
import MaintenancePage from './MaintenancePage';

vi.mock('../../lib/api', () => ({
  queryVitalityCleanupCandidates: vi.fn(),
  prepareVitalityCleanup: vi.fn(),
  confirmVitalityCleanup: vi.fn(),
  triggerVitalityDecay: vi.fn(),
  extractApiError: vi.fn((error, fallback = 'Request failed') => {
    const detail = error?.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (detail && typeof detail === 'object') {
      return detail.error || detail.reason || detail.message || fallback;
    }
    if (typeof error?.message === 'string' && error.message.trim()) return error.message;
    return fallback;
  }),
  listOrphanMemories: vi.fn(),
  getOrphanMemoryDetail: vi.fn(),
  deleteOrphanMemory: vi.fn(),
}));

describe('MaintenancePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    vi.spyOn(window, 'confirm').mockImplementation(() => true);
    vi.spyOn(window, 'prompt').mockReturnValue(null);

    api.listOrphanMemories.mockResolvedValue([
      {
        id: 1,
        category: 'deprecated',
        created_at: '2026-01-01T00:00:00Z',
        content_snippet: 'orphan snippet',
      },
    ]);
    api.queryVitalityCleanupCandidates.mockResolvedValue({ items: [] });
    api.getOrphanMemoryDetail.mockResolvedValue({
      id: 1,
      content: 'orphan full content',
    });
    api.deleteOrphanMemory.mockResolvedValue({ ok: true });
  });

  it('loads orphan list and detail via shared API module', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    await waitFor(() => {
      expect(api.listOrphanMemories).toHaveBeenCalledTimes(1);
    });

    await user.click(await screen.findByText(/orphan snippet/i));

    await waitFor(() => {
      expect(api.getOrphanMemoryDetail).toHaveBeenCalledWith(1);
    });
    expect(await screen.findByText(/orphan full content/i)).toBeInTheDocument();
  });

  it('uses shared API module for batch delete', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    await screen.findByText(/orphan snippet/i);
    await user.click(screen.getByTitle('Select all'));
    await user.click(screen.getByRole('button', { name: /delete 1 orphans/i }));

    await waitFor(() => {
      expect(window.confirm).toHaveBeenCalledTimes(1);
      expect(api.deleteOrphanMemory).toHaveBeenCalledWith(1);
    });
  });

  it('passes optional domain/path_prefix filters when applying vitality query', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    await waitFor(() => {
      expect(api.queryVitalityCleanupCandidates).toHaveBeenCalledTimes(1);
    });
    api.queryVitalityCleanupCandidates.mockClear();

    await user.type(screen.getByLabelText(/vitality domain/i), 'notes');
    await user.type(screen.getByLabelText(/vitality path prefix/i), 'scope/');
    await user.click(screen.getByRole('button', { name: /apply filters/i }));

    await waitFor(() => {
      expect(api.queryVitalityCleanupCandidates).toHaveBeenCalledTimes(1);
    });
    expect(api.queryVitalityCleanupCandidates).toHaveBeenCalledWith({
      threshold: 0.35,
      inactive_days: 14,
      limit: 80,
      domain: 'notes',
      path_prefix: 'scope/',
    });
  });

  it('keeps prepared review for retry when confirm returns confirmation_phrase_mismatch', async () => {
    const user = userEvent.setup();
    api.queryVitalityCleanupCandidates.mockResolvedValue({
      status: 'ok',
      items: [
        {
          memory_id: 101,
          vitality_score: 0.12,
          inactive_days: 30,
          access_count: 0,
          can_delete: true,
          uri: 'core://agent/legacy',
          content_snippet: 'legacy candidate',
          state_hash: 'hash-101',
        },
      ],
    });
    api.prepareVitalityCleanup.mockResolvedValue({
      review: {
        review_id: 'review-1',
        token: 'token-1',
        confirmation_phrase: 'CONFIRM DELETE',
        action: 'delete',
        reviewer: 'maintenance_dashboard',
      },
    });
    api.confirmVitalityCleanup.mockRejectedValue({
      response: { data: { detail: 'confirmation_phrase_mismatch' } },
    });
    api.extractApiError.mockReturnValue('confirmation_phrase_mismatch');
    window.prompt.mockReturnValue('CONFIRM DELETE');

    render(<MaintenancePage />);
    await screen.findByText(/legacy candidate/i);

    const selectAllButtons = screen.getAllByRole('button', { name: /^Select all$/i });
    await user.click(selectAllButtons[selectAllButtons.length - 1]);
    await user.click(screen.getByRole('button', { name: /Prepare Delete \(1\)/i }));
    await screen.findByText(/review_id: review-1/i);

    await user.click(screen.getByRole('button', { name: /Confirm delete/i }));

    await waitFor(() => {
      expect(api.confirmVitalityCleanup).toHaveBeenCalledWith({
        review_id: 'review-1',
        token: 'token-1',
        confirmation_phrase: 'CONFIRM DELETE',
      });
    });
    expect(screen.getByText(/review_id: review-1/i)).toBeInTheDocument();
    expect(screen.getByText('confirmation_phrase_mismatch')).toBeInTheDocument();
  });
});
