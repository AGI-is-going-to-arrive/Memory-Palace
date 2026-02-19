import React, { useCallback, useEffect, useMemo, useState } from 'react';
import clsx from 'clsx';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  Gauge,
  Radar,
  RefreshCw,
  Search,
  TimerReset,
  Wrench,
  Zap,
} from 'lucide-react';
import {
  cancelIndexJob,
  extractApiError,
  getIndexJob,
  getObservabilitySummary,
  retryIndexJob,
  runObservabilitySearch,
  triggerIndexRebuild,
  triggerMemoryReindex,
  triggerSleepConsolidation,
} from '../../lib/api';

const MODE_OPTIONS = ['hybrid', 'semantic', 'keyword'];
const PANEL_CLASS =
  'rounded-2xl border border-[color:var(--palace-line)] bg-[rgba(255,250,244,0.9)] p-4 shadow-[var(--palace-shadow-sm)] backdrop-blur-sm';
const INPUT_CLASS =
  'w-full rounded-lg border border-[color:var(--palace-line)] bg-white/90 px-3 py-2 text-sm text-[color:var(--palace-ink)] placeholder:text-[color:var(--palace-muted)] focus:outline-none focus:ring-2 focus:ring-[color:var(--palace-accent)]/35 focus:border-[color:var(--palace-accent)]';
const LABEL_CLASS = 'mb-2 block text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--palace-muted)]';

const formatNumber = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return '-';
  }
  return Number(value).toLocaleString();
};

const formatMs = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return '-';
  }
  return `${Number(value).toFixed(1)} ms`;
};

const formatDateTime = (value) => {
  if (!value || typeof value !== 'string') {
    return '-';
  }
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) {
    return value;
  }
  return new Date(parsed).toLocaleString();
};

const parseOptionalNonNegativeInteger = (rawValue, label) => {
  const normalized = String(rawValue ?? '').trim();
  if (!normalized) return null;
  if (!/^\d+$/.test(normalized)) {
    throw new Error(`${label} must be a non-negative integer`);
  }
  const parsed = Number(normalized);
  if (!Number.isSafeInteger(parsed)) {
    throw new Error(`${label} must be a non-negative integer`);
  }
  return parsed;
};

const parseRequiredIntegerInRange = (
  rawValue,
  label,
  { min = 1, max = Number.MAX_SAFE_INTEGER } = {}
) => {
  const normalized = String(rawValue ?? '').trim();
  if (!normalized) {
    throw new Error(`${label} is required`);
  }
  if (!/^\d+$/.test(normalized)) {
    throw new Error(`${label} must be an integer`);
  }
  const parsed = Number(normalized);
  if (!Number.isSafeInteger(parsed) || parsed < min || parsed > max) {
    throw new Error(`${label} must be in range [${min}, ${max}]`);
  }
  return parsed;
};

const getJobStatusTone = (status) => {
  if (status === 'succeeded') return 'good';
  if (status === 'failed' || status === 'dropped') return 'danger';
  if (status === 'cancelled' || status === 'cancelling') return 'warn';
  return 'neutral';
};

const isRetryEndpointUnsupported = (error) => {
  const statusCode = error?.response?.status;
  if (statusCode === 405) return true;
  if (statusCode !== 404) return false;

  const detail = error?.response?.data?.detail;
  const detailParts = [];
  const pushDetailPart = (value) => {
    if (typeof value !== 'string') return;
    const normalized = value.trim().toLowerCase();
    if (!normalized || detailParts.includes(normalized)) return;
    detailParts.push(normalized);
  };

  if (typeof detail === 'string') {
    pushDetailPart(detail);
  } else if (detail && typeof detail === 'object') {
    pushDetailPart(detail.error);
    pushDetailPart(detail.reason);
    pushDetailPart(detail.message);
    if (detailParts.length === 0) {
      try {
        pushDetailPart(JSON.stringify(detail));
      } catch (_error) {
        // ignore non-serializable details
      }
    }
  }
  const detailText = detailParts.join(' | ');
  const hasNotFoundSignature =
    detailText.includes('not found') || detailText.includes('not_found');
  if (!hasNotFoundSignature) return false;

  // New retry endpoint and old backend route mismatch should fallback to legacy calls.
  // But explicit job-not-found from new backend should not fallback.
  if (detailText.includes('job_not_found')) return false;
  if (detailText.includes('job') && detailText.includes('not found')) return false;
  return true;
};

function StatCard({ icon: Icon, label, value, hint, tone = 'neutral' }) {
  return (
    <div
      className={clsx(
        'rounded-2xl border p-4 backdrop-blur-sm transition duration-200 shadow-[var(--palace-shadow-sm)]',
        tone === 'good' && 'border-[rgba(179,133,79,0.45)] bg-[rgba(251,245,236,0.9)]',
        tone === 'warn' && 'border-[rgba(200,171,134,0.65)] bg-[rgba(244,236,224,0.92)]',
        tone === 'danger' && 'border-[rgba(143,106,69,0.5)] bg-[rgba(236,224,207,0.88)]',
        tone === 'neutral' && 'border-[color:var(--palace-line)] bg-[rgba(255,250,244,0.9)]'
      )}
    >
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-[0.16em] text-[color:var(--palace-muted)]">{label}</span>
        <Icon size={14} className="text-[color:var(--palace-accent-2)]" />
      </div>
      <div className="text-2xl font-semibold text-[color:var(--palace-ink)]">{value}</div>
      <div className="mt-1 text-xs text-[color:var(--palace-muted)]">{hint}</div>
    </div>
  );
}

