import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';

import MemoryBrowser from './MemoryBrowser';
import * as api from '../../lib/api';

vi.mock('../../lib/api', () => ({
  createMemoryNode: vi.fn(),
  deleteMemoryNode: vi.fn(),
  getMemoryNode: vi.fn(),
  updateMemoryNode: vi.fn(),
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

const ROOT_PAYLOAD = {
  node: null,
  children: [],
  breadcrumbs: [{ path: '', label: 'root' }],
};

const makeNodePayload = (path, content) => ({
  node: {
    path,
    domain: 'core',
    uri: `core://${path}`,
    name: path,
    content,
    priority: 0,
    disclosure: '',
    gist_text: null,
    gist_method: null,
    gist_quality: null,
    source_hash: null,
  },
  children: [],
  breadcrumbs: [
    { path: '', label: 'root' },
    { path, label: path },
  ],
});

const renderMemoryBrowser = (entry) =>
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/memory" element={<MemoryBrowser />} />
      </Routes>
    </MemoryRouter>
  );

function RaceHarness() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate('/memory?domain=core&path=path-b')}>
        Go path-b
      </button>
      <MemoryBrowser />
    </>
  );
}

describe('MemoryBrowser', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getMemoryNode.mockResolvedValue(ROOT_PAYLOAD);
    api.createMemoryNode.mockResolvedValue({ success: true, created: true, path: 'created/path', domain: 'core', uri: 'core://created/path' });
    api.updateMemoryNode.mockResolvedValue({ success: true, updated: true });
    api.deleteMemoryNode.mockResolvedValue({ success: true });
  });

  it('does not navigate and shows guard feedback when create returns created=false', async () => {
    const user = userEvent.setup();
    api.createMemoryNode.mockResolvedValue({
      success: true,
      created: false,
      message: 'Skipped: write_guard blocked create_node (action=NOOP, method=hybrid).',
    });

    renderMemoryBrowser('/memory?domain=core');

    const storeButton = await screen.findByRole('button', { name: /Store Memory/i });
    await user.click(storeButton);

    await screen.findByText(/Skipped: write_guard blocked create_node/i);
    expect(api.createMemoryNode).toHaveBeenCalledTimes(1);
    expect(api.getMemoryNode).toHaveBeenCalledTimes(1);
    expect(
      api.getMemoryNode.mock.calls.some(([params]) => params?.domain === 'undefined')
    ).toBe(false);
  });

  it('shows write_guard skip feedback when update returns updated=false', async () => {
    const user = userEvent.setup();
    api.getMemoryNode.mockResolvedValueOnce(makeNodePayload('path-a', 'old content'));
    api.updateMemoryNode.mockResolvedValue({
      success: true,
      updated: false,
      message: 'Skipped: write_guard blocked update_node (action=NOOP, method=hybrid).',
    });

    renderMemoryBrowser('/memory?domain=core&path=path-a');

    const editButton = await screen.findByRole('button', { name: /Edit/i });
    await user.click(editButton);

    const textarea = await screen.findByDisplayValue('old content');
    await user.clear(textarea);
    await user.type(textarea, 'old content changed');
    await user.click(screen.getByRole('button', { name: /Save/i }));

    await screen.findByText(/Skipped: write_guard blocked update_node/i);
    expect(screen.queryByText('Memory updated.')).not.toBeInTheDocument();
    expect(api.updateMemoryNode).toHaveBeenCalledTimes(1);
    expect(api.getMemoryNode).toHaveBeenCalledTimes(1);
  });

  it('ignores stale node responses when path switches quickly', async () => {
    const user = userEvent.setup();
    const deferredA = createDeferred();
    const deferredB = createDeferred();

    api.getMemoryNode.mockImplementation(({ path }) => {
      if (path === 'path-a') return deferredA.promise;
      if (path === 'path-b') return deferredB.promise;
      return Promise.resolve(ROOT_PAYLOAD);
    });

    render(
      <MemoryRouter initialEntries={['/memory?domain=core&path=path-a']}>
        <Routes>
          <Route path="/memory" element={<RaceHarness />} />
        </Routes>
      </MemoryRouter>
    );

    await user.click(screen.getByRole('button', { name: /Go path-b/i }));

    deferredB.resolve(makeNodePayload('path-b', 'fresh content B'));
    await screen.findByText('fresh content B');

    deferredA.resolve(makeNodePayload('path-a', 'stale content A'));
    await waitFor(() => {
      expect(screen.queryByText('stale content A')).not.toBeInTheDocument();
    });
    expect(screen.getByText('fresh content B')).toBeInTheDocument();
  });
});
