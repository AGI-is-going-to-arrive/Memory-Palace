import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import AgentationLite from './AgentationLite';

describe('AgentationLite', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    window.localStorage.clear();
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ id: 'session-1234567890' }),
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('does not let the dev overlay block dialogs underneath it', async () => {
    render(
      <MemoryRouter>
        <AgentationLite />
      </MemoryRouter>
    );

    const toolbar = await screen.findByText('Agentation');
    const root = toolbar.closest('[data-agentation-lite="toolbar"]');
    const interactivePanels = root?.querySelectorAll('.pointer-events-auto') || [];

    expect(root?.className).toContain('pointer-events-none');
    expect(root?.className).toContain('z-30');
    expect(interactivePanels.length).toBeGreaterThan(0);
  });
});
