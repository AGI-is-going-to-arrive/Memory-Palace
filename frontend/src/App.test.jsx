import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor, within } from '@testing-library/react';

import App, { buildRoutesKey } from './App';
import i18n, { LOCALE_STORAGE_KEY } from './i18n';

const { memoryMountCounter, setupApi } = vi.hoisted(() => ({
  memoryMountCounter: { current: 0 },
  setupApi: {
    getSetupStatus: vi.fn(),
    saveSetupConfig: vi.fn(),
  },
}));

vi.mock('./lib/api', async () => {
  const actual = await vi.importActual('./lib/api');
  return {
    ...actual,
    getSetupStatus: setupApi.getSetupStatus,
    saveSetupConfig: setupApi.saveSetupConfig,
  };
});

vi.mock('./features/memory/MemoryBrowser', () => ({
  default: () => {
    React.useEffect(() => {
      memoryMountCounter.current += 1;
    }, []);
    return <div>memory-page</div>;
  },
}));

vi.mock('./features/review/ReviewPage', () => ({
  default: () => <div>review-page</div>,
}));

vi.mock('./features/maintenance/MaintenancePage', () => ({
  default: () => <div>maintenance-page</div>,
}));

vi.mock('./features/observability/ObservabilityPage', () => ({
  default: () => <div>observability-page</div>,
}));

vi.mock('./components/AgentationLite', () => ({
  default: () => null,
}));