function Badge({ children, tone = 'neutral' }) {
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded border px-2 py-0.5 text-[11px] font-medium',
        tone === 'good' && 'border-[rgba(179,133,79,0.5)] bg-[rgba(246,237,224,0.85)] text-[color:var(--palace-accent-2)]',
        tone === 'warn' && 'border-[rgba(200,171,134,0.65)] bg-[rgba(240,230,215,0.9)] text-[color:var(--palace-accent-2)]',
        tone === 'danger' && 'border-[rgba(143,106,69,0.45)] bg-[rgba(232,218,198,0.9)] text-[color:var(--palace-accent-2)]',
        tone === 'neutral' && 'border-[color:var(--palace-line)] bg-[rgba(255,250,244,0.84)] text-[color:var(--palace-muted)]'
      )}
    >
      {children}
    </span>
  );
}

function ResultCard({ item }) {
  const finalScore = item?.scores?.final;
  const scoreText = finalScore === undefined ? '-' : Number(finalScore).toFixed(4);
  const uri = item?.uri || '-';
  const snippet = item?.snippet || '(empty snippet)';
  const metadata = item?.metadata || {};
  const source = metadata.source || metadata.match_type || 'global';

  return (
    <article className="rounded-2xl border border-[color:var(--palace-line)] bg-[rgba(255,250,244,0.9)] p-4 shadow-[var(--palace-shadow-sm)] transition duration-200 hover:border-[color:var(--palace-accent-2)] hover:shadow-[var(--palace-shadow-md)]">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <code className="break-all text-xs text-[color:var(--palace-accent-2)]">{uri}</code>
        <div className="flex items-center gap-2">
          <Badge tone="neutral">score {scoreText}</Badge>
          <Badge tone={source === 'session_queue' ? 'good' : 'neutral'}>{source}</Badge>
        </div>
      </header>
      <p className="mb-3 whitespace-pre-wrap text-sm leading-relaxed text-[color:var(--palace-ink)]">{snippet}</p>
      <footer className="flex flex-wrap gap-2 text-[11px] text-[color:var(--palace-muted)]">
        <span>memory #{item?.memory_id ?? '-'}</span>
        <span>priority {metadata.priority ?? '-'}</span>
        <span>{metadata.updated_at || 'updated_at unknown'}</span>
      </footer>
    </article>
  );
}

