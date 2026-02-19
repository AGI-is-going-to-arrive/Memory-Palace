import React from 'react';
import clsx from 'clsx';

const OP_CONFIG = {
  create: { label: 'Create', color: 'accent' },
  create_alias: { label: 'Alias', color: 'accent' },
  delete: { label: 'Delete', color: 'quiet' },
  modify_meta: { label: 'Meta', color: 'sand' },
  modify_content: { label: 'Content', color: 'sand' },
  modify: { label: 'Update', color: 'sand' },
};

const COLOR_CLASSES = {
  accent: {
    active: 'bg-[color:var(--palace-accent)] shadow-[0_0_10px_rgba(179,133,79,0.32)]',
    idle: 'bg-[rgba(179,133,79,0.44)]',
    label: 'text-[color:var(--palace-accent-2)]',
  },
  quiet: {
    active: 'bg-[color:var(--palace-accent-2)] shadow-[0_0_10px_rgba(143,106,69,0.28)]',
    idle: 'bg-[rgba(143,106,69,0.5)]',
    label: 'text-[color:var(--palace-muted)]',
  },
  sand: {
    active: 'bg-[color:var(--palace-sand-3)] shadow-[0_0_8px_rgba(200,171,134,0.4)]',
    idle: 'bg-[rgba(200,171,134,0.55)]',
    label: 'text-[color:var(--palace-muted)]',
  },
};

const SnapshotList = ({ snapshots, selectedId, onSelect }) => {
  if (snapshots.length === 0) {
    return (
      <div className="py-10 text-center text-xs uppercase tracking-[0.18em] text-[color:var(--palace-muted)]">
        Empty Sequence
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      {snapshots.map((snap) => {
        const isSelected = snap.resource_id === selectedId;
        const opConfig = OP_CONFIG[snap.operation_type] || OP_CONFIG.modify;
        const colors = COLOR_CLASSES[opConfig.color];
        const displayName = snap.uri || snap.resource_id;

        return (
          <button
            key={snap.resource_id}
            onClick={() => onSelect(snap)}
            className={clsx(
              'group relative w-full border-l-2 px-5 py-3 text-left outline-none transition duration-200 hover:bg-[rgba(237,226,211,0.66)]',
              isSelected
                ? 'border-[color:var(--palace-accent)] bg-[rgba(255,250,244,0.96)] text-[color:var(--palace-ink)] shadow-[inset_0_0_0_1px_rgba(198,165,126,0.45)]'
                : 'border-transparent text-[color:var(--palace-muted)] hover:text-[color:var(--palace-ink)]'
            )}
          >
            {isSelected && (
              <div className="pointer-events-none absolute inset-0 bg-gradient-to-r from-[rgba(179,133,79,0.14)] to-transparent" />
            )}

            <div className="relative z-10 flex items-center gap-3">
              <div
                className={clsx(
                  'h-1.5 w-1.5 flex-shrink-0 rounded-full transition-colors',
                  isSelected ? colors.active : colors.idle
                )}
              />

              <div className="min-w-0 flex-1">
                <div
                  className={clsx(
                    'truncate text-xs font-medium transition-colors',
                    isSelected
                      ? 'text-[color:var(--palace-ink)]'
                      : 'text-[color:var(--palace-muted)] group-hover:text-[color:var(--palace-ink)]'
                  )}
                >
                  {displayName}
                </div>
                <div className="mt-0.5 flex items-center gap-2">
                  <span className={clsx('text-[10px] font-bold uppercase tracking-[0.14em]', colors.label)}>
                    {opConfig.label}
                  </span>
                  <span className="text-[10px] text-[color:var(--palace-muted)]">{snap.resource_type}</span>
                </div>
              </div>

              <div
                className={clsx(
                  'text-[10px] font-mono transition-opacity',
                  isSelected
                    ? 'opacity-100 text-[color:var(--palace-accent-2)]'
                    : 'opacity-0 text-[color:var(--palace-muted)] group-hover:opacity-100'
                )}
              >
                {new Date(snap.snapshot_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
};

export default SnapshotList;
