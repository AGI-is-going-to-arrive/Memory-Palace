import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '@testing-library/react';

import App from './App';

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

describe('App routing', () => {
  beforeEach(() => {
    window.localStorage?.removeItem?.('memory-palace.dashboardAuth');
    delete window.__MEMORY_PALACE_RUNTIME__;
    vi.spyOn(window, 'prompt').mockReturnValue(null);
    vi.spyOn(window, 'alert').mockImplementation(() => {});
  });

  afterEach(() => {
    window.history.pushState({}, '', '/');
    vi.restoreAllMocks();
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
    window.prompt.mockReturnValue('stored-key');

    render(<App />);

    await user.click(screen.getByRole('button', { name: /set api key/i }));

    expect(window.localStorage.getItem('memory-palace.dashboardAuth')).toContain('stored-key');
    expect(await screen.findByRole('button', { name: /update api key/i })).toBeInTheDocument();
  });

  it('shows runtime status badge when runtime config is present', async () => {
    window.history.pushState({}, '', '/memory');
    window.__MEMORY_PALACE_RUNTIME__ = {
      maintenanceApiKey: 'runtime-key',
      maintenanceApiKeyMode: 'header',
    };

    render(<App />);

    expect(await screen.findByText(/runtime key active/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /set api key/i })).not.toBeInTheDocument();
  });
});
