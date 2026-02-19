import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  Trash2, Feather, AlertTriangle, RefreshCw,
  ChevronDown, ChevronUp, ArrowRight, Unlink, Archive, CheckSquare, Square, Minus
} from 'lucide-react';
import { format } from 'date-fns';
import DiffViewer from '../../components/DiffViewer';
import {
  queryVitalityCleanupCandidates,
  prepareVitalityCleanup,
  confirmVitalityCleanup,
  triggerVitalityDecay,
  extractApiError,
  listOrphanMemories,
  getOrphanMemoryDetail,
  deleteOrphanMemory,
} from '../../lib/api';

const VITALITY_PREPARE_MAX_SELECTIONS = 100;

export default function MaintenancePage() {
  const [orphans, setOrphans] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [expandedId, setExpandedId] = useState(null);
  const [detailData, setDetailData] = useState({});
  const [detailLoading, setDetailLoading] = useState(null);

  const [selectedIds, setSelectedIds] = useState(new Set());
  const [batchDeleting, setBatchDeleting] = useState(false);

  const [vitalityCandidates, setVitalityCandidates] = useState([]);
  const [vitalityLoading, setVitalityLoading] = useState(false);
  const [vitalityError, setVitalityError] = useState(null);
  const [vitalitySelectedIds, setVitalitySelectedIds] = useState(new Set());
  const [vitalityThreshold, setVitalityThreshold] = useState(0.35);
  const [vitalityInactiveDays, setVitalityInactiveDays] = useState(14);
  const [vitalityLimit, setVitalityLimit] = useState(80);
  const [vitalityReviewer, setVitalityReviewer] = useState('maintenance_dashboard');
  const [vitalityProcessing, setVitalityProcessing] = useState(false);
  const [vitalityPreparedReview, setVitalityPreparedReview] = useState(null);
  const [vitalityLastResult, setVitalityLastResult] = useState(null);
  const [vitalityQueryMeta, setVitalityQueryMeta] = useState(null);
  const vitalityRequestSeqRef = useRef(0);
  const vitalityPrepareSeqRef = useRef(0);

  const invalidatePreparedReview = useCallback(() => {
    vitalityPrepareSeqRef.current += 1;
    setVitalityPreparedReview(null);
  }, []);

  useEffect(() => {
    loadOrphans();
    loadVitalityCandidates();
  }, []);

  const loadOrphans = async () => {
    setLoading(true);
    setError(null);
    setSelectedIds(new Set());
    try {
      const data = await listOrphanMemories();
      setOrphans(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(`Failed to load orphans: ${extractApiError(err, 'Failed to load orphans')}`);
    } finally {
      setLoading(false);
    }
  };

  const loadVitalityCandidates = async ({ forceDecay = false } = {}) => {
    const requestSeq = vitalityRequestSeqRef.current + 1;
    vitalityRequestSeqRef.current = requestSeq;
    setVitalityLoading(true);
    setVitalityError(null);
    invalidatePreparedReview();
    try {
      const thresholdRaw = String(vitalityThreshold ?? '').trim();
      const inactiveDaysRaw = String(vitalityInactiveDays ?? '').trim();
      const limitRaw = String(vitalityLimit ?? '').trim();
      if (!thresholdRaw) {
        throw new Error('threshold is required');
      }
      if (!inactiveDaysRaw) {
        throw new Error('inactive_days is required');
      }
      if (!limitRaw) {
        throw new Error('limit is required');
      }
      const parsedThreshold = Number(thresholdRaw);
      const parsedInactiveDays = Number(inactiveDaysRaw);
      const parsedLimit = Number(limitRaw);
      if (!Number.isFinite(parsedThreshold) || parsedThreshold < 0) {
        throw new Error('threshold must be a non-negative number');
      }
      if (!Number.isFinite(parsedInactiveDays) || parsedInactiveDays < 0) {
        throw new Error('inactive_days must be a non-negative number');
      }
      if (
        !Number.isFinite(parsedLimit)
        || !Number.isInteger(parsedLimit)
        || parsedLimit < 1
        || parsedLimit > 500
      ) {
        throw new Error('limit must be in range [1, 500]');
      }
      if (forceDecay) {
        await triggerVitalityDecay({ force: true, reason: 'maintenance.manual_refresh' });
      }
      const res = await queryVitalityCleanupCandidates({
        threshold: parsedThreshold,
        inactive_days: parsedInactiveDays,
        limit: parsedLimit,
      });
      if (requestSeq !== vitalityRequestSeqRef.current) return;
      setVitalityCandidates(Array.isArray(res.items) ? res.items : []);
      setVitalityQueryMeta({
        status: res?.status || 'ok',
        decay: res?.decay || null,
      });
      setVitalitySelectedIds(new Set());
    } catch (err) {
      if (requestSeq !== vitalityRequestSeqRef.current) return;
      setVitalityQueryMeta(null);
      setVitalityError(extractApiError(err, 'Failed to load vitality candidates'));
    } finally {
      if (requestSeq !== vitalityRequestSeqRef.current) return;
      setVitalityLoading(false);
    }
  };

  const toggleSelect = useCallback((id, e) => {
    e.stopPropagation();
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback((items) => {
    const ids = items.map(i => i.id);
    setSelectedIds(prev => {
      const next = new Set(prev);
      const allSelected = ids.every(id => next.has(id));
      if (allSelected) {
        ids.forEach(id => next.delete(id));
      } else {
        ids.forEach(id => next.add(id));
      }
      return next;
    });
  }, []);

  const toggleVitalitySelect = useCallback((memoryId) => {
    if (vitalityProcessing) return;
    setVitalitySelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(memoryId)) next.delete(memoryId);
      else next.add(memoryId);
      return next;
    });
    invalidatePreparedReview();
  }, [invalidatePreparedReview, vitalityProcessing]);

  const toggleVitalitySelectAll = useCallback(() => {
    if (vitalityProcessing) return;
    const ids = vitalityCandidates.map(item => item.memory_id);
    setVitalitySelectedIds(prev => {
      const next = new Set(prev);
      const allSelected = ids.length > 0 && ids.every(id => next.has(id));
      if (allSelected) {
        ids.forEach(id => next.delete(id));
      } else {
        ids.forEach(id => next.add(id));
      }
      return next;
    });
    invalidatePreparedReview();
  }, [invalidatePreparedReview, vitalityCandidates, vitalityProcessing]);

  const handleBatchDelete = async () => {
    const count = selectedIds.size;
    if (count === 0) return;
    if (!confirm(`Permanently delete ${count} memories? This cannot be undone.`)) return;

    setBatchDeleting(true);
    const toDelete = [...selectedIds];
    const failed = [];

    for (const id of toDelete) {
      try {
        await deleteOrphanMemory(id);
      } catch {
        failed.push(id);
      }
    }

    const failedSet = new Set(failed);
    setOrphans(prev => prev.filter(item => !toDelete.includes(item.id) || failedSet.has(item.id)));
    setSelectedIds(new Set(failed));

    if (expandedId && toDelete.includes(expandedId) && !failedSet.has(expandedId)) {
      setExpandedId(null);
    }

    if (failed.length > 0) {
      alert(`${failed.length} of ${count} deletions failed. Failed IDs: ${failed.join(', ')}`);
    }

    setBatchDeleting(false);
  };

  const prepareVitalityReview = async (action) => {
    const selectedRows = vitalityCandidates.filter(item => vitalitySelectedIds.has(item.memory_id));
    if (selectedRows.length === 0) return;
    const normalizedAction = action === 'keep' ? 'keep' : 'delete';
    const reviewRows = normalizedAction === 'delete'
      ? selectedRows.filter(item => item.can_delete)
      : selectedRows;
    if (reviewRows.length === 0) {
      invalidatePreparedReview();
      setVitalityError(
        normalizedAction === 'delete'
          ? 'No deletable candidate selected. Please select rows marked as deletable.'
          : 'No candidate selected for keep review.'
      );
      return;
    }
    if (reviewRows.length > VITALITY_PREPARE_MAX_SELECTIONS) {
      invalidatePreparedReview();
      setVitalityError(`Too many selections: ${reviewRows.length}. Max allowed is ${VITALITY_PREPARE_MAX_SELECTIONS}.`);
      return;
    }

    const prepareSeq = vitalityPrepareSeqRef.current + 1;
    vitalityPrepareSeqRef.current = prepareSeq;
    setVitalityProcessing(true);
    setVitalityError(null);
    try {
      const payload = await prepareVitalityCleanup({
        action: normalizedAction,
        reviewer: vitalityReviewer.trim() || 'maintenance_dashboard',
        selections: reviewRows.map(item => ({
          memory_id: item.memory_id,
          state_hash: item.state_hash,
        })),
      });
      const review = payload?.review;
      if (
        !review
        || typeof review !== 'object'
        || !review.review_id
        || !review.token
        || !review.confirmation_phrase
      ) {
        throw new Error('Invalid cleanup review payload');
      }
      if (prepareSeq !== vitalityPrepareSeqRef.current) return;
      setVitalityPreparedReview({ ...review, action: review.action || normalizedAction });
      setVitalityLastResult(null);
    } catch (err) {
      if (prepareSeq !== vitalityPrepareSeqRef.current) return;
      setVitalityPreparedReview(null);
      setVitalityError(extractApiError(err, 'Failed to prepare cleanup'));
    } finally {
      if (prepareSeq !== vitalityPrepareSeqRef.current) return;
      setVitalityProcessing(false);
    }
  };

  const handlePrepareVitalityDelete = async () => {
    await prepareVitalityReview('delete');
  };

  const handlePrepareVitalityKeep = async () => {
    await prepareVitalityReview('keep');
  };

  const handleConfirmVitalityCleanup = async () => {
    if (!vitalityPreparedReview) return;
    const action = vitalityPreparedReview.action || 'delete';

    const typed = window.prompt(
      `Type confirmation phrase to execute ${action} cleanup:\n${vitalityPreparedReview.confirmation_phrase}`
    );
    if (typed === null) return;
    if (typed.trim() !== vitalityPreparedReview.confirmation_phrase) {
      setVitalityError('Confirmation phrase mismatch. Cleanup request not sent.');
      return;
    }

    setVitalityProcessing(true);
    setVitalityError(null);
    try {
      const payload = await confirmVitalityCleanup({
        review_id: vitalityPreparedReview.review_id,
        token: vitalityPreparedReview.token,
        confirmation_phrase: typed,
      });
      setVitalityLastResult(payload);
      invalidatePreparedReview();
      await Promise.all([loadOrphans(), loadVitalityCandidates()]);
    } catch (err) {
      const detailText = extractApiError(err, 'Failed to confirm cleanup');
      setVitalityError(detailText);
      invalidatePreparedReview();
      if (detailText !== 'confirmation_phrase_mismatch') {
        await loadVitalityCandidates();
      }
    } finally {
      setVitalityProcessing(false);
    }
  };

  const handleExpand = async (id) => {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);

    if (!detailData[id]) {
      setDetailLoading(id);
      try {
        const data = await getOrphanMemoryDetail(id);
        setDetailData(prev => ({ ...prev, [id]: data }));
      } catch (err) {
        setDetailData(prev => ({ ...prev, [id]: { error: extractApiError(err, 'Failed to load orphan detail') } }));
      } finally {
        setDetailLoading(null);
      }
    }
  };

  const deprecated = orphans.filter(o => o.category === 'deprecated');
  const orphaned = orphans.filter(o => o.category === 'orphaned');
  const vitalitySelectedCount = vitalityCandidates.filter(
    item => vitalitySelectedIds.has(item.memory_id)
  ).length;
  const vitalityCanDeleteCount = vitalityCandidates.filter(item => item.can_delete).length;
  const vitalitySelectedCanDelete = vitalityCandidates.filter(
    item => vitalitySelectedIds.has(item.memory_id) && item.can_delete
  ).length;

  const renderCard = (item) => {
    const isExpanded = expandedId === item.id;
    const detail = detailData[item.id];
    const isLoadingDetail = detailLoading === item.id;
    const isChecked = selectedIds.has(item.id);

    return (
      <div key={item.id} className="group relative rounded-lg border border-stone-700/40 bg-stone-900 transition-all hover:border-amber-700/45 hover:shadow-[0_0_14px_rgba(245,158,11,0.12)]">
        <div
          className="flex items-start gap-3 p-4 cursor-pointer select-none"
          onClick={() => handleExpand(item.id)}
        >
          <button
            onClick={(e) => toggleSelect(item.id, e)}
            className="mt-0.5 flex-shrink-0 p-0.5 rounded transition-colors hover:bg-stone-700/30"
          >
            {isChecked ? (
              <CheckSquare size={18} className="text-amber-400" />
            ) : (
              <Square size={18} className="text-stone-600 group-hover:text-stone-500" />
            )}
          </button>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap mb-1.5">
              <span className="text-[11px] font-mono text-stone-400 bg-stone-800/80 px-1.5 py-0.5 rounded">
                #{item.id}
              </span>
              {item.category === 'deprecated' ? (
                <span className="text-[10px] font-mono text-amber-300 bg-amber-900/40 px-1.5 py-0.5 rounded flex items-center gap-1">
                  <Archive size={9} /> deprecated
                </span>
              ) : (
                <span className="text-[10px] font-mono text-rose-300 bg-rose-900/40 px-1.5 py-0.5 rounded flex items-center gap-1">
                  <Unlink size={9} /> orphaned
                </span>
              )}
              {item.migrated_to && (
                <span className="text-[10px] font-mono text-amber-300 bg-amber-900/30 px-1.5 py-0.5 rounded">
                  → #{item.migrated_to}
                </span>
              )}
              <span className="text-[11px] text-stone-500">
                {item.created_at ? format(new Date(item.created_at), 'yyyy-MM-dd HH:mm') : 'Unknown'}
              </span>
            </div>

            {item.migration_target && item.migration_target.paths.length > 0 && (
              <div className="flex items-center gap-1.5 flex-wrap mb-2">
                <ArrowRight size={12} className="text-amber-400/70 flex-shrink-0" />
                {item.migration_target.paths.map((p, i) => (
                  <span key={i} className="text-[11px] font-mono text-amber-300/90 bg-amber-900/25 px-1.5 py-0.5 rounded border border-amber-800/30">
                    {p}
                  </span>
                ))}
              </div>
            )}
            {item.migration_target && item.migration_target.paths.length === 0 && (
              <div className="flex items-center gap-1.5 mb-2">
                <ArrowRight size={12} className="text-stone-500 flex-shrink-0" />
                <span className="text-[11px] text-stone-500 italic">
                  target #{item.migration_target.id} also has no paths
                </span>
              </div>
            )}

            <div className="bg-stone-900/60 rounded p-2.5 text-[12px] text-stone-400 font-mono leading-relaxed line-clamp-3">
              {item.content_snippet}
            </div>
          </div>

          <div className="mt-1 flex-shrink-0 text-stone-500">
            {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </div>
        </div>

        {isExpanded && (
          <div className="border-t border-stone-700/30 p-5 bg-stone-900">
            {isLoadingDetail ? (
              <div className="flex items-center gap-3 text-stone-500 py-4">
                <div className="w-4 h-4 border-2 border-amber-500/30 border-t-amber-500 rounded-full animate-spin"></div>
                <span className="text-xs">Loading full content...</span>
              </div>
            ) : detail?.error ? (
              <div className="text-rose-400 text-xs py-2">Error: {detail.error}</div>
            ) : detail ? (
              <div className="space-y-4">
                <div>
                  <h4 className="text-[11px] uppercase tracking-widest text-stone-500 mb-2 font-semibold">
                    {detail.migration_target ? 'Old Version (This Memory)' : 'Full Content'}
                  </h4>
                  <div className="bg-stone-900 rounded p-4 border border-stone-800/60 text-[12px] text-stone-300 font-mono leading-relaxed whitespace-pre-wrap max-h-64 overflow-y-auto custom-scrollbar">
                    {detail.content}
                  </div>
                </div>

                {detail.migration_target && (
                  <div>
                    <h4 className="text-[11px] uppercase tracking-widest text-stone-500 mb-2 font-semibold flex items-center gap-2">
                      <span>Diff: #{item.id} → #{detail.migration_target.id}</span>
                      {detail.migration_target.paths.length > 0 && (
                        <span className="text-amber-400/70 normal-case tracking-normal font-normal">
                          ({detail.migration_target.paths[0]})
                        </span>
                      )}
                    </h4>
                    <div className="bg-stone-900 rounded border border-stone-800/60 p-4 max-h-96 overflow-y-auto custom-scrollbar">
                      <DiffViewer
                        oldText={detail.content}
                        newText={detail.migration_target.content}
                      />
                    </div>
                  </div>
                )}
              </div>
            ) : null}
          </div>
        )}
      </div>
    );
  };

  const renderSectionHeader = (icon, label, color, items) => {
    const allSelected = items.length > 0 && items.every(i => selectedIds.has(i.id));
    const someSelected = items.some(i => selectedIds.has(i.id));

    return (
      <div className="flex items-center gap-3 mb-4">
        <button
          onClick={() => toggleSelectAll(items)}
          className="p-0.5 rounded transition-colors hover:bg-stone-700/30"
          title={allSelected ? 'Deselect all' : 'Select all'}
        >
          {allSelected ? (
            <CheckSquare size={16} className={color} />
          ) : someSelected ? (
            <Minus size={16} className={color} />
          ) : (
            <Square size={16} className="text-stone-600" />
          )}
        </button>
        {icon}
        <h3 className={`text-xs font-bold uppercase tracking-widest ${color}`}>
          {label}
        </h3>
        <span className="text-[11px] text-stone-500 bg-stone-800/80 px-2 py-0.5 rounded-full">
          {items.length}
        </span>
      </div>
    );
  };

  return (
    <div className="palace-harmonized flex h-full overflow-hidden bg-stone-950 text-stone-200 font-sans selection:bg-amber-500/30 selection:text-amber-100">
      <div className="w-72 flex-shrink-0 bg-stone-900 border-r border-stone-700/30 flex flex-col p-6">
        <div className="mb-8">
          <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl border border-amber-800/30 bg-amber-950/30 shadow-[0_0_20px_rgba(245,158,11,0.1)]">
            <Feather className="text-amber-400" size={24} />
          </div>
          <h1 className="font-display mb-2 text-xl text-amber-50">Brain Cleanup</h1>
          <p className="text-[12px] text-stone-400 leading-relaxed">
            Manage orphan memories and low-vitality cleanup candidates with human confirmation.
          </p>
        </div>

        <div className="space-y-3 mt-auto">
          <div className="bg-stone-800/40 rounded-lg p-4 border border-stone-700/40">
            <div className="text-stone-400 text-xs uppercase font-bold tracking-wider mb-1">Deprecated</div>
            <div className="text-3xl font-mono text-amber-400">{deprecated.length}</div>
            <div className="text-stone-500 text-[11px] mt-1">old versions from updates</div>
          </div>
          <div className="bg-stone-800/40 rounded-lg p-4 border border-stone-700/40">
            <div className="text-stone-400 text-xs uppercase font-bold tracking-wider mb-1">Orphaned</div>
            <div className="text-3xl font-mono text-rose-400">{orphaned.length}</div>
            <div className="text-stone-500 text-[11px] mt-1">unreachable (no paths)</div>
          </div>
          <div className="bg-stone-800/40 rounded-lg p-4 border border-stone-700/40">
            <div className="text-stone-400 text-xs uppercase font-bold tracking-wider mb-1">Low Vitality</div>
            <div className="text-3xl font-mono text-sky-400">{vitalityCandidates.length}</div>
            <div className="text-stone-500 text-[11px] mt-1">{vitalityCanDeleteCount} deletable now</div>
          </div>
        </div>
      </div>

      <div className="flex-1 flex flex-col min-w-0 bg-stone-950 relative overflow-hidden">
        <div className="h-14 flex items-center justify-between px-8 border-b border-stone-700/30 bg-stone-950/90 backdrop-blur-md sticky top-0 z-10">
          <h2 className="text-sm font-bold text-stone-300 uppercase tracking-widest flex items-center gap-2">
            <Trash2 size={14} /> Maintenance Console
          </h2>
          <div className="flex items-center gap-2">
            {selectedIds.size > 0 && (
              <button
                onClick={handleBatchDelete}
                disabled={batchDeleting}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-rose-900/40 text-rose-300 hover:bg-rose-900/60 border border-rose-800/40 transition-colors disabled:opacity-50"
              >
                {batchDeleting ? (
                  <div className="w-3 h-3 border-2 border-rose-400/30 border-t-rose-400 rounded-full animate-spin"></div>
                ) : (
                  <Trash2 size={13} />
                )}
                Delete {selectedIds.size} orphans
              </button>
            )}
            <button
              onClick={() => {
                loadOrphans();
                loadVitalityCandidates();
              }}
              disabled={loading || vitalityLoading || vitalityProcessing}
              className="p-2 text-stone-400 hover:text-amber-400 hover:bg-stone-700/40 rounded-full transition-all disabled:opacity-50"
              title="Refresh"
            >
              <RefreshCw size={16} className={loading || vitalityLoading ? 'animate-spin' : ''} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-8 custom-scrollbar">
          <div className="max-w-5xl mx-auto space-y-8">
            <section>
              <div className="mb-4 flex items-center justify-between">
                <h3 className="text-xs font-bold uppercase tracking-widest text-amber-300 flex items-center gap-2">
                  <Archive size={14} /> Orphan Cleanup
                </h3>
                <span className="text-[11px] text-stone-500 bg-stone-800/80 px-2 py-0.5 rounded-full">
                  {orphans.length} total
                </span>
              </div>
              {loading ? (
                <div className="flex items-center gap-2 text-xs text-stone-500">
                  <div className="w-3 h-3 border-2 border-amber-500/30 border-t-amber-500 rounded-full animate-spin"></div>
                  Scanning orphan memories...
                </div>
              ) : error ? (
                <div className="text-rose-400 bg-rose-950/20 border border-rose-800/40 p-4 rounded-lg flex items-center gap-3">
                  <AlertTriangle size={18} />
                  <span className="text-sm">{error}</span>
                </div>
              ) : orphans.length === 0 ? (
                <div className="rounded-lg border border-stone-800 bg-stone-900/40 p-4 text-sm text-stone-500">
                  No orphan memories detected.
                </div>
              ) : (
                <div className="space-y-8">
                  {deprecated.length > 0 && (
                    <section>
                      {renderSectionHeader(
                        <Archive size={16} className="text-amber-400/80" />,
                        'Deprecated Versions',
                        'text-amber-400/80',
                        deprecated
                      )}
                      <div className="space-y-2">
                        {deprecated.map(renderCard)}
                      </div>
                    </section>
                  )}

                  {orphaned.length > 0 && (
                    <section>
                      {renderSectionHeader(
                        <Unlink size={16} className="text-rose-400/80" />,
                        'Orphaned Memories',
                        'text-rose-400/80',
                        orphaned
                      )}
                      <div className="space-y-2">
                        {orphaned.map(renderCard)}
                      </div>
                    </section>
                  )}
                </div>
              )}
            </section>

            <section className="rounded-lg border border-stone-800/80 bg-stone-900/30 p-5">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-xs font-bold uppercase tracking-widest text-sky-300 flex items-center gap-2">
                  <Trash2 size={14} /> Vitality Cleanup Candidates
                </h3>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => loadVitalityCandidates({ forceDecay: true })}
                    disabled={vitalityLoading || vitalityProcessing}
                    className="px-2.5 py-1 text-[11px] rounded border border-sky-800/50 text-sky-200 hover:bg-sky-900/30 disabled:opacity-50"
                  >
                    Run Decay + Refresh
                  </button>
                </div>
              </div>

              <div className="mb-4 flex flex-wrap items-center gap-3 text-xs">
                <label className="flex items-center gap-1 text-stone-400">
                  threshold
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={vitalityThreshold}
                    onChange={(e) => {
                      setVitalityThreshold(e.target.value);
                      invalidatePreparedReview();
                    }}
                    disabled={vitalityProcessing}
                    className="w-20 rounded border border-stone-700 bg-stone-900 px-2 py-1 text-stone-200"
                  />
                </label>
                <label className="flex items-center gap-1 text-stone-400">
                  inactive_days
                  <input
                    type="number"
                    min="0"
                    step="1"
                    value={vitalityInactiveDays}
                    onChange={(e) => {
                      setVitalityInactiveDays(e.target.value);
                      invalidatePreparedReview();
                    }}
                    disabled={vitalityProcessing}
                    className="w-20 rounded border border-stone-700 bg-stone-900 px-2 py-1 text-stone-200"
                  />
                </label>
                <label className="flex items-center gap-1 text-stone-400">
                  limit
                  <input
                    type="number"
                    min="1"
                    max="500"
                    step="1"
                    value={vitalityLimit}
                    onChange={(e) => {
                      setVitalityLimit(e.target.value);
                      invalidatePreparedReview();
                    }}
                    disabled={vitalityProcessing}
                    className="w-20 rounded border border-stone-700 bg-stone-900 px-2 py-1 text-stone-200"
                  />
                </label>
                <label className="flex items-center gap-1 text-stone-400">
                  reviewer
                  <input
                    type="text"
                    value={vitalityReviewer}
                    onChange={(e) => {
                      setVitalityReviewer(e.target.value);
                      invalidatePreparedReview();
                    }}
                    disabled={vitalityProcessing}
                    className="w-36 rounded border border-stone-700 bg-stone-900 px-2 py-1 text-stone-200"
                    placeholder="maintenance_dashboard"
                  />
                </label>
                <button
                  onClick={() => loadVitalityCandidates()}
                  disabled={vitalityLoading || vitalityProcessing}
                  className="px-2.5 py-1 text-[11px] rounded border border-stone-700 text-stone-200 hover:bg-stone-800/60 disabled:opacity-50"
                >
                  Apply Filters
                </button>
                <button
                  onClick={toggleVitalitySelectAll}
                  disabled={vitalityCandidates.length === 0 || vitalityProcessing}
                  className="px-2.5 py-1 text-[11px] rounded border border-stone-700 text-stone-300 hover:bg-stone-800/60 disabled:opacity-50"
                >
                  {vitalityCandidates.length > 0 && vitalityCandidates.every(item => vitalitySelectedIds.has(item.memory_id))
                    ? 'Deselect all'
                    : 'Select all'}
                </button>
              </div>

              <div className="mb-4 flex flex-wrap items-center gap-2">
                <button
                  onClick={handlePrepareVitalityKeep}
                  disabled={vitalitySelectedCount === 0 || vitalityProcessing}
                  className="px-3 py-1.5 text-xs rounded bg-sky-900/40 text-sky-200 border border-sky-800/50 hover:bg-sky-900/60 disabled:opacity-50"
                >
                  Prepare Keep ({vitalitySelectedCount})
                </button>
                <button
                  onClick={handlePrepareVitalityDelete}
                  disabled={vitalitySelectedCanDelete === 0 || vitalityProcessing}
                  className="px-3 py-1.5 text-xs rounded bg-amber-900/40 text-amber-200 border border-amber-800/50 hover:bg-amber-900/60 disabled:opacity-50"
                >
                  Prepare Delete ({vitalitySelectedCanDelete})
                </button>
                <button
                  onClick={handleConfirmVitalityCleanup}
                  disabled={!vitalityPreparedReview || vitalityProcessing}
                  className="px-3 py-1.5 text-xs rounded bg-rose-900/40 text-rose-200 border border-rose-800/50 hover:bg-rose-900/60 disabled:opacity-50"
                >
                  Confirm {vitalityPreparedReview?.action || 'Review'}
                </button>
                {vitalityPreparedReview && (
                  <button
                    onClick={invalidatePreparedReview}
                    disabled={vitalityProcessing}
                    className="px-3 py-1.5 text-xs rounded border border-stone-700 text-stone-300 hover:bg-stone-800/60 disabled:opacity-50"
                  >
                    Discard Review
                  </button>
                )}
                <span className="text-xs text-stone-500">
                  selected: {vitalitySelectedCount}, deletable selected: {vitalitySelectedCanDelete}
                </span>
              </div>

              {vitalityPreparedReview && (
                <div className="mb-4 rounded border border-amber-800/40 bg-amber-950/20 p-3 text-xs text-amber-200">
                  <div>review_id: {vitalityPreparedReview.review_id}</div>
                  <div>action: {vitalityPreparedReview.action}</div>
                  <div>reviewer: {vitalityPreparedReview.reviewer}</div>
                  <div>confirmation phrase: {vitalityPreparedReview.confirmation_phrase}</div>
                </div>
              )}

              {vitalityQueryMeta?.status === 'degraded' && (
                <div className="mb-4 rounded border border-amber-800/40 bg-amber-950/20 p-3 text-xs text-amber-200">
                  <div>status: degraded</div>
                  <div>reason: {vitalityQueryMeta?.decay?.reason || 'unknown'}</div>
                </div>
              )}

              {vitalityLastResult && (
                <div className="mb-4 rounded border border-sky-800/40 bg-sky-950/20 p-3 text-xs text-sky-200">
                  <div>status: {vitalityLastResult.status}</div>
                  <div>
                    deleted={vitalityLastResult.deleted_count} kept={vitalityLastResult.kept_count} skipped={vitalityLastResult.skipped_count} errors={vitalityLastResult.error_count}
                  </div>
                </div>
              )}

              {vitalityLoading ? (
                <div className="flex items-center gap-2 text-xs text-stone-500">
                  <div className="w-3 h-3 border-2 border-sky-500/30 border-t-sky-500 rounded-full animate-spin"></div>
                  Loading vitality candidates...
                </div>
              ) : vitalityError ? (
                <div className="rounded border border-rose-800/40 bg-rose-950/20 p-3 text-xs text-rose-300">
                  {typeof vitalityError === 'string' ? vitalityError : JSON.stringify(vitalityError)}
                </div>
              ) : vitalityCandidates.length === 0 ? (
                <div className="rounded border border-stone-800 bg-stone-900/40 p-3 text-xs text-stone-500">
                  No vitality candidates match current filters.
                </div>
              ) : (
                <div className="space-y-2">
                  {vitalityCandidates.map((item) => (
                    <div
                      key={item.memory_id}
                      className="rounded border border-stone-800 bg-stone-900/50 p-3"
                    >
                      <div className="flex flex-wrap items-center gap-2 mb-1.5">
                        <button
                          onClick={() => toggleVitalitySelect(item.memory_id)}
                          disabled={vitalityProcessing}
                          className="p-0.5 rounded hover:bg-stone-700/40 disabled:opacity-50"
                        >
                          {vitalitySelectedIds.has(item.memory_id) ? (
                            <CheckSquare size={14} className="text-sky-300" />
                          ) : (
                            <Square size={14} className="text-stone-500" />
                          )}
                        </button>
                        <span className="text-[11px] font-mono text-stone-300 bg-stone-800 px-1.5 py-0.5 rounded">
                          #{item.memory_id}
                        </span>
                        <span className="text-[11px] text-sky-300 bg-sky-900/30 px-1.5 py-0.5 rounded">
                          vitality {Number(item.vitality_score || 0).toFixed(3)}
                        </span>
                        <span className="text-[11px] text-stone-400 bg-stone-800/70 px-1.5 py-0.5 rounded">
                          inactive {Number(item.inactive_days || 0).toFixed(1)}d
                        </span>
                        <span className="text-[11px] text-stone-400 bg-stone-800/70 px-1.5 py-0.5 rounded">
                          access {item.access_count || 0}
                        </span>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${item.can_delete ? 'text-rose-300 bg-rose-900/30' : 'text-amber-300 bg-amber-900/30'}`}>
                          {item.can_delete ? 'deletable' : 'active paths'}
                        </span>
                      </div>
                      <div className="text-[11px] text-stone-500 mb-1.5">
                        {item.uri || '(no path)'}
                      </div>
                      <div className="rounded bg-stone-900 p-2 text-[12px] text-stone-400 font-mono leading-relaxed">
                        {item.content_snippet}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
