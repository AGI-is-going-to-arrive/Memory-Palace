import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../lib/api';
import ReviewPage from './ReviewPage';

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    getSessions: vi.fn(),
    getSnapshots: vi.fn(),
    getDiff: vi.fn(),
    rollbackResource: vi.fn(),
    approveSnapshot: vi.fn(),
    clearSession: vi.fn(),
    extractApiError: vi.fn(actual.extractApiError),
  };
});

vi.mock('../../components/SnapshotList', () => ({
  default: ({ snapshots = [], onSelect }) => (
    <div>
      {snapshots.map((snapshot) => (
        <button
          key={snapshot.resource_id}
          type="button"
          onClick={() => onSelect(snapshot)}
        >
          {snapshot.resource_id}
        </button>
      ))}
    </div>
  ),
}));

vi.mock('../../components/DiffViewer', () => ({
  SimpleDiff: () => <div>diff</div>,
}));

const createDeferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

const DEFAULT_SESSION = { session_id: 'session-a' };
const DEFAULT_SNAPSHOT = {
  resource_id: 'res-1',
  uri: 'core://agent/res-1',
  resource_type: 'memory',
  operation_type: 'modify',
  snapshot_time: '2026-01-01T00:00:00Z',
};
const DEFAULT_DIFF = {
  has_changes: false,
  snapshot_data: { content: 'old-content' },
  current_data: { content: 'new-content' },
};

