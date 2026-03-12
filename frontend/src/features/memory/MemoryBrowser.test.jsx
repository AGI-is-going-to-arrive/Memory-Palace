import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';

import MemoryBrowser from './MemoryBrowser';
import i18n, { LOCALE_STORAGE_KEY } from '../../i18n';
import * as api from '../../lib/api';

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    createMemoryNode: vi.fn(),
    deleteMemoryNode: vi.fn(),
    getMemoryNode: vi.fn(),
    updateMemoryNode: vi.fn(),
  };
});

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

const makeChild = (path, contentSnippet = '') => ({
  domain: 'core',
  path,
  name: path,
  priority: 0,
  gist_text: null,
  content_snippet: contentSnippet,
});

const makeNodePayload = (path, content, children = []) => ({
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
  children,
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
    vi.spyOn(window, 'confirm').mockImplementation(() => true);
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

  it('recomputes load error copy when the language changes', async () => {
    api.getMemoryNode.mockRejectedValue({
      response: {
        data: {
          detail: {
            error: 'maintenance_auth_failed',
            reason: 'invalid_or_missing_api_key',
          },
        },
      },
    });
    await i18n.changeLanguage('en');

    renderMemoryBrowser('/memory?domain=core');

    await screen.findByText(/Click "Set API key"/);

    await act(async () => {
      await i18n.changeLanguage('zh-CN');
    });

    await screen.findByText(/点击右上角“设置 API 密钥”/);
    expect(screen.queryByText(/Click "Set API key"/)).not.toBeInTheDocument();
  });

  it('keeps the current node when navigation is cancelled with unsaved edits', async () => {
    const user = userEvent.setup();
    window.confirm.mockReturnValueOnce(false);
    api.getMemoryNode.mockResolvedValue(
      makeNodePayload('path-a', 'draft content', [makeChild('path-b', 'child node')])
    );

    renderMemoryBrowser('/memory?domain=core&path=path-a');

    await user.click(await screen.findByRole('button', { name: i18n.t('common.actions.edit') }));
    const textarea = await screen.findByDisplayValue('draft content');
    await user.type(textarea, ' updated');
    await user.click(screen.getByRole('button', { name: /path-b/i }));

    expect(window.confirm).toHaveBeenCalledWith(i18n.t('memory.prompts.discardNodeChanges'));
    expect(api.getMemoryNode).toHaveBeenCalledTimes(1);
    expect(screen.getAllByText('path-a').length).toBeGreaterThan(0);
  });

  it('shows child memories in batches and loads more on demand', async () => {
    const user = userEvent.setup();
    api.getMemoryNode.mockResolvedValue({
      ...ROOT_PAYLOAD,
      children: Array.from({ length: 55 }, (_, index) => makeChild(`memory-${index + 1}`)),
    });

    renderMemoryBrowser('/memory?domain=core');

    await screen.findByText('memory-1');
    expect(screen.queryByText('memory-55')).not.toBeInTheDocument();
    expect(
      screen.getByText(i18n.t('memory.showingChildren', { shown: 50, total: 55 }))
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole('button', {
        name: i18n.t('memory.loadMoreChildren', { count: 5 }),
      })
    );

    expect(await screen.findByText('memory-55')).toBeInTheDocument();
  });

  it('offers visible domain switching and a quick jump to project notes', async () => {
    const user = userEvent.setup();
    api.getMemoryNode.mockResolvedValue(ROOT_PAYLOAD);

    renderMemoryBrowser('/memory?domain=core');

    await screen.findByText(i18n.t('memory.rootNodeTitle'));

    await user.click(screen.getByRole('button', { name: i18n.t('memory.domainLabels.notes') }));
    await waitFor(() => {
      expect(api.getMemoryNode).toHaveBeenLastCalledWith(
        { domain: 'notes', path: '' },
        expect.any(Object)
      );
    });

    await user.click(screen.getByRole('button', { name: i18n.t('memory.projectNotesRoot') }));
    await waitFor(() => {
      expect(api.getMemoryNode).toHaveBeenLastCalledWith(
        { domain: 'notes', path: 'projects' },
        expect.any(Object)
      );
    });
  });

  it('defaults bare /memory to the project notes scope', async () => {
    api.getMemoryNode.mockResolvedValue(ROOT_PAYLOAD);

    renderMemoryBrowser('/memory');

    await waitFor(() => {
      expect(api.getMemoryNode).toHaveBeenLastCalledWith(
        { domain: 'notes', path: 'projects' },
        expect.any(Object)
      );
    });
  });
});
