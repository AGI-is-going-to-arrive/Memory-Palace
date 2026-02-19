import React from 'react';
import { diffLines } from 'diff';

const DiffViewer = ({ oldText, newText }) => {
  const safeOld = oldText || '';
  const safeNew = newText || '';
  const diff = diffLines(safeOld, safeNew);
  const hasChanges = safeOld !== safeNew;

  return (
    <div className="w-full text-sm leading-7 text-[color:var(--palace-ink)]">
      {!hasChanges && (
        <div className="rounded-lg border border-[color:var(--palace-line)] border-dashed bg-[rgba(248,240,230,0.68)] p-4 text-center italic text-[color:var(--palace-muted)]">
          No changes detected in content.
        </div>
      )}

      <div className="space-y-1">
        {diff.map((part, index) => {
          if (part.removed) {
            return (
              <div
                key={index}
                className="group relative border-l-2 border-[color:var(--palace-sand-3)] bg-[rgba(236,227,214,0.72)] py-1 pl-4 pr-2 transition-colors hover:bg-[rgba(231,219,200,0.78)]"
              >
                <span className="mb-1 block select-none font-mono text-xs text-[color:var(--palace-muted)]">REMOVED</span>
                <span className="whitespace-pre-wrap font-serif text-[color:var(--palace-muted)] line-through decoration-[rgba(143,106,69,0.55)]">
                  {part.value}
                </span>
              </div>
            );
          }

          if (part.added) {
            return (
              <div
                key={index}
                className="group relative my-1 rounded-r border-l-2 border-[color:var(--palace-accent)] bg-[rgba(245,238,228,0.88)] py-2 pl-4 pr-2 transition-colors hover:bg-[rgba(240,230,216,0.95)]"
              >
                <span className="mb-1 block select-none font-mono text-xs text-[color:var(--palace-accent-2)]">ADDED</span>
                <span className="whitespace-pre-wrap font-serif font-medium text-[color:var(--palace-ink)]">
                  {part.value}
                </span>
              </div>
            );
          }

          return (
            <div
              key={index}
              className="whitespace-pre-wrap border-l-2 border-transparent py-1 pl-4 pr-2 text-[color:var(--palace-muted)] transition-colors hover:text-[color:var(--palace-ink)]"
            >
              {part.value}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export const SimpleDiff = DiffViewer;
export default DiffViewer;
