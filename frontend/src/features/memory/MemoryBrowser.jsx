import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import clsx from 'clsx';
import { motion, AnimatePresence } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  BookOpenText,
  ChevronRight,
  Compass,
  Edit3,
  Filter,
  Folder,
  Home,
  Plus,
  RefreshCw,
  Save,
  Search,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';

import {
  createMemoryNode,
  deleteMemoryNode,
  extractApiError,
  getMemoryNode,
  updateMemoryNode,
} from '../../lib/api';
import GlassCard from '../../components/GlassCard';

const isAbortError = (error) =>
  Boolean(
    error &&
      (error.code === 'ERR_CANCELED' ||
        error.name === 'AbortError' ||
        error.name === 'CanceledError')
  );
const CHILD_PAGE_SIZE = 50;
const BROWSABLE_DOMAINS = ['core', 'notes', 'writer', 'game'];

function CrumbBar({ items, onNavigate }) {
  const { t } = useTranslation();

  return (
    <div className="flex items-center gap-1 overflow-x-auto rounded-full border border-[color:var(--palace-glass-border)] bg-white/40 backdrop-blur-md px-3 py-1.5 shadow-sm">
      <button
        type="button"
        onClick={() => onNavigate('')}
        className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-full text-[color:var(--palace-muted)] transition hover:bg-white/60 hover:text-[color:var(--palace-ink)]"
      >
        <Home size={14} />
      </button>
      {items.map((item, idx) => (
        <React.Fragment key={item.path}>
          <ChevronRight size={12} className="text-[color:var(--palace-muted)]/60" />
          <button
            type="button"
            onClick={() => onNavigate(item.path)}
            className={clsx(
              'cursor-pointer whitespace-nowrap rounded-full px-3 py-1 text-xs font-medium transition',
              idx === items.length - 1
                ? 'bg-white/80 text-[color:var(--palace-ink)] shadow-sm ring-1 ring-black/5'
                : 'text-[color:var(--palace-muted)] hover:bg-white/50 hover:text-[color:var(--palace-ink)]'
            )}
          >
            {!item.label || item.label === 'root' ? t('memory.rootBreadcrumb') : item.label}
          </button>
        </React.Fragment>
      ))}
    </div>
  );
}

function ChildCard({ child, onOpen }) {
  const { t } = useTranslation();
  const preview = child.gist_text || child.content_snippet || t('common.states.noPreview');
  return (
    <GlassCard
      as={motion.button}
      onClick={onOpen}
      className="group w-full cursor-pointer p-5 text-left bg-white/40 hover:bg-white/60 border-white/40"
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <div className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-[color:var(--palace-accent)]/10 to-[color:var(--palace-accent-2)]/5 text-[color:var(--palace-accent-2)] ring-1 ring-[color:var(--palace-accent)]/10">
          <Folder size={16} />
        </div>
        <span className="rounded-full border border-[color:var(--palace-line)] bg-white/50 px-2 py-0.5 text-[10px] font-semibold text-[color:var(--palace-muted)] backdrop-blur-sm">
          p{child.priority ?? 0}
        </span>
      </div>
      <div className="mb-1.5 line-clamp-1 text-sm font-semibold text-[color:var(--palace-ink)] group-hover:text-[color:var(--palace-accent-2)] transition-colors">
        {child.name || child.path}
      </div>
      <div className="line-clamp-3 text-xs leading-relaxed text-[color:var(--palace-muted)]">
        {preview}
      </div>
    </GlassCard>
  );
}