describe('App routing', () => {
  beforeEach(async () => {
    memoryMountCounter.current = 0;
    window.localStorage?.removeItem?.('memory-palace.dashboardAuth');
    window.localStorage?.removeItem?.(LOCALE_STORAGE_KEY);
    window.localStorage?.setItem?.('memory-palace.setupAssistantDismissed', '1');
    delete window.__MEMORY_PALACE_RUNTIME__;
    await i18n.changeLanguage('en');
    window.localStorage?.removeItem?.(LOCALE_STORAGE_KEY);
    setupApi.getSetupStatus.mockReset();
    setupApi.saveSetupConfig.mockReset();
    setupApi.getSetupStatus.mockResolvedValue({
      ok: true,
      apply_supported: true,
      apply_reason: 'local_env_file',
      target_label: '.env',
      restart_required: true,
      restart_targets: ['backend', 'sse'],
      summary: {
        dashboard_auth_configured: false,
        allow_insecure_local: false,
        embedding_backend: 'hash',
        embedding_configured: true,
        reranker_enabled: false,
        reranker_configured: false,
        write_guard_enabled: false,
        write_guard_configured: false,
        intent_llm_enabled: false,
        intent_llm_configured: false,
      },
    });
  });

  afterEach(() => {
    window.history.pushState({}, '', '/');
  });

  it('redirects root path to memory', async () => {
    window.history.pushState({}, '', '/');

    render(<App />);

    expect(await screen.findByText('memory-page')).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe('/memory'));
  });

  it('redirects unknown paths to memory', async () => {
    window.history.pushState({}, '', '/unknown-route');

    render(<App />);

    expect(await screen.findByText('memory-page')).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe('/memory'));
  });

  it('stores API key through header action when runtime config is absent', async () => {
    const user = userEvent.setup();
    window.history.pushState({}, '', '/memory');

    render(<App />);

    await user.click(screen.getByRole('button', { name: i18n.t('app.auth.setApiKey') }));
    const dialog = await screen.findByRole('dialog', { name: i18n.t('setup.title') });
    await user.type(
      within(dialog).getByPlaceholderText(i18n.t('setup.dashboard.apiKeyPlaceholder')),
      'stored-key'
    );
    await user.click(screen.getByRole('button', { name: i18n.t('setup.actions.saveBrowserOnly') }));

    expect(window.localStorage.getItem('memory-palace.dashboardAuth')).toContain('stored-key');
    expect(await screen.findByRole('button', { name: i18n.t('app.auth.updateApiKey') })).toBeInTheDocument();
  });

  it('shows an error instead of crashing when browser storage rejects the API key write', async () => {
    const user = userEvent.setup();
    const originalStorage = window.localStorage;
    window.history.pushState({}, '', '/memory');

    render(<App />);

    await user.click(screen.getByRole('button', { name: i18n.t('app.auth.setApiKey') }));
    const dialog = await screen.findByRole('dialog', { name: i18n.t('setup.title') });
    await user.type(
      within(dialog).getByPlaceholderText(i18n.t('setup.dashboard.apiKeyPlaceholder')),
      'stored-key'
    );
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      writable: true,
      value: {
        getItem: originalStorage.getItem.bind(originalStorage),
        setItem: (key, value) => {
          if (key === 'memory-palace.dashboardAuth') {
            throw new Error('quota');
          }
          return originalStorage.setItem(key, value);
        },
        removeItem: originalStorage.removeItem.bind(originalStorage),
        clear: originalStorage.clear.bind(originalStorage),
      },
    });

    await user.click(screen.getByRole('button', { name: i18n.t('setup.actions.saveBrowserOnly') }));
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      writable: true,
      value: originalStorage,
    });

    expect((await screen.findAllByText(i18n.t('setup.messages.saveFailed'))).length).toBeGreaterThan(0);
    expect(screen.getByRole('dialog', { name: i18n.t('setup.title') })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: i18n.t('app.auth.setApiKey') })).toBeInTheDocument();
  });

  it('remounts routes after stored auth changes without depending on raw key text', async () => {
    const user = userEvent.setup();
    window.history.pushState({}, '', '/memory');

    render(<App />);
    expect(await screen.findByText('memory-page')).toBeInTheDocument();
    await waitFor(() => expect(memoryMountCounter.current).toBe(1));

    await user.click(screen.getByRole('button', { name: i18n.t('app.auth.setApiKey') }));
    const dialog = await screen.findByRole('dialog', { name: i18n.t('setup.title') });
    await user.type(
      within(dialog).getByPlaceholderText(i18n.t('setup.dashboard.apiKeyPlaceholder')),
      'stored-key'
    );
    await user.click(screen.getByRole('button', { name: i18n.t('setup.actions.saveBrowserOnly') }));

    await waitFor(() => expect(memoryMountCounter.current).toBe(2));
  });

  it('defaults to english when no stored locale exists', async () => {
    window.history.pushState({}, '', '/memory');

    render(<App />);

    expect(await screen.findByRole('button', { name: 'Set API key' })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe('en');
  });

  it('shows runtime status badge when runtime config is present', async () => {
    window.history.pushState({}, '', '/memory');
    window.__MEMORY_PALACE_RUNTIME__ = {
      maintenanceApiKey: 'runtime-key',
      maintenanceApiKeyMode: 'header',
    };

    render(<App />);

    expect(await screen.findByText(i18n.t('app.auth.runtimeBadge'))).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: i18n.t('app.auth.setApiKey') })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: i18n.t('app.auth.openSetup') })).toBeInTheDocument();
  });

  it('toggles language and persists the selection across remounts', async () => {
    const user = userEvent.setup();
    window.history.pushState({}, '', '/memory');

    const firstRender = render(<App />);

    expect(await screen.findByRole('button', { name: 'Set API key' })).toBeInTheDocument();
    await user.click(screen.getByTestId('language-toggle'));

    expect(await screen.findByRole('button', { name: '设置 API 密钥' })).toBeInTheDocument();
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('zh-CN');

    firstRender.unmount();

    render(<App />);

    expect(await screen.findByRole('button', { name: '设置 API 密钥' })).toBeInTheDocument();
  });

  it('auto-opens setup assistant on first load when no auth is configured', async () => {
    window.localStorage.removeItem('memory-palace.setupAssistantDismissed');
    window.history.pushState({}, '', '/memory');

    render(<App />);

    expect(await screen.findByRole('dialog', { name: i18n.t('setup.title') })).toBeInTheDocument();
  });

  it('allows switching language from inside the setup assistant on first load', async () => {
    const user = userEvent.setup();
    window.localStorage.removeItem('memory-palace.setupAssistantDismissed');
    window.history.pushState({}, '', '/memory');

    render(<App />);

    const dialog = await screen.findByRole('dialog', { name: i18n.t('setup.title') });
    expect(within(dialog).getByText('Configure Memory Palace')).toBeInTheDocument();

    await user.click(within(dialog).getByTestId('setup-language-toggle'));

    expect(await screen.findByRole('dialog', { name: '配置 Memory Palace' })).toBeInTheDocument();
    expect(within(screen.getByRole('dialog', { name: '配置 Memory Palace' })).getByText('首启配置')).toBeInTheDocument();
  });

  it('keeps typed setup values when switching language inside the setup assistant', async () => {
    const user = userEvent.setup();
    window.localStorage.removeItem('memory-palace.setupAssistantDismissed');
    window.history.pushState({}, '', '/memory');

    render(<App />);

    const dialog = await screen.findByRole('dialog', { name: i18n.t('setup.title') });
    const apiKeyInput = within(dialog).getByPlaceholderText(i18n.t('setup.dashboard.apiKeyPlaceholder'));

    await user.type(apiKeyInput, 'typed-key-123');
    await user.click(within(dialog).getByTestId('setup-language-toggle'));

    const translatedDialog = await screen.findByRole('dialog', { name: '配置 Memory Palace' });
    expect(within(translatedDialog).getByDisplayValue('typed-key-123')).toBeInTheDocument();
  });

  it('does not embed the raw api key in the routes key', () => {
    const routesKey = buildRoutesKey(
      {
        source: 'stored',
        mode: 'header',
        key: 'super-secret-key',
      },
      3
    );

    expect(routesKey).toBe('stored:header:3');
    expect(routesKey).not.toContain('super-secret-key');
    expect(buildRoutesKey(null, 4)).toBe('no-auth:4');
  });
});
