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
  listOrphanMemories: vi.fn(),
  getOrphanMemoryDetail: vi.fn(),
  deleteOrphanMemory: vi.fn(),
}));

describe('MaintenancePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    vi.spyOn(window, 'confirm').mockImplementation(() => true);

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
});
