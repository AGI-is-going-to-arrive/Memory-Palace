import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';

vi.mock('./features/memory/MemoryBrowser', () => ({
  default: () => <div>memory-page</div>,
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

describe('i18n bootstrap', () => {
  beforeEach(() => {
    window.localStorage?.removeItem?.('memory-palace.dashboardAuth');
    window.localStorage?.removeItem?.('memory-palace.locale');
    delete window.__MEMORY_PALACE_RUNTIME__;
    vi.spyOn(window, 'prompt').mockReturnValue(null);
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    window.history.pushState({}, '', '/memory');
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.resetModules();
    window.history.pushState({}, '', '/');
  });

  it('restores stored locale on fresh init instead of overwriting it back to english', async () => {
    window.localStorage.setItem('memory-palace.locale', 'zh-CN');

    vi.resetModules();
    const [{ default: FreshApp }, { default: freshI18n, LOCALE_STORAGE_KEY }] = await Promise.all([
      import('./App'),
      import('./i18n'),
    ]);

    render(<FreshApp />);

    expect(await screen.findByRole('button', { name: '设置 API 密钥' })).toBeInTheDocument();
    await waitFor(() => expect(freshI18n.resolvedLanguage).toBe('zh-CN'));
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe('zh-CN');
    expect(document.documentElement.lang).toBe('zh-CN');
    expect(document.title).toBe('Memory Palace 控制台');
  });
});
