import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../lib/api';
import i18n, { LOCALE_STORAGE_KEY } from '../../i18n';
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
  extractApiErrorCode: vi.fn((error) => {
    const detail = error?.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail.trim();
    if (detail && typeof detail === 'object') {
      return detail.code || detail.error || detail.reason || null;
    }
    return null;
  }),
  listOrphanMemories: vi.fn(),
  getOrphanMemoryDetail: vi.fn(),
  deleteOrphanMemory: vi.fn(),
}));

describe('MaintenancePage', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    window.localStorage?.removeItem?.(LOCALE_STORAGE_KEY);
    await i18n.changeLanguage('zh-CN');
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
    await user.click(screen.getByTitle(i18n.t('maintenance.selectAll')));
    await user.click(screen.getByRole('button', { name: i18n.t('maintenance.deleteOrphans', { count: 1 }) }));

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

    await user.type(screen.getByLabelText(i18n.t('maintenance.vitality.domain')), 'notes');
    await user.type(screen.getByLabelText(i18n.t('maintenance.vitality.pathPrefix')), 'scope/');
    await user.click(screen.getByRole('button', { name: i18n.t('maintenance.vitality.applyFilters') }));

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

  it('shows translated error when vitality prepare selection exceeds limit', async () => {
    const user = userEvent.setup();
    api.queryVitalityCleanupCandidates.mockResolvedValue({
      status: 'ok',
      items: Array.from({ length: 101 }, (_, index) => ({
        memory_id: index + 1,
        vitality_score: 0.12,
        inactive_days: 30,
        access_count: 0,
        can_delete: true,
        uri: `core://agent/${index + 1}`,
        content_snippet: `candidate-${index + 1}`,
        state_hash: `hash-${index + 1}`,
      })),
    });

    render(<MaintenancePage />);
    await screen.findByText('candidate-1');

    expect(screen.getByLabelText(i18n.t('maintenance.vitality.domain'))).toBeInTheDocument();
    expect(screen.getByLabelText(i18n.t('maintenance.vitality.pathPrefix'))).toBeInTheDocument();

    const selectAllButtons = screen.getAllByRole('button', { name: i18n.t('maintenance.selectAll') });
    await user.click(selectAllButtons[selectAllButtons.length - 1]);
    await user.click(screen.getByRole('button', { name: i18n.t('maintenance.vitality.prepareDelete', { count: 101 }) }));

    expect(
      await screen.findByText('选择数量过多：101。最多只能选择 100 条。')
    ).toBeInTheDocument();
    expect(api.prepareVitalityCleanup).not.toHaveBeenCalled();
  });

  it('handles invalid created_at and migration_target paths without crashing', async () => {
    const user = userEvent.setup();
    api.listOrphanMemories.mockResolvedValue([
      {
        id: 1,
        category: 'deprecated',
        created_at: 'invalid-time',
        content_snippet: 'legacy orphan',
        migration_target: {
          id: 2,
          paths: { bad: true },
        },
      },
    ]);
    api.getOrphanMemoryDetail.mockResolvedValue({
      id: 1,
      content: 'legacy full content',
      migration_target: {
        id: 2,
        content: 'migrated content',
        paths: 'not-an-array',
      },
    });

    render(<MaintenancePage />);

    expect(await screen.findByText(i18n.t('common.states.unknown'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('maintenance.card.targetNoPaths', { id: 2 }))).toBeInTheDocument();

    await user.click(screen.getByText(/legacy orphan/i));
    await waitFor(() => {
      expect(api.getOrphanMemoryDetail).toHaveBeenCalledWith(1);
    });

    const detailContentNodes = await screen.findAllByText(/legacy full content/i);
    expect(detailContentNodes.length).toBeGreaterThan(0);
    expect(screen.getByText(i18n.t('maintenance.card.diffTitle', { from: 1, to: 2 }))).toBeInTheDocument();
  });

  it('keeps prepared review for retry when confirm returns structured confirmation_phrase_mismatch detail', async () => {
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
      response: {
        data: {
          detail: {
            error: 'confirmation_phrase_mismatch',
            message: 'confirmation phrase mismatch',
          },
        },
      },
    });
    api.extractApiError.mockReturnValue('confirmation phrase mismatch');
    api.extractApiErrorCode.mockReturnValue('confirmation_phrase_mismatch');
    window.prompt.mockReturnValue('CONFIRM DELETE');

    render(<MaintenancePage />);
    await screen.findByText(/legacy candidate/i);

    const selectAllButtons = screen.getAllByRole('button', { name: i18n.t('maintenance.selectAll') });
    await user.click(selectAllButtons[selectAllButtons.length - 1]);
    await user.click(screen.getByRole('button', { name: i18n.t('maintenance.vitality.prepareDelete', { count: 1 }) }));
    await screen.findByText(i18n.t('maintenance.vitality.reviewId', { value: 'review-1' }));

    await user.click(screen.getByRole('button', { name: i18n.t('maintenance.vitality.confirmAction', { action: 'delete' }) }));

    await waitFor(() => {
      expect(api.confirmVitalityCleanup).toHaveBeenCalledWith({
        review_id: 'review-1',
        token: 'token-1',
        confirmation_phrase: 'CONFIRM DELETE',
      });
    });
    expect(screen.getByText(i18n.t('maintenance.vitality.reviewId', { value: 'review-1' }))).toBeInTheDocument();
    expect(screen.getByText('confirmation phrase mismatch')).toBeInTheDocument();
    expect(api.queryVitalityCleanupCandidates).toHaveBeenCalledTimes(1);
  });
});
