import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SnapshotList from './SnapshotList';
import i18n, { LOCALE_STORAGE_KEY } from '../i18n';

describe('SnapshotList', () => {
  beforeEach(async () => {
    window.localStorage?.removeItem?.(LOCALE_STORAGE_KEY);
    await i18n.changeLanguage('zh-CN');
  });

  it('formats snapshot time with the app locale instead of the browser default locale', () => {
    const realDateTimeFormat = Intl.DateTimeFormat;
    const localeCalls = [];
    vi.spyOn(Intl, 'DateTimeFormat').mockImplementation((locale, options) => {
      localeCalls.push(locale);
      return new realDateTimeFormat(locale, options);
    });

    render(
      <SnapshotList
        snapshots={[
          {
            resource_id: 'snapshot-1',
            resource_type: 'memory',
            operation_type: 'create',
            snapshot_time: '2026-03-09T09:08:00Z',
          },
        ]}
        selectedId="snapshot-1"
        onSelect={() => {}}
      />
    );

    expect(screen.getByText('记忆')).toBeInTheDocument();
    expect(localeCalls).toContain('zh-CN');
  });
});