describe('ReviewPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.spyOn(window, 'alert').mockImplementation(() => {});

    api.getSessions.mockResolvedValue([DEFAULT_SESSION]);
    api.getSnapshots.mockResolvedValue([DEFAULT_SNAPSHOT]);
    api.getDiff.mockResolvedValue(DEFAULT_DIFF);
    api.rollbackResource.mockResolvedValue({ success: true });
    api.approveSnapshot.mockResolvedValue({});
    api.clearSession.mockResolvedValue({});
  });

  it('prevents duplicate integrate submissions on double click', async () => {
    const user = userEvent.setup();
    const approveDeferred = createDeferred();
    api.approveSnapshot.mockImplementation(() => approveDeferred.promise);

    render(<ReviewPage />);

    const integrateButton = await screen.findByRole('button', { name: /^Integrate$/i });
    const rejectButton = screen.getByRole('button', { name: /^Reject$/i });
    const integrateAllButton = screen.getByRole('button', { name: /^Integrate All$/i });

    await user.dblClick(integrateButton);

    expect(api.approveSnapshot).toHaveBeenCalledTimes(1);
    expect(integrateButton).toBeDisabled();
    expect(rejectButton).toBeDisabled();
    expect(integrateAllButton).toBeDisabled();

    approveDeferred.resolve({});
    await waitFor(() => expect(integrateButton).not.toBeDisabled());
  });

  it('prevents duplicate reject submissions on double click', async () => {
    const user = userEvent.setup();
    const rollbackDeferred = createDeferred();
    api.rollbackResource.mockImplementation(() => rollbackDeferred.promise);

    render(<ReviewPage />);

    const rejectButton = await screen.findByRole('button', { name: /^Reject$/i });
    await user.dblClick(rejectButton);

    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(api.rollbackResource).toHaveBeenCalledTimes(1);

    rollbackDeferred.resolve({ success: true });
    await waitFor(() => expect(rejectButton).not.toBeDisabled());
  });

  it('does not approve snapshot when rollback returns success=false', async () => {
    const user = userEvent.setup();
    api.rollbackResource.mockResolvedValue({
      success: false,
      message: 'Rollback failed in backend',
    });

    render(<ReviewPage />);

    const rejectButton = await screen.findByRole('button', { name: /^Reject$/i });
    await user.click(rejectButton);

    await waitFor(() => {
      expect(api.approveSnapshot).not.toHaveBeenCalled();
    });
    expect(window.alert).toHaveBeenCalledWith(
      'Rejection failed: Rollback failed in backend'
    );
  });

  it('does not approve snapshot when rollback request throws', async () => {
    const user = userEvent.setup();
    api.rollbackResource.mockRejectedValue(new Error('network down'));

    render(<ReviewPage />);

    const rejectButton = await screen.findByRole('button', { name: /^Reject$/i });
    await user.click(rejectButton);

    await waitFor(() => {
      expect(api.approveSnapshot).not.toHaveBeenCalled();
    });
    expect(window.alert).toHaveBeenCalledWith('Rejection failed: network down');
  });

  it('surfaces partial success when rollback succeeds but snapshot cleanup fails', async () => {
    const user = userEvent.setup();
    api.approveSnapshot.mockRejectedValue(new Error('cleanup failed'));

    render(<ReviewPage />);

    const rejectButton = await screen.findByRole('button', { name: /^Reject$/i });
    await user.click(rejectButton);

    await waitFor(() => {
      expect(api.rollbackResource).toHaveBeenCalledTimes(1);
      expect(api.approveSnapshot).toHaveBeenCalledTimes(1);
    });
    expect(window.alert).toHaveBeenCalledWith(
      'Rollback succeeded but snapshot cleanup failed: cleanup failed'
    );
  });

  it('ignores stale snapshot responses when switching sessions quickly', async () => {
    const user = userEvent.setup();
    const sessionA = { session_id: 'session-a' };
    const sessionB = { session_id: 'session-b' };
    const snapshotA = { ...DEFAULT_SNAPSHOT, resource_id: 'res-a' };
    const snapshotB = { ...DEFAULT_SNAPSHOT, resource_id: 'res-b' };
    const deferredA = createDeferred();
    const deferredB = createDeferred();

    api.getSessions.mockResolvedValue([sessionA, sessionB]);
    api.getSnapshots.mockImplementation((sessionId) => {
      if (sessionId === 'session-a') return deferredA.promise;
      if (sessionId === 'session-b') return deferredB.promise;
      return Promise.resolve([]);
    });

    render(<ReviewPage />);

    const sessionSelect = await screen.findByRole('combobox', { name: /target session/i });
    await user.selectOptions(sessionSelect, 'session-b');

    deferredB.resolve([snapshotB]);
    await screen.findByRole('button', { name: 'res-b' });

    deferredA.resolve([snapshotA]);
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'res-a' })).not.toBeInTheDocument();
    });
  });

  it('clears stale snapshot selection when next session snapshots request fails', async () => {
    const user = userEvent.setup();
    const sessionA = { session_id: 'session-a' };
    const sessionB = { session_id: 'session-b' };
    const snapshotA = { ...DEFAULT_SNAPSHOT, resource_id: 'res-a' };

    api.getSessions.mockResolvedValue([sessionA, sessionB]);
    api.getSnapshots.mockImplementation((sessionId) => {
      if (sessionId === 'session-a') {
        return Promise.resolve([snapshotA]);
      }
      if (sessionId === 'session-b') {
        return Promise.reject({
          response: { status: 500, data: { detail: { error: 'backend_failed' } } },
        });
      }
      return Promise.resolve([]);
    });

    render(<ReviewPage />);
    await screen.findByRole('button', { name: 'res-a' });

    const sessionSelect = await screen.findByRole('combobox', { name: /target session/i });
    await user.selectOptions(sessionSelect, 'session-b');

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'res-a' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^Integrate$/i })).not.toBeInTheDocument();
    });
  });

  it('clears stale diff error when switching to a session with no snapshots (404)', async () => {
    const user = userEvent.setup();
    const sessionA = { session_id: 'session-a' };
    const sessionB = { session_id: 'session-b' };
    const snapshotA = { ...DEFAULT_SNAPSHOT, resource_id: 'res-a' };

    api.getSessions.mockResolvedValue([sessionA, sessionB]);
    api.getSnapshots.mockImplementation((sessionId) => {
      if (sessionId === 'session-a') {
        return Promise.resolve([snapshotA]);
      }
      if (sessionId === 'session-b') {
        return Promise.reject({ response: { status: 404, data: { detail: 'no snapshots' } } });
      }
      return Promise.resolve([]);
    });
    api.getDiff.mockRejectedValue({
      response: { data: { detail: { error: 'backend_failed' } } },
    });

    render(<ReviewPage />);
    await screen.findByText('Memory Retrieval Failed');

    const sessionSelect = await screen.findByRole('combobox', { name: /target session/i });
    await user.selectOptions(sessionSelect, 'session-b');

    await waitFor(() => {
      expect(screen.queryByText('Connection Lost')).not.toBeInTheDocument();
      expect(screen.getByText('Awaiting Input')).toBeInTheDocument();
    });
  });

  it('tolerates non-array sessions payload without crashing', async () => {
    api.getSessions.mockResolvedValue({ sessions: [] });

    render(<ReviewPage />);

    await waitFor(() => {
      expect(api.getSessions).toHaveBeenCalledTimes(1);
      expect(screen.getByText('Awaiting Input')).toBeInTheDocument();
    });
  });

  it('handles invalid session_id, surviving_paths, and snapshot_time without crashing', async () => {
    const deleteSnapshot = {
      ...DEFAULT_SNAPSHOT,
      operation_type: 'delete',
      snapshot_time: 'not-a-valid-time',
    };
    api.getSessions.mockResolvedValue([{ session_id: null }]);
    api.getSnapshots.mockResolvedValue([deleteSnapshot]);
    api.getDiff.mockResolvedValue({
      has_changes: true,
      snapshot_data: { content: 'old-content' },
      current_data: {
        content: 'new-content',
        surviving_paths: { invalid: true },
      },
    });

    render(<ReviewPage />);

    await waitFor(() => {
      expect(api.getSnapshots).toHaveBeenCalledWith('session-1');
    });
    expect(await screen.findByText('Memory Fully Orphaned')).toBeInTheDocument();
    expect(screen.getByText('Unknown')).toBeInTheDocument();
  });

  it('renders object detail from loadDiff without crashing', async () => {
    api.getDiff.mockRejectedValue({
      response: { data: { detail: { error: 'backend_failed' } } },
    });

    render(<ReviewPage />);

    await screen.findByText('Memory Retrieval Failed');
    expect(screen.getByText('backend_failed')).toBeInTheDocument();
    expect(api.extractApiError).toHaveBeenCalledWith(
      expect.anything(),
      'Failed to retrieve memory fragment.'
    );
  });

  it('renders serialized unknown object detail for diff error', async () => {
    api.getDiff.mockRejectedValue({
      response: { data: { detail: { foo: 'bar' } } },
    });

    render(<ReviewPage />);

    await screen.findByText('Memory Retrieval Failed');
    expect(screen.getByText('{"foo":"bar"}')).toBeInTheDocument();
  });

  it('shows extracted /review/sessions 401 error detail in session-failure branch', async () => {
    api.getSessions.mockRejectedValue({
      response: {
        status: 401,
        data: {
          detail: {
            error: 'unauthorized',
            reason: 'missing_api_key',
            operation: 'list_review_sessions',
          },
        },
      },
    });

    render(<ReviewPage />);

    await screen.findByText('Connection Lost');
    expect(
      screen.getByText('unauthorized | missing_api_key | operation=list_review_sessions')
    ).toBeInTheDocument();
    expect(api.extractApiError).toHaveBeenCalledWith(
      expect.anything(),
      'Failed to load review sessions.'
    );
  });
});
