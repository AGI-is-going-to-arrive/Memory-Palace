import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';

import MemoryBrowser from './MemoryBrowser';
import i18n, { LOCALE_STORAGE_KEY } from '../../i18n';
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
  beforeEach(async () => {
    vi.clearAllMocks();
    window.localStorage?.removeItem?.(LOCALE_STORAGE_KEY);
    await i18n.changeLanguage('zh-CN');
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

    const storeButton = await screen.findByRole('button', { name: i18n.t('memory.storeMemory') });
    await user.click(storeButton);

    await screen.findByText(i18n.t('memory.feedback.createGuardSkipped'));
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

    const editButton = await screen.findByRole('button', { name: i18n.t('common.actions.edit') });
    await user.click(editButton);

    const textarea = await screen.findByDisplayValue('old content');
    await user.clear(textarea);
    await user.type(textarea, 'old content changed');
    await user.click(screen.getByRole('button', { name: i18n.t('common.actions.save') }));

    await screen.findByText(i18n.t('memory.feedback.updateGuardSkipped'));
    expect(screen.queryByText(i18n.t('memory.feedback.memoryUpdated'))).not.toBeInTheDocument();
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

  it('refreshes the default conversation when language changes before the user edits it', async () => {
    await i18n.changeLanguage('en');
    renderMemoryBrowser('/memory?domain=core');

    const composer = await screen.findByPlaceholderText('Paste LLM / agent dialogue...');
    const englishDefault = i18n.getFixedT('en')('memory.defaultConversation');
    const chineseDefault = i18n.getFixedT('zh-CN')('memory.defaultConversation');

    expect(composer).toHaveValue(englishDefault);

    await act(async () => {
      await i18n.changeLanguage('zh-CN');
    });

    await waitFor(() => {
      expect(composer).toHaveValue(chineseDefault);
    });
  });

  it('does not refetch the current node when only the language changes', async () => {
    api.getMemoryNode.mockResolvedValueOnce(makeNodePayload('path-a', 'stable content'));

    renderMemoryBrowser('/memory?domain=core&path=path-a');

    await screen.findByText('path-a');
    const initialCalls = api.getMemoryNode.mock.calls.length;
    expect(initialCalls).toBe(1);

    await act(async () => {
      await i18n.changeLanguage('en');
    });

    await waitFor(() => {
      expect(screen.getAllByText('path-a').length).toBeGreaterThan(0);
    });
    expect(api.getMemoryNode.mock.calls.length).toBe(initialCalls);
  });
});