export default function MemoryBrowser() {
  const { t } = useTranslation();
  const defaultConversation = t('memory.defaultConversation');
  const [searchParams, setSearchParams] = useSearchParams();
  const hasDomainParam = searchParams.has('domain');
  const hasPathParam = searchParams.has('path');
  const shouldUseProjectNotesDefault = !hasDomainParam && !hasPathParam;
  const domain = shouldUseProjectNotesDefault ? 'notes' : (searchParams.get('domain') || 'core');
  const path = shouldUseProjectNotesDefault ? 'projects' : (searchParams.get('path') || '');

  const [loading, setLoading] = useState(true);
  const [errorState, setErrorState] = useState(null);
  const [data, setData] = useState({ node: null, children: [], breadcrumbs: [] });

  const [searchValue, setSearchValue] = useState('');
  const [priorityFilter, setPriorityFilter] = useState('');

  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [editDisclosure, setEditDisclosure] = useState('');
  const [editPriority, setEditPriority] = useState(0);
  const [contentView, setContentView] = useState('original');

  const [composerTitle, setComposerTitle] = useState('');
  const [composerDisclosure, setComposerDisclosure] = useState('');
  const [composerPriority, setComposerPriority] = useState(0);
  const [conversation, setConversation] = useState(() => defaultConversation);
  const [conversationDirty, setConversationDirty] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const [visibleChildCount, setVisibleChildCount] = useState(CHILD_PAGE_SIZE);
  const nodeRequestRef = useRef(0);
  const nodeAbortControllerRef = useRef(null);

  const isRoot = !path;
  useEffect(() => {
    if (!shouldUseProjectNotesDefault) return;
    const params = new URLSearchParams();
    params.set('domain', 'notes');
    params.set('path', 'projects');
    setSearchParams(params, { replace: true });
  }, [setSearchParams, shouldUseProjectNotesDefault]);

  const error = useMemo(() => {
    if (!errorState) return null;
    return extractApiError(errorState.error, t(errorState.fallbackKey));
  }, [errorState, t]);

  const refreshNode = useCallback(async () => {
    const requestId = ++nodeRequestRef.current;
    nodeAbortControllerRef.current?.abort();
    const controller = new AbortController();
    nodeAbortControllerRef.current = controller;
    setLoading(true);
    setErrorState(null);
    setEditing(false);
    try {
      const response = await getMemoryNode({ domain, path }, { signal: controller.signal });
      if (requestId !== nodeRequestRef.current) return;
      setData(response);
      setEditContent(response.node?.content || '');
      setEditDisclosure(response.node?.disclosure || '');
      setEditPriority(response.node?.priority ?? 0);
      setContentView(response.node?.gist_text ? 'gist' : 'original');
    } catch (err) {
      if (requestId !== nodeRequestRef.current) return;
      if (controller.signal.aborted || isAbortError(err)) return;
      setErrorState({ error: err, fallbackKey: 'memory.errors.loadNode' });
    } finally {
      if (requestId !== nodeRequestRef.current) return;
      setLoading(false);
    }
  }, [domain, path]);

  useEffect(() => {
    refreshNode();
    return () => {
      nodeRequestRef.current += 1;
      nodeAbortControllerRef.current?.abort();
    };
  }, [refreshNode]);

  useEffect(() => {
    if (!conversationDirty) {
      setConversation(defaultConversation);
    }
  }, [conversationDirty, defaultConversation]);

  const hasUnsavedNodeEdit = useMemo(() => {
    if (!editing || isRoot || !data.node) return false;
    return (
      editContent !== (data.node.content || '')
      || editDisclosure !== (data.node.disclosure || '')
      || editPriority !== (data.node.priority ?? 0)
    );
  }, [data.node, editContent, editDisclosure, editPriority, editing, isRoot]);
  const canAutoRefresh = !hasUnsavedNodeEdit && !editing && !creating && !saving && !deleting;
  const domainOptions = useMemo(() => {
    if (BROWSABLE_DOMAINS.includes(domain)) return BROWSABLE_DOMAINS;
    return [domain, ...BROWSABLE_DOMAINS];
  }, [domain]);

  useEffect(() => {
    if (!hasUnsavedNodeEdit) return undefined;
    const handleBeforeUnload = (event) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
    };
  }, [hasUnsavedNodeEdit]);

  useEffect(() => {
    if (!canAutoRefresh) return undefined;

    const handleRefreshSignal = () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      void refreshNode();
    };

    window.addEventListener('focus', handleRefreshSignal);
    document.addEventListener('visibilitychange', handleRefreshSignal);
    return () => {
      window.removeEventListener('focus', handleRefreshSignal);
      document.removeEventListener('visibilitychange', handleRefreshSignal);
    };
  }, [canAutoRefresh, refreshNode]);

  const navigateTo = (nextPath, nextDomain = domain, { force = false } = {}) => {
    const sameTarget = nextDomain === domain && nextPath === path;
    if (
      !force
      && !sameTarget
      && hasUnsavedNodeEdit
      && !window.confirm(t('memory.prompts.discardNodeChanges'))
    ) {
      return false;
    }
    const params = new URLSearchParams();
    params.set('domain', nextDomain);
    if (nextPath) params.set('path', nextPath);
    setSearchParams(params);
    return true;
  };

  const onRefreshNode = async () => {
    if (hasUnsavedNodeEdit && !window.confirm(t('memory.prompts.discardNodeChanges'))) {
      return;
    }
    setFeedback(null);
    await refreshNode();
  };

  const visibleChildren = useMemo(() => {
    return (data.children || []).filter((item) => {
      const text =
        `${item.path} ${item.name || ''} ${item.gist_text || ''} ${item.content_snippet || ''}`.toLowerCase();
      const queryOk = !searchValue.trim() || text.includes(searchValue.trim().toLowerCase());
      const priorityOk =
        !priorityFilter.trim() || (item.priority ?? 999) <= Number(priorityFilter.trim());
      return queryOk && priorityOk;
    });
  }, [data.children, priorityFilter, searchValue]);
  const displayedChildren = useMemo(
    () => visibleChildren.slice(0, visibleChildCount),
    [visibleChildCount, visibleChildren]
  );
  const remainingChildrenCount = Math.max(visibleChildren.length - displayedChildren.length, 0);
  const hasMoreChildren = remainingChildrenCount > 0;

  useEffect(() => {
    setVisibleChildCount(CHILD_PAGE_SIZE);
  }, [domain, path, searchValue, priorityFilter, data.children]);

  const hasNodeGist = Boolean(data.node?.gist_text);
  const gistQualityText =
    data.node?.gist_quality == null
      ? t('common.states.notAvailable')
      : Number(data.node.gist_quality).toFixed(3);
  const sourceHashShort = data.node?.source_hash
    ? `${String(data.node.source_hash).slice(0, 10)}...`
    : t('common.states.notAvailable');

  const onStartEdit = () => {
    if (isRoot || !data.node) return;
    setEditContent(data.node.content || '');
    setEditDisclosure(data.node.disclosure || '');
    setEditPriority(data.node.priority ?? 0);
    setEditing(true);
    setFeedback(null);
  };

  const onCancelEdit = () => {
    setEditing(false);
    setEditContent(data.node?.content || '');
    setEditDisclosure(data.node?.disclosure || '');
    setEditPriority(data.node?.priority ?? 0);
  };

  const onSaveEdit = async () => {
    if (isRoot || !data.node) return;
    setSaving(true);
    setFeedback(null);
    try {
      const payload = {};
      if (editContent !== (data.node.content || '')) payload.content = editContent;
      if ((data.node.priority ?? 0) !== editPriority) payload.priority = editPriority;
      if ((data.node.disclosure || '') !== editDisclosure) payload.disclosure = editDisclosure;
      if (Object.keys(payload).length === 0) {
        setEditing(false);
        return;
      }
      const result = await updateMemoryNode(path, domain, payload);
      if (!result?.updated) {
        setFeedback({
          type: 'error',
          text: t('memory.feedback.updateGuardSkipped'),
        });
        return;
      }
      await refreshNode();
      setEditing(false);
      setFeedback({ type: 'ok', text: t('memory.feedback.memoryUpdated') });
    } catch (err) {
      setFeedback({ type: 'error', text: extractApiError(err, t('memory.errors.updateNode')) });
    } finally {
      setSaving(false);
    }
  };

  const onCreateFromConversation = async () => {
    if (!conversation.trim()) {
      setFeedback({ type: 'error', text: t('memory.feedback.conversationEmpty') });
      return;
    }
    setCreating(true);
    setFeedback(null);
    try {
      const created = await createMemoryNode({
        parent_path: path,
        title: composerTitle.trim() || null,
        content: conversation,
        priority: Number(composerPriority) || 0,
        disclosure: composerDisclosure.trim() || null,
        domain,
      });
      if (!created?.created) {
        setFeedback({
          type: 'error',
          text: t('memory.feedback.createGuardSkipped'),
        });
        return;
      }
      if (!created?.path || !created?.domain) {
        setFeedback({
          type: 'error',
          text: t('memory.feedback.createResponseMissing'),
        });
        return;
      }
      setComposerTitle('');
      setComposerDisclosure('');
      setComposerPriority(0);
      setConversationDirty(false);
      setConversation(defaultConversation);
      setFeedback({ type: 'ok', text: t('memory.feedback.memoryCreated', { uri: created.uri }) });
      navigateTo(created.path, created.domain, { force: true });
    } catch (err) {
      setFeedback({ type: 'error', text: extractApiError(err, t('memory.errors.createNode')) });
    } finally {
      setCreating(false);
    }
  };

  const onDeletePath = async () => {
    if (isRoot) return;
    if (!window.confirm(t('memory.prompts.deletePath', { target: `${domain}://${path}` }))) return;
    setDeleting(true);
    setFeedback(null);
    try {
      await deleteMemoryNode(path, domain);
      const parent = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
      navigateTo(parent, domain, { force: true });
      setFeedback({ type: 'ok', text: t('memory.feedback.pathDeleted') });
    } catch (err) {
      setFeedback({ type: 'error', text: extractApiError(err, t('memory.errors.deleteNode')) });
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden text-[color:var(--palace-ink)]">
      {/* Internal Header */}
      <motion.header
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="shrink-0 px-2 pb-6"
      >
        <div className="flex w-full flex-wrap items-end justify-between gap-4">
          <div>
            <p className="mb-2 inline-flex items-center gap-2 rounded-full border border-[color:var(--palace-line)] bg-white/30 px-3 py-1 text-[10px] font-bold uppercase tracking-[0.14em] text-[color:var(--palace-muted)] backdrop-blur-sm">
              <Compass size={12} />
              {t('memory.consoleBadge')}
            </p>
            <h1 className="font-display text-3xl font-medium text-[color:var(--palace-ink)] drop-shadow-sm">
              {isRoot ? t('memory.rootTitle') : (data.node?.name || path.split('/').pop())}
            </h1>
          </div>
          <div className="flex flex-col items-end gap-3">
            <div className="flex flex-wrap items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  void onRefreshNode();
                }}
                className="palace-btn-ghost bg-white/50"
              >
                <RefreshCw size={14} className={clsx(loading && 'animate-spin')} />
                {t('memory.refreshNode')}
              </button>
              <div className="inline-flex items-center gap-2 rounded-full border border-white/40 bg-white/20 px-4 py-1.5 text-xs font-medium text-[color:var(--palace-muted)] backdrop-blur-md shadow-sm">
                <Sparkles size={13} className="text-[color:var(--palace-accent)]" />
                {domain}://{path || 'root'}
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-[color:var(--palace-muted)]">
                {t('memory.browseScope')}
              </span>
              {domainOptions.map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => navigateTo('', option)}
                  className={clsx(
                    'rounded-full border px-3 py-1 text-xs font-semibold transition',
                    option === domain
                      ? 'border-[color:var(--palace-accent)]/30 bg-[color:var(--palace-accent)]/12 text-[color:var(--palace-accent-2)]'
                      : 'border-white/40 bg-white/30 text-[color:var(--palace-muted)] hover:bg-white/55 hover:text-[color:var(--palace-ink)]'
                  )}
                >
                  {t(`memory.domainLabels.${option}`)}
                </button>
              ))}
              <button
                type="button"
                onClick={() => navigateTo('projects', 'notes')}
                className="rounded-full border border-white/40 bg-white/30 px-3 py-1 text-xs font-semibold text-[color:var(--palace-muted)] transition hover:bg-white/55 hover:text-[color:var(--palace-ink)]"
              >
                {t('memory.projectNotesRoot')}
              </button>
            </div>
          </div>
        </div>
      </motion.header>

      <main className="flex-1 overflow-y-auto px-1 pb-10 scrollbar-none">
        <div className="grid w-full gap-6 lg:grid-cols-[360px_1fr]">
          <motion.aside
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.1 }}
            className="space-y-6"
          >
            <GlassCard className="p-5">
             <h2 className="mb-4 inline-flex items-center gap-2 text-sm font-semibold text-[color:var(--palace-ink)]">
                <BookOpenText size={16} className="text-[color:var(--palace-accent-2)]" />
                {t('memory.conversationVault')}
              </h2>
              <div className="space-y-3">
                <input
                  value={composerTitle}
                  onChange={(e) => setComposerTitle(e.target.value)}
                  placeholder={t('memory.titlePlaceholder')}
                  className="palace-input bg-white/40 focus:bg-white/80"
                />
                <textarea
                  value={conversation}
                  onChange={(e) => {
                    setConversation(e.target.value);
                    setConversationDirty(true);
                  }}
                  placeholder={t('memory.conversationPlaceholder')}
                  className="palace-input h-48 resize-none bg-white/40 focus:bg-white/80 leading-relaxed"
                />
                <div className="grid grid-cols-2 gap-3">
                  <input
                    value={composerPriority}
                    onChange={(e) => setComposerPriority(Number(e.target.value) || 0)}
                    type="number"
                    min="0"
                    className="palace-input bg-white/40 focus:bg-white/80"
                    placeholder={t('memory.priorityPlaceholder')}
                  />
                  <input
                    value={composerDisclosure}
                    onChange={(e) => setComposerDisclosure(e.target.value)}
                    className="palace-input bg-white/40 focus:bg-white/80"
                    placeholder={t('memory.disclosurePlaceholder')}
                  />
                </div>
                <button
                  type="button"
                  onClick={onCreateFromConversation}
                  disabled={creating}
                  data-testid="memory-store-button"
                  className="palace-btn-primary w-full justify-center"
                >
                  {creating ? <Save size={14} className="animate-pulse" /> : <Plus size={14} />}
                  {t('memory.storeMemory')}
                </button>
              </div>
            </GlassCard>

            <GlassCard className="p-5">
              <h3 className="mb-4 inline-flex items-center gap-2 text-sm font-semibold text-[color:var(--palace-ink)]">
                <Filter size={15} className="text-[color:var(--palace-accent-2)]" />
                {t('memory.childFilters')}
              </h3>
              <div className="space-y-3">
                <div className="relative">
                  <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[color:var(--palace-muted)]" />
                  <input
                    value={searchValue}
                    onChange={(e) => setSearchValue(e.target.value)}
                    placeholder={t('memory.searchPlaceholder')}
                    className="palace-input pl-9 bg-white/40 focus:bg-white/80"
                  />
                </div>
                <input
                  value={priorityFilter}
                  onChange={(e) => setPriorityFilter(e.target.value)}
                  type="number"
                  min="0"
                  placeholder={t('memory.maxPriorityPlaceholder')}
                  className="palace-input bg-white/40 focus:bg-white/80"
                />
              </div>
            </GlassCard>
          </motion.aside>

          <motion.section
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.2 }}
            className="space-y-6"
          >
            <div className="flex items-center justify-between">
                <CrumbBar items={data.breadcrumbs || [{ path: '', label: t('memory.rootBreadcrumb') }]} onNavigate={navigateTo} />
            </div>

            <AnimatePresence mode="wait">
                {feedback && (
                <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    className={clsx(
                    'rounded-2xl border px-4 py-3 text-sm backdrop-blur-md shadow-sm',
                    feedback.type === 'ok'
                        ? 'border-emerald-200/50 bg-emerald-50/60 text-emerald-800'
                        : 'border-rose-200/50 bg-rose-50/60 text-rose-700'
                    )}
                >
                    {feedback.text}
                </motion.div>
                )}
            </AnimatePresence>

            {loading ? (
              <GlassCard className="p-12 text-center text-sm text-[color:var(--palace-muted)]">
                <motion.div
                    animate={{ rotate: 360 }}
                    transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
                    className="mx-auto mb-3 h-6 w-6 rounded-full border-2 border-[color:var(--palace-line)] border-t-[color:var(--palace-accent)]"
                />
                {t('memory.loadingNode')}
              </GlassCard>
            ) : error ? (
              <GlassCard className="p-6 border-rose-200/50 bg-rose-50/30 text-rose-700">
                <div className="mb-2 inline-flex items-center gap-2 font-semibold">
                  <AlertTriangle size={15} />
                  {t('memory.loadNodeFailed')}
                </div>
                <p className="opacity-90">{error}</p>
              </GlassCard>
            ) : (
              <>
                <GlassCard className="p-6">
                  <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <p className="mb-1.5 text-[10px] font-bold uppercase tracking-[0.15em] text-[color:var(--palace-muted)]">
                        {t('memory.currentNodeContent')}
                      </p>
                      <div className="flex items-center gap-3">
                         <h2 className="font-display text-2xl font-medium">
                            {isRoot ? t('memory.rootNodeTitle') : data.node?.name}
                         </h2>
                      </div>
                      {!isRoot && hasNodeGist && (
                        <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
                          <div className="inline-flex items-center rounded-full border border-[color:var(--palace-line)] bg-white/50 p-1">
                            <button
                              type="button"
                              onClick={() => setContentView('gist')}
                              className={clsx(
                                'rounded-full px-2.5 py-1 font-semibold transition',
                                contentView === 'gist'
                                  ? 'bg-[color:var(--palace-accent)]/15 text-[color:var(--palace-accent-2)]'
                                  : 'text-[color:var(--palace-muted)] hover:bg-white/70'
                              )}
                            >
                              {t('memory.gistView')}
                            </button>
                            <button
                              type="button"
                              onClick={() => setContentView('original')}
                              className={clsx(
                                'rounded-full px-2.5 py-1 font-semibold transition',
                                contentView === 'original'
                                  ? 'bg-[color:var(--palace-accent)]/15 text-[color:var(--palace-accent-2)]'
                                  : 'text-[color:var(--palace-muted)] hover:bg-white/70'
                              )}
                            >
                              {t('memory.originalView')}
                            </button>
                          </div>
                          <span className="rounded-full border border-[color:var(--palace-line)] bg-white/50 px-2 py-1 text-[color:var(--palace-muted)]">
                            {t('memory.method')}: {data.node?.gist_method || t('common.states.notAvailable')}
                          </span>
                          <span className="rounded-full border border-[color:var(--palace-line)] bg-white/50 px-2 py-1 text-[color:var(--palace-muted)]">
                            {t('memory.quality')}: {gistQualityText}
                          </span>
                          <span className="rounded-full border border-[color:var(--palace-line)] bg-white/50 px-2 py-1 text-[color:var(--palace-muted)]">
                            {t('memory.source')}: {sourceHashShort}
                          </span>
                        </div>
                      )}
                    </div>

                    {!isRoot && (
                      <div className="flex items-center gap-2">
                        {editing ? (
                          <>
                            <button
                              type="button"
                              onClick={onCancelEdit}
                              data-testid="memory-cancel-edit"
                              className="palace-btn-ghost bg-white/50"
                            >
                              <X size={14} />
                              {t('common.actions.cancel')}
                            </button>
                            <button
                              type="button"
                              onClick={onSaveEdit}
                              disabled={saving}
                              data-testid="memory-save-edit"
                              className="palace-btn-primary"
                            >
                              <Save size={14} />
                              {saving ? t('memory.saving') : t('common.actions.save')}
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              type="button"
                              onClick={onStartEdit}
                              data-testid="memory-start-edit"
                              className="palace-btn-ghost bg-white/50"
                            >
                              <Edit3 size={14} />
                              {t('common.actions.edit')}
                            </button>
                            <button
                              type="button"
                              onClick={onDeletePath}
                              disabled={deleting}
                              data-testid="memory-delete-path"
                              className="inline-flex cursor-pointer items-center gap-1 rounded-xl border border-rose-200/50 bg-rose-50/30 px-3 py-2 text-xs font-semibold text-rose-700 transition hover:bg-rose-100/50 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              <Trash2 size={14} />
                              {deleting ? t('memory.deleting') : t('memory.deletePath')}
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </div>

                  {!isRoot && editing && (
                    <motion.div
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: 'auto' }}
                        className="mb-4 grid gap-3 md:grid-cols-2"
                    >
                      <input
                        type="number"
                        min="0"
                        value={editPriority}
                        onChange={(e) => setEditPriority(Number(e.target.value) || 0)}
                        className="palace-input bg-white/60"
                        placeholder={t('memory.priorityPlaceholder')}
                      />
                      <input
                        value={editDisclosure}
                        onChange={(e) => setEditDisclosure(e.target.value)}
                        className="palace-input bg-white/60"
                        placeholder={t('memory.disclosurePlaceholder')}
                      />
                    </motion.div>
                  )}

                  {!isRoot && editing ? (
                    <textarea
                      value={editContent}
                      onChange={(e) => setEditContent(e.target.value)}
                      className="palace-input h-72 resize-y bg-white/60 font-mono text-sm leading-relaxed"
                    />
                  ) : (
                    <div className="rounded-xl border border-[color:var(--palace-glass-border)] bg-white/30 px-5 py-4 shadow-inner">
                        <pre className="max-h-[500px] overflow-auto whitespace-pre-wrap font-sans text-sm leading-7 text-[color:var(--palace-ink)]">
                        {isRoot
                            ? t('memory.rootNodeNoContent')
                            : contentView === 'gist'
                              ? data.node?.gist_text || <span className="text-[color:var(--palace-muted)] italic">{t('memory.gistUnavailable')}</span>
                              : data.node?.content || <span className="text-[color:var(--palace-muted)] italic">{t('memory.emptyContent')}</span>}
                        </pre>
                    </div>
                  )}
                </GlassCard>

                <GlassCard className="p-6">
                  <div className="mb-4 flex items-center justify-between">
                    <h3 className="text-sm font-semibold">{t('memory.childMemories')}</h3>
                    <span className="rounded-full border border-[color:var(--palace-line)] bg-white/50 px-2 py-0.5 text-xs text-[color:var(--palace-muted)]">
                      {visibleChildren.length} / {data.children?.length || 0}
                    </span>
                  </div>
                  {visibleChildren.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-[color:var(--palace-line)] bg-[color:var(--palace-soft)]/50 px-4 py-12 text-center text-sm text-[color:var(--palace-muted)]">
                      <p>{t('memory.noChildMatches')}</p>
                    </div>
                  ) : (
                    <div className="space-y-4">
                      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                        {displayedChildren.map((child) => (
                          <ChildCard
                            key={`${child.domain}:${child.path}`}
                            child={child}
                            onOpen={() => navigateTo(child.path, child.domain)}
                          />
                        ))}
                      </div>
                      {hasMoreChildren ? (
                        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-[color:var(--palace-line)] bg-white/20 px-4 py-4 text-center">
                          <p className="text-xs text-[color:var(--palace-muted)]">
                            {t('memory.showingChildren', {
                              shown: displayedChildren.length,
                              total: visibleChildren.length,
                            })}
                          </p>
                          <button
                            type="button"
                            onClick={() => setVisibleChildCount((current) => current + CHILD_PAGE_SIZE)}
                            className="palace-btn-ghost bg-white/50"
                          >
                            {t('memory.loadMoreChildren', {
                              count: Math.min(CHILD_PAGE_SIZE, remainingChildrenCount),
                            })}
                          </button>
                        </div>
                      ) : null}
                    </div>
                  )}
                </GlassCard>
              </>
            )}
          </motion.section>
        </div>
      </main>
    </div>
  );
}