export default function ObservabilityPage() {
  const [summary, setSummary] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState(null);

  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState(null);
  const [searchResult, setSearchResult] = useState(null);

  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildMessage, setRebuildMessage] = useState(null);
  const [sleepConsolidating, setSleepConsolidating] = useState(false);
  const [jobActionKey, setJobActionKey] = useState(null);
  const [activeJob, setActiveJob] = useState(null);
  const [activeJobLoading, setActiveJobLoading] = useState(false);
  const [detailJobError, setDetailJobError] = useState(null);
  const [inspectedJobId, setInspectedJobId] = useState(null);

  const [form, setForm] = useState({
    query: 'memory flush queue',
    mode: 'hybrid',
    maxResults: '8',
    candidateMultiplier: '4',
    includeSession: true,
    sessionId: 'api-observability',
    domain: '',
    pathPrefix: '',
    maxPriority: '',
  });
  const activeJobId = summary?.health?.runtime?.index_worker?.active_job_id || null;
  const detailJobId = inspectedJobId || activeJobId || null;
  const summaryTimestamp = summary?.timestamp || '';

  const loadSummary = useCallback(async () => {
    setSummaryLoading(true);
    setSummaryError(null);
    try {
      const data = await getObservabilitySummary();
      setSummary(data);
    } catch (err) {
      setSummaryError(extractApiError(err, 'Failed to load observability summary'));
    } finally {
      setSummaryLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    let disposed = false;
    if (!detailJobId) {
      setActiveJob(null);
      setActiveJobLoading(false);
      setDetailJobError(null);
      return () => {
        disposed = true;
      };
    }

    const loadActiveJob = async () => {
      setActiveJob(null);
      setActiveJobLoading(true);
      setDetailJobError(null);
      try {
        const payload = await getIndexJob(detailJobId);
        if (!disposed) {
          setActiveJob(payload?.job || null);
        }
      } catch (err) {
        if (!disposed) {
          setActiveJob(null);
          setDetailJobError(extractApiError(err, `Failed to load job ${detailJobId}`));
          const statusCode = err?.response?.status;
          if (statusCode === 404) {
            setInspectedJobId((prev) => (prev === detailJobId ? null : prev));
          }
        }
      } finally {
        if (!disposed) {
          setActiveJobLoading(false);
        }
      }
    };

    loadActiveJob();
    return () => {
      disposed = true;
    };
  }, [detailJobId, summaryTimestamp]);

  const onFieldChange = (name, value) => {
    setForm((prev) => ({ ...prev, [name]: value }));
  };

  const runSearch = async (event) => {
    event.preventDefault();
    setSearching(true);
    setSearchError(null);
    setRebuildMessage(null);
    try {
      const filters = {};
      if (form.domain.trim()) filters.domain = form.domain.trim();
      if (form.pathPrefix.trim()) filters.path_prefix = form.pathPrefix.trim();
      const maxPriority = parseOptionalNonNegativeInteger(form.maxPriority, 'max priority');
      if (maxPriority !== null) {
        filters.max_priority = maxPriority;
      }

      const payload = {
        query: form.query,
        mode: form.mode,
        max_results: parseRequiredIntegerInRange(form.maxResults, 'max results', {
          min: 1,
          max: 50,
        }),
        candidate_multiplier: parseRequiredIntegerInRange(
          form.candidateMultiplier,
          'candidate multiplier',
          { min: 1, max: 20 }
        ),
        include_session: form.includeSession,
        session_id: form.sessionId.trim() || null,
        filters,
      };

      const data = await runObservabilitySearch(payload);
      setSearchResult(data);
      await loadSummary();
    } catch (err) {
      setSearchError(extractApiError(err, 'Diagnostic search failed'));
    } finally {
      setSearching(false);
    }
  };

  const handleRebuild = async () => {
    setRebuilding(true);
    setRebuildMessage(null);
    try {
      const data = await triggerIndexRebuild({
        reason: 'observability_console',
        wait: false,
      });
      const jobId = data?.job_id ? `job ${data.job_id}` : 'sync';
      setRebuildMessage(`Rebuild requested (${jobId})`);
      await loadSummary();
    } catch (err) {
      setRebuildMessage(`Rebuild failed: ${extractApiError(err)}`);
    } finally {
      setRebuilding(false);
    }
  };

  const handleSleepConsolidation = async () => {
    setSleepConsolidating(true);
    setRebuildMessage(null);
    try {
      const data = await triggerSleepConsolidation({
        reason: 'observability_console',
        wait: false,
      });
      const jobId = data?.job_id ? `job ${data.job_id}` : 'sync';
      setRebuildMessage(`Sleep consolidation requested (${jobId})`);
      await loadSummary();
    } catch (err) {
      setRebuildMessage(`Sleep consolidation failed: ${extractApiError(err)}`);
    } finally {
      setSleepConsolidating(false);
    }
  };

  const handleCancelJob = async (jobId) => {
    if (!jobId) return;
    const actionKey = `cancel:${jobId}`;
    setJobActionKey(actionKey);
    setRebuildMessage(null);
    try {
      await cancelIndexJob(jobId, { reason: 'observability_console_cancel' });
      setRebuildMessage(`Cancel requested (${jobId})`);
      await loadSummary();
    } catch (err) {
      const statusCode = err?.response?.status;
      const detail = extractApiError(err, 'cancel request failed');
      const normalizedDetail = detail.trim().toLowerCase();
      const isJobNotFound =
        normalizedDetail.includes('job_not_found') ||
        (normalizedDetail.includes('job') && normalizedDetail.includes('not found'));
      const isAlreadyFinalized =
        normalizedDetail.includes('job_already_finalized') ||
        (normalizedDetail.includes('already') && normalizedDetail.includes('final'));
      if (statusCode === 404) {
        if (isJobNotFound) {
          setRebuildMessage(`Cancel skipped (${jobId}): job not found`);
          await loadSummary();
        } else {
          setRebuildMessage(`Cancel failed (${jobId}): ${detail}`);
        }
      } else if (statusCode === 409) {
        if (isAlreadyFinalized) {
          setRebuildMessage(`Cancel skipped (${jobId}): already finalized`);
          await loadSummary();
        } else {
          setRebuildMessage(`Cancel failed (${jobId}): ${detail}`);
        }
      } else {
        setRebuildMessage(`Cancel failed (${jobId}): ${detail}`);
      }
    } finally {
      setJobActionKey(null);
    }
  };

  const handleRetryJob = async (job) => {
    const jobId = job?.job_id;
    if (!jobId) return;
    const actionKey = `retry:${jobId}`;
    setJobActionKey(actionKey);
    setRebuildMessage(null);

    const retryReason = `retry:${jobId}`;
    const taskType = String(job?.task_type || '');
    const retryMemoryId = Number(job?.memory_id);
    try {
      let payload = null;
      try {
        payload = await retryIndexJob(jobId, { reason: retryReason });
      } catch (err) {
        if (isRetryEndpointUnsupported(err)) {
          if (taskType === 'reindex_memory' && Number.isInteger(retryMemoryId) && retryMemoryId > 0) {
            payload = await triggerMemoryReindex(retryMemoryId, {
              reason: retryReason,
              wait: false,
            });
          } else if (taskType === 'rebuild_index') {
            payload = await triggerIndexRebuild({
              reason: retryReason,
              wait: false,
            });
          } else if (taskType === 'sleep_consolidation') {
            payload = await triggerSleepConsolidation({
              reason: retryReason,
              wait: false,
            });
          } else {
            throw new Error(`retry for task type '${taskType || 'unknown'}' is not supported`);
          }
        } else {
          throw err;
        }
      }
      const requestedJob = payload?.job_id ? `job ${payload.job_id}` : 'sync';
      setRebuildMessage(`Retry requested (${requestedJob})`);
      await loadSummary();
    } catch (err) {
      setRebuildMessage(`Retry failed (${jobId}): ${extractApiError(err)}`);
    } finally {
      setJobActionKey(null);
    }
  };

  const searchStats = summary?.search_stats || {};
  const latency = searchStats.latency_ms || {};
  const health = summary?.health || {};
  const indexHealth = health.index || {};
  const runtime = health.runtime || {};
  const worker = runtime.index_worker || {};
  const sleepConsolidation = runtime.sleep_consolidation || summary?.sleep_consolidation || {};
  const indexLatency = summary?.index_latency || {};
  const cleanupQueryStats = summary?.cleanup_query_stats || {};
  const cleanupLatency = cleanupQueryStats.latency_ms || {};
  const recentJobs = Array.isArray(worker.recent_jobs) ? worker.recent_jobs : [];
  const viewingActiveJob = Boolean(detailJobId && activeJobId && detailJobId === activeJobId);

  const modeBreakdown = useMemo(() => {
    const breakdown = searchStats.mode_breakdown || {};
    return Object.entries(breakdown);
  }, [searchStats.mode_breakdown]);

  const intentBreakdown = useMemo(() => {
    const breakdown = searchStats.intent_breakdown || {};
    return Object.entries(breakdown);
  }, [searchStats.intent_breakdown]);

  const strategyBreakdown = useMemo(() => {
    const breakdown = searchStats.strategy_hit_breakdown || {};
    return Object.entries(breakdown);
  }, [searchStats.strategy_hit_breakdown]);

  return (
    <div className="palace-harmonized flex h-full flex-col overflow-hidden bg-[color:var(--palace-bg)] text-[color:var(--palace-ink)] selection:bg-[rgba(179,133,79,0.28)] selection:text-[color:var(--palace-ink)]">
      <header className="border-b border-[color:var(--palace-line)] bg-[radial-gradient(circle_at_top_right,rgba(198,165,126,0.24),rgba(241,232,220,0.72),rgba(246,242,234,0.92)_58%)] px-6 py-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-display flex items-center gap-2 text-lg text-[color:var(--palace-ink)]">
              <Radar size={18} className="text-[color:var(--palace-accent)]" />
              Retrieval Observability Console
            </h1>
            <p className="mt-1 text-sm text-[color:var(--palace-muted)]">
              Track search latency, degrade reasons, cache hits, and index worker health.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={loadSummary}
              disabled={summaryLoading}
              className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-[color:var(--palace-line)] bg-white/88 px-3 py-2 text-xs font-medium text-[color:var(--palace-muted)] transition-colors hover:border-[color:var(--palace-accent)] hover:text-[color:var(--palace-ink)] disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-[color:var(--palace-accent)]/35"
            >
              <RefreshCw size={14} className={summaryLoading ? 'animate-spin' : ''} />
              Refresh
            </button>
            <button
              type="button"
              onClick={handleRebuild}
              disabled={rebuilding}
              className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-[color:var(--palace-accent)] bg-[linear-gradient(135deg,rgba(198,165,126,0.38),rgba(255,250,244,0.9))] px-3 py-2 text-xs font-medium text-[color:var(--palace-ink)] transition-colors hover:border-[color:var(--palace-accent-2)] hover:bg-[linear-gradient(135deg,rgba(190,154,112,0.42),rgba(255,250,244,0.95))] disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-[color:var(--palace-accent)]/35"
            >
              {rebuilding ? (
                <RefreshCw size={14} className="animate-spin" />
              ) : (
                <Wrench size={14} />
              )}
              Rebuild Index
            </button>
            <button
              type="button"
              onClick={handleSleepConsolidation}
              disabled={sleepConsolidating}
              className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-[color:var(--palace-line)] bg-white/88 px-3 py-2 text-xs font-medium text-[color:var(--palace-muted)] transition-colors hover:border-[color:var(--palace-accent)] hover:text-[color:var(--palace-ink)] disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-[color:var(--palace-accent)]/35"
            >
              {sleepConsolidating ? (
                <RefreshCw size={14} className="animate-spin" />
              ) : (
                <TimerReset size={14} />
              )}
              Sleep Consolidation
            </button>
          </div>
        </div>
        {rebuildMessage && (
          <p className="mt-3 text-xs text-[color:var(--palace-muted)]">{rebuildMessage}</p>
        )}
        {summaryError && (
          <div className="mt-3 inline-flex items-center gap-2 rounded-md border border-[rgba(143,106,69,0.45)] bg-[rgba(232,218,198,0.88)] px-3 py-2 text-xs text-[color:var(--palace-accent-2)]">
            <AlertTriangle size={13} />
            {summaryError}
          </div>
        )}
      </header>

      <main className="flex-1 overflow-y-auto px-6 py-5">
        <section className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
          <StatCard
            icon={Search}
            label="Queries"
            value={formatNumber(searchStats.total_queries)}
            hint={`degraded ${formatNumber(searchStats.degraded_queries)}`}
            tone="neutral"
          />
          <StatCard
            icon={TimerReset}
            label="Latency"
            value={formatMs(latency.avg)}
            hint={`p95 ${formatMs(latency.p95)}`}
            tone="neutral"
          />
          <StatCard
            icon={Zap}
            label="Cache Hit Ratio"
            value={`${((searchStats.cache_hit_ratio || 0) * 100).toFixed(1)}%`}
            hint={`hit queries ${formatNumber(searchStats.cache_hit_queries)}`}
            tone={searchStats.cache_hit_ratio > 0.4 ? 'good' : 'neutral'}
          />
          <StatCard
            icon={Gauge}
            label="Index Latency"
            value={formatMs(indexLatency.avg_ms)}
            hint={`samples ${formatNumber(indexLatency.samples)}`}
            tone={indexLatency.samples > 0 ? 'neutral' : 'warn'}
          />
          <StatCard
            icon={Database}
            label="Cleanup p95"
            value={formatMs(cleanupLatency.p95)}
            hint={`slow ${formatNumber(cleanupQueryStats.slow_queries)} (>=${formatMs(cleanupQueryStats.slow_threshold_ms)})`}
            tone={cleanupQueryStats.slow_queries > 0 ? 'warn' : 'neutral'}
          />
          <StatCard
            icon={Activity}
            label="Cleanup Index Hit"
            value={`${((cleanupQueryStats.index_hit_ratio || 0) * 100).toFixed(1)}%`}
            hint={`full scan ${formatNumber(cleanupQueryStats.full_scan_queries)}`}
            tone={cleanupQueryStats.index_hit_ratio >= 0.9 ? 'good' : cleanupQueryStats.index_hit_ratio >= 0.5 ? 'neutral' : 'warn'}
          />
        </section>

        <section className="grid gap-4 xl:grid-cols-[360px_1fr]">
          <div className="space-y-4">
            <form
              onSubmit={runSearch}
              noValidate
              className={PANEL_CLASS}
            >
              <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold text-[color:var(--palace-ink)]">
                <Activity size={15} className="text-[color:var(--palace-accent)]" />
                Search Console
              </h2>

              <label htmlFor="obs-query-input" className={LABEL_CLASS}>
                Query
              </label>
              <input
                id="obs-query-input"
                name="query"
                value={form.query}
                onChange={(e) => onFieldChange('query', e.target.value)}
                className={`mb-3 ${INPUT_CLASS}`}
                placeholder="search terms..."
              />

              <div className="mb-3 grid grid-cols-2 gap-2">
                <div>
                  <label htmlFor="obs-mode-select" className={LABEL_CLASS}>
                    Mode
                  </label>
                  <select
                    id="obs-mode-select"
                    name="mode"
                    value={form.mode}
                    onChange={(e) => onFieldChange('mode', e.target.value)}
                    className={INPUT_CLASS}
                  >
                    {MODE_OPTIONS.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="obs-session-id-input" className={LABEL_CLASS}>
                    Session Id
                  </label>
                  <input
                    id="obs-session-id-input"
                    name="session_id"
                    value={form.sessionId}
                    onChange={(e) => onFieldChange('sessionId', e.target.value)}
                    className={INPUT_CLASS}
                    placeholder="api-observability"
                  />
                </div>
              </div>

              <div className="mb-3 grid grid-cols-2 gap-2">
                <div>
                  <label htmlFor="obs-max-results-input" className={LABEL_CLASS}>
                    Max Results
                  </label>
                  <input
                    id="obs-max-results-input"
                    name="max_results"
                    type="number"
                    min="1"
                    max="50"
                    value={form.maxResults}
                    onChange={(e) => onFieldChange('maxResults', e.target.value)}
                    className={INPUT_CLASS}
                  />
                </div>
                <div>
                  <label htmlFor="obs-candidate-multiplier-input" className={LABEL_CLASS}>
                    Candidate x
                  </label>
                  <input
                    id="obs-candidate-multiplier-input"
                    name="candidate_multiplier"
                    type="number"
                    min="1"
                    max="20"
                    value={form.candidateMultiplier}
                    onChange={(e) => onFieldChange('candidateMultiplier', e.target.value)}
                    className={INPUT_CLASS}
                  />
                </div>
              </div>

              <div className="mb-3 grid grid-cols-2 gap-2">
                <input
                  id="obs-domain-filter-input"
                  name="domain_filter"
                  aria-label="Domain filter"
                  value={form.domain}
                  onChange={(e) => onFieldChange('domain', e.target.value)}
                  className={INPUT_CLASS}
                  placeholder="domain filter"
                />
                <input
                  id="obs-path-prefix-input"
                  name="path_prefix"
                  aria-label="Path prefix filter"
                  value={form.pathPrefix}
                  onChange={(e) => onFieldChange('pathPrefix', e.target.value)}
                  className={INPUT_CLASS}
                  placeholder="path prefix"
                />
              </div>

              <div className="mb-4 flex items-center justify-between gap-2">
                <input
                  id="obs-max-priority-input"
                  name="max_priority"
                  type="number"
                  min="0"
                  step="1"
                  aria-label="Max priority filter"
                  value={form.maxPriority}
                  onChange={(e) => onFieldChange('maxPriority', e.target.value)}
                  className={INPUT_CLASS}
                  placeholder="max priority"
                />
                <label
                  htmlFor="obs-include-session-checkbox"
                  className="inline-flex cursor-pointer items-center gap-2 text-xs text-[color:var(--palace-muted)]"
                >
                  <input
                    id="obs-include-session-checkbox"
                    name="include_session"
                    type="checkbox"
                    checked={form.includeSession}
                    onChange={(e) => onFieldChange('includeSession', e.target.checked)}
                    className="h-4 w-4 rounded border-[color:var(--palace-line)] bg-white text-[color:var(--palace-accent)] focus:ring-[color:var(--palace-accent)]/40"
                  />
                  session-first
                </label>
              </div>

              <button
                type="submit"
                disabled={searching}
                className="inline-flex w-full cursor-pointer items-center justify-center gap-2 rounded-lg border border-[color:var(--palace-accent)] bg-[linear-gradient(135deg,rgba(198,165,126,0.34),rgba(255,250,244,0.92))] px-3 py-2 text-sm font-medium text-[color:var(--palace-ink)] transition-colors hover:border-[color:var(--palace-accent-2)] hover:bg-[linear-gradient(135deg,rgba(191,154,110,0.42),rgba(255,250,244,0.95))] disabled:cursor-not-allowed disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-[color:var(--palace-accent)]/35"
              >
                {searching ? (
                  <RefreshCw size={14} className="animate-spin" />
                ) : (
                  <Search size={14} />
                )}
                Run Diagnostic Search
              </button>

              {searchError && (
                <div className="mt-3 rounded-md border border-[rgba(143,106,69,0.45)] bg-[rgba(232,218,198,0.88)] px-3 py-2 text-xs text-[color:var(--palace-accent-2)]">
                  {searchError}
                </div>
              )}
            </form>

            <div className={PANEL_CLASS}>
              <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-[color:var(--palace-ink)]">
                <Database size={15} className="text-[color:var(--palace-accent)]" />
                Runtime Snapshot
              </h3>
              <div className="space-y-2 text-xs text-[color:var(--palace-muted)]">
                <p className="flex items-center gap-2">
                  {summary?.status === 'ok' ? (
                    <CheckCircle2 size={13} className="text-[color:var(--palace-accent)]" />
                  ) : (
                    <AlertTriangle size={13} className="text-[color:var(--palace-accent-2)]" />
                  )}
                  status: {summary?.status || 'unknown'}
                </p>
                <p>index degraded: {String(Boolean(indexHealth.degraded))}</p>
                <p>queue depth: {worker.queue_depth ?? '-'}</p>
                <p>active job: {worker.active_job_id || '-'}</p>
                <p>cancelling jobs: {worker.cancelling_jobs ?? 0}</p>
                <p>last worker error: {worker.last_error || '-'}</p>
                <p>sleep pending: {String(Boolean(worker.sleep_pending))}</p>
                <p>sleep last reason: {sleepConsolidation.reason || '-'}</p>
                <p>cleanup queries: {formatNumber(cleanupQueryStats.total_queries)}</p>
                <p>updated at: {summary?.timestamp || '-'}</p>
              </div>
            </div>

            <div className={PANEL_CLASS}>
              <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-[color:var(--palace-ink)]">
                <Wrench size={15} className="text-[color:var(--palace-accent)]" />
                Index Task Queue
              </h3>
              {activeJobLoading && (
                <p className="mb-2 text-xs text-[color:var(--palace-muted)]">
                  Loading active job...
                </p>
              )}
              {detailJobError && (
                <p className="mb-2 text-xs text-[color:var(--palace-accent-2)]">
                  {detailJobError}
                </p>
              )}
              {detailJobId && activeJob && (
                (() => {
                  const jobId = String(activeJob.job_id || detailJobId);
                  const status = String(activeJob.status || 'unknown');
                  const taskType = String(activeJob.task_type || 'unknown');
                  const canCancel = ['queued', 'running', 'cancelling'].includes(status);
                  const canRetry = ['failed', 'dropped', 'cancelled'].includes(status);
                  const cancelPending = jobActionKey === `cancel:${jobId}`;
                  const retryPending = jobActionKey === `retry:${jobId}`;
                  const errorText = activeJob?.error || activeJob?.result?.error || '-';
                  const degradeReasons = Array.isArray(activeJob?.result?.degrade_reasons)
                    ? activeJob.result.degrade_reasons.join(', ')
                    : '-';
                  return (
                    <article className="mb-3 rounded-xl border border-[color:var(--palace-accent)]/45 bg-[rgba(255,248,238,0.9)] p-3 text-xs text-[color:var(--palace-muted)]">
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        <Badge tone={viewingActiveJob ? 'good' : 'neutral'}>
                          {viewingActiveJob ? 'active' : 'detail'}
                        </Badge>
                        <code className="text-[11px] text-[color:var(--palace-accent-2)]">{jobId}</code>
                        <Badge tone={getJobStatusTone(status)}>{status}</Badge>
                        <Badge tone="neutral">{taskType}</Badge>
                      </div>
                      <div className="space-y-1">
                        <p>reason: {activeJob?.reason || '-'}</p>
                        <p>memory: {activeJob?.memory_id ?? '-'}</p>
                        <p>error: {errorText}</p>
                        <p>cancel reason: {activeJob?.cancel_reason || '-'}</p>
                        <p>degrade reasons: {degradeReasons || '-'}</p>
                        <p>requested: {formatDateTime(activeJob?.requested_at)}</p>
                        <p>started: {formatDateTime(activeJob?.started_at)}</p>
                        <p>finished: {formatDateTime(activeJob?.finished_at)}</p>
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <button
                          type="button"
                          disabled={!canCancel || cancelPending}
                          onClick={() => handleCancelJob(jobId)}
                          className="inline-flex cursor-pointer items-center gap-1 rounded border border-[color:var(--palace-line)] bg-white/90 px-2 py-1 text-[11px] text-[color:var(--palace-muted)] transition-colors hover:border-[color:var(--palace-accent)] hover:text-[color:var(--palace-ink)] disabled:cursor-not-allowed disabled:opacity-45"
                        >
                          {cancelPending ? <RefreshCw size={12} className="animate-spin" /> : <AlertTriangle size={12} />}
                          Cancel
                        </button>
                        <button
                          type="button"
                          disabled={!canRetry || retryPending}
                          onClick={() => handleRetryJob(activeJob)}
                          className="inline-flex cursor-pointer items-center gap-1 rounded border border-[color:var(--palace-line)] bg-white/90 px-2 py-1 text-[11px] text-[color:var(--palace-muted)] transition-colors hover:border-[color:var(--palace-accent)] hover:text-[color:var(--palace-ink)] disabled:cursor-not-allowed disabled:opacity-45"
                        >
                          {retryPending ? <RefreshCw size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                          Retry
                        </button>
                        {inspectedJobId && (
                          <button
                            type="button"
                            onClick={() => setInspectedJobId(null)}
                            className="inline-flex cursor-pointer items-center gap-1 rounded border border-[color:var(--palace-line)] bg-white/90 px-2 py-1 text-[11px] text-[color:var(--palace-muted)] transition-colors hover:border-[color:var(--palace-accent)] hover:text-[color:var(--palace-ink)]"
                          >
                            {activeJobId ? 'Back to Active' : 'Clear Detail'}
                          </button>
                        )}
                      </div>
                    </article>
                  );
                })()
              )}
              {recentJobs.length === 0 ? (
                <p className="text-xs text-[color:var(--palace-muted)]">
                  No recent index jobs.
                </p>
              ) : (
                <div className="space-y-2">
                  {recentJobs.map((job) => {
                    const jobId = String(job?.job_id || 'unknown-job');
                    const status = String(job?.status || 'unknown');
                    const taskType = String(job?.task_type || 'unknown');
                    const canCancel = ['queued', 'running', 'cancelling'].includes(status);
                    const canRetry = ['failed', 'dropped', 'cancelled'].includes(status);
                    const cancelPending = jobActionKey === `cancel:${jobId}`;
                    const retryPending = jobActionKey === `retry:${jobId}`;
                    const errorText = job?.error || job?.result?.error || '-';

                    return (
                      <article
                        key={jobId}
                        className="rounded-xl border border-[color:var(--palace-line)] bg-[rgba(255,250,244,0.84)] p-3 text-xs text-[color:var(--palace-muted)]"
                      >
                        <div className="mb-2 flex flex-wrap items-center gap-2">
                          <code className="text-[11px] text-[color:var(--palace-accent-2)]">{jobId}</code>
                          <Badge tone={getJobStatusTone(status)}>{status}</Badge>
                          <Badge tone="neutral">{taskType}</Badge>
                        </div>
                        <div className="space-y-1">
                          <p>reason: {job?.reason || '-'}</p>
                          <p>memory: {job?.memory_id ?? '-'}</p>
                          <p>error: {errorText}</p>
                          <p>requested: {formatDateTime(job?.requested_at)}</p>
                          <p>started: {formatDateTime(job?.started_at)}</p>
                          <p>finished: {formatDateTime(job?.finished_at)}</p>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2">
                          <button
                            type="button"
                            disabled={!canCancel || cancelPending}
                            onClick={() => handleCancelJob(jobId)}
                            className="inline-flex cursor-pointer items-center gap-1 rounded border border-[color:var(--palace-line)] bg-white/90 px-2 py-1 text-[11px] text-[color:var(--palace-muted)] transition-colors hover:border-[color:var(--palace-accent)] hover:text-[color:var(--palace-ink)] disabled:cursor-not-allowed disabled:opacity-45"
                          >
                            {cancelPending ? <RefreshCw size={12} className="animate-spin" /> : <AlertTriangle size={12} />}
                            Cancel
                          </button>
                          <button
                            type="button"
                            disabled={!canRetry || retryPending}
                            onClick={() => handleRetryJob(job)}
                            className="inline-flex cursor-pointer items-center gap-1 rounded border border-[color:var(--palace-line)] bg-white/90 px-2 py-1 text-[11px] text-[color:var(--palace-muted)] transition-colors hover:border-[color:var(--palace-accent)] hover:text-[color:var(--palace-ink)] disabled:cursor-not-allowed disabled:opacity-45"
                          >
                            {retryPending ? <RefreshCw size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                            Retry
                          </button>
                          <button
                            type="button"
                            onClick={() => setInspectedJobId(jobId)}
                            className="inline-flex cursor-pointer items-center gap-1 rounded border border-[color:var(--palace-line)] bg-white/90 px-2 py-1 text-[11px] text-[color:var(--palace-muted)] transition-colors hover:border-[color:var(--palace-accent)] hover:text-[color:var(--palace-ink)]"
                          >
                            Inspect
                          </button>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </div>

            {modeBreakdown.length > 0 && (
              <div className={PANEL_CLASS}>
                <h3 className="mb-3 text-sm font-semibold text-[color:var(--palace-ink)]">Mode Breakdown</h3>
                <div className="flex flex-wrap gap-2">
                  {modeBreakdown.map(([mode, count]) => (
                    <Badge key={mode} tone="neutral">
                      {mode}: {count}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {intentBreakdown.length > 0 && (
              <div className={PANEL_CLASS}>
                <h3 className="mb-3 text-sm font-semibold text-[color:var(--palace-ink)]">Intent Breakdown</h3>
                <div className="flex flex-wrap gap-2">
                  {intentBreakdown.map(([intent, count]) => (
                    <Badge key={intent} tone="neutral">
                      {intent}: {count}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {strategyBreakdown.length > 0 && (
              <div className={PANEL_CLASS}>
                <h3 className="mb-3 text-sm font-semibold text-[color:var(--palace-ink)]">Strategy Hits</h3>
                <div className="flex flex-wrap gap-2">
                  {strategyBreakdown.map(([strategy, count]) => (
                    <Badge key={strategy} tone="neutral">
                      {strategy}: {count}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="space-y-4">
            <div className={PANEL_CLASS}>
              <h2 className="mb-3 text-sm font-semibold text-[color:var(--palace-ink)]">Search Diagnostics</h2>
              {!searchResult ? (
                <p className="text-sm text-[color:var(--palace-muted)]">
                  Run a search to inspect latency, degrade reasons, and ranked snippets.
                </p>
              ) : (
                <div className="space-y-3 text-xs text-[color:var(--palace-muted)]">
                  <div className="flex flex-wrap gap-2">
                    <Badge tone="neutral">latency {formatMs(searchResult.latency_ms)}</Badge>
                    <Badge tone="neutral">mode {searchResult.mode_applied}</Badge>
                    <Badge tone="neutral">
                      intent {searchResult.intent_applied || searchResult.intent || 'unknown'}
                    </Badge>
                    <Badge tone="neutral">
                      strategy {searchResult.strategy_template_applied || searchResult.strategy_template || searchResult.intent_profile?.strategy_template || 'default'}
                    </Badge>
                    <Badge tone={searchResult.degraded ? 'warn' : 'good'}>
                      degraded {String(Boolean(searchResult.degraded))}
                    </Badge>
                    <Badge tone="neutral">
                      counts s:{searchResult.counts?.session ?? 0} g:{searchResult.counts?.global ?? 0} r:{searchResult.counts?.returned ?? 0}
                    </Badge>
                  </div>
                  {Array.isArray(searchResult.degrade_reasons) && searchResult.degrade_reasons.length > 0 && (
                    <div className="rounded-lg border border-[rgba(198,165,126,0.55)] bg-[rgba(240,230,215,0.78)] p-3">
                      <div className="mb-2 text-[11px] uppercase tracking-[0.14em] text-[color:var(--palace-accent-2)]">
                        degrade reasons
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {searchResult.degrade_reasons.map((reason) => (
                          <Badge key={reason} tone="warn">
                            {reason}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="space-y-3">
              {searching && (
                <div className="flex items-center gap-2 rounded-lg border border-[color:var(--palace-line)] bg-[rgba(255,250,244,0.86)] px-3 py-2 text-sm text-[color:var(--palace-muted)]">
                  <RefreshCw size={14} className="animate-spin" />
                  running diagnostic query...
                </div>
              )}
              {!searching && searchResult?.results?.length === 0 && (
                <div className="rounded-lg border border-[color:var(--palace-line)] bg-[rgba(255,250,244,0.86)] px-3 py-3 text-sm text-[color:var(--palace-muted)]">
                  No matched snippets.
                </div>
              )}
              {(searchResult?.results || []).map((item, idx) => (
                <ResultCard key={`${item.uri || 'result'}-${idx}`} item={item} />
              ))}
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
