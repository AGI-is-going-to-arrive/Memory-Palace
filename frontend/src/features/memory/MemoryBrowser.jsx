import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import clsx from 'clsx';
import { motion, AnimatePresence } from 'framer-motion';
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

const defaultConversation = [
  'LLM: 我今天需要记住“发布流程的回滚检查清单”。',
  'Agent: 已记录。请补充触发条件与责任人。',
  'LLM: 触发条件是部署后 10 分钟内关键接口错误率 > 5%。',
].join('\n');

const isAbortError = (error) =>
  Boolean(
    error &&
      (error.code === 'ERR_CANCELED' ||
        error.name === 'AbortError' ||
        error.name === 'CanceledError')
  );

function CrumbBar({ items, onNavigate }) {
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
            {item.label || 'root'}
          </button>
        </React.Fragment>
      ))}
    </div>
  );
}

function ChildCard({ child, onOpen }) {
  const preview = child.gist_text || child.content_snippet || 'No preview';
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
  const [searchParams, setSearchParams] = useSearchParams();
  const domain = searchParams.get('domain') || 'core';
  const path = searchParams.get('path') || '';

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
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
  const [conversation, setConversation] = useState(defaultConversation);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const nodeRequestRef = useRef(0);
  const nodeAbortControllerRef = useRef(null);

  const isRoot = !path;

  const refreshNode = useCallback(async () => {
    const requestId = ++nodeRequestRef.current;
    nodeAbortControllerRef.current?.abort();
    const controller = new AbortController();
    nodeAbortControllerRef.current = controller;
    setLoading(true);
    setError(null);
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
      setError(extractApiError(err, 'Failed to load memory node'));
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

  const navigateTo = (nextPath, nextDomain = domain) => {
    const params = new URLSearchParams();
    params.set('domain', nextDomain);
    if (nextPath) params.set('path', nextPath);
    setSearchParams(params);
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

  const hasNodeGist = Boolean(data.node?.gist_text);
  const gistQualityText =
    data.node?.gist_quality == null ? 'n/a' : Number(data.node.gist_quality).toFixed(3);
  const sourceHashShort = data.node?.source_hash
    ? `${String(data.node.source_hash).slice(0, 10)}...`
    : 'n/a';

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
          text: result?.message || 'Skipped: write_guard blocked update_node.',
        });
        return;
      }
      await refreshNode();
      setEditing(false);
      setFeedback({ type: 'ok', text: 'Memory updated.' });
    } catch (err) {
      setFeedback({ type: 'error', text: extractApiError(err, 'Failed to update memory node') });
    } finally {
      setSaving(false);
    }
  };

  const onCreateFromConversation = async () => {
    if (!conversation.trim()) {
      setFeedback({ type: 'error', text: 'Conversation content cannot be empty.' });
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
          text: created?.message || 'Skipped: write_guard blocked create_node.',
        });
        return;
      }
      if (!created?.path || !created?.domain) {
        setFeedback({
          type: 'error',
          text: 'Create response missing destination path.',
        });
        return;
      }
      setComposerTitle('');
      setComposerDisclosure('');
      setComposerPriority(0);
      setConversation(defaultConversation);
      setFeedback({ type: 'ok', text: `Memory created: ${created.uri}` });
      navigateTo(created.path, created.domain);
    } catch (err) {
      setFeedback({ type: 'error', text: extractApiError(err, 'Failed to create memory node') });
    } finally {
      setCreating(false);
    }
  };

  const onDeletePath = async () => {
    if (isRoot) return;
    if (!window.confirm(`Delete path ${domain}://${path} ?`)) return;
    setDeleting(true);
    setFeedback(null);
    try {
      await deleteMemoryNode(path, domain);
      const parent = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
      navigateTo(parent, domain);
      setFeedback({ type: 'ok', text: 'Path deleted.' });
    } catch (err) {
      setFeedback({ type: 'error', text: extractApiError(err, 'Failed to delete memory node') });
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
              Memory Console
            </p>
            <h1 className="font-display text-3xl font-medium text-[color:var(--palace-ink)] drop-shadow-sm">
              {isRoot ? 'Memory Hall' : (data.node?.name || path.split('/').pop())}
            </h1>
          </div>
          <div className="flex items-center gap-3">
             <div className="inline-flex items-center gap-2 rounded-full border border-white/40 bg-white/20 px-4 py-1.5 text-xs font-medium text-[color:var(--palace-muted)] backdrop-blur-md shadow-sm">
              <Sparkles size={13} className="text-[color:var(--palace-accent)]" />
              {domain}://{path || 'root'}
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
                Conversation Vault
              </h2>
              <div className="space-y-3">
                <input
                  value={composerTitle}
                  onChange={(e) => setComposerTitle(e.target.value)}
                  placeholder="Memory title (optional)"
                  className="palace-input bg-white/40 focus:bg-white/80"
                />
                <textarea
                  value={conversation}
                  onChange={(e) => setConversation(e.target.value)}
                  placeholder="Paste LLM / agent dialogue..."
                  className="palace-input h-48 resize-none bg-white/40 focus:bg-white/80 leading-relaxed"
                />
                <div className="grid grid-cols-2 gap-3">
                  <input
                    value={composerPriority}
                    onChange={(e) => setComposerPriority(Number(e.target.value) || 0)}
                    type="number"
                    min="0"
                    className="palace-input bg-white/40 focus:bg-white/80"
                    placeholder="Priority"
                  />
                  <input
                    value={composerDisclosure}
                    onChange={(e) => setComposerDisclosure(e.target.value)}
                    className="palace-input bg-white/40 focus:bg-white/80"
                    placeholder="Disclosure"
                  />
                </div>
                <button
                  type="button"
                  onClick={onCreateFromConversation}
                  disabled={creating}
                  className="palace-btn-primary w-full justify-center"
                >
                  {creating ? <Save size={14} className="animate-pulse" /> : <Plus size={14} />}
                  Store Memory
                </button>
              </div>
            </GlassCard>

            <GlassCard className="p-5">
              <h3 className="mb-4 inline-flex items-center gap-2 text-sm font-semibold text-[color:var(--palace-ink)]">
                <Filter size={15} className="text-[color:var(--palace-accent-2)]" />
                Child Filters
              </h3>
              <div className="space-y-3">
                <div className="relative">
                  <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[color:var(--palace-muted)]" />
                  <input
                    value={searchValue}
                    onChange={(e) => setSearchValue(e.target.value)}
                    placeholder="Search path / snippet"
                    className="palace-input pl-9 bg-white/40 focus:bg-white/80"
                  />
                </div>
                <input
                  value={priorityFilter}
                  onChange={(e) => setPriorityFilter(e.target.value)}
                  type="number"
                  min="0"
                  placeholder="Max priority (optional)"
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
                <CrumbBar items={data.breadcrumbs || [{ path: '', label: 'root' }]} onNavigate={navigateTo} />
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
                Loading memory node...
              </GlassCard>
            ) : error ? (
              <GlassCard className="p-6 border-rose-200/50 bg-rose-50/30 text-rose-700">
                <div className="mb-2 inline-flex items-center gap-2 font-semibold">
                  <AlertTriangle size={15} />
                  Failed to load node
                </div>
                <p className="opacity-90">{error}</p>
              </GlassCard>
            ) : (
              <>
                <GlassCard className="p-6">
                  <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <p className="mb-1.5 text-[10px] font-bold uppercase tracking-[0.15em] text-[color:var(--palace-muted)]">
                        Current Node Content
                      </p>
                      <div className="flex items-center gap-3">
                         <h2 className="font-display text-2xl font-medium">
                            {isRoot ? 'Root Memory Hall' : data.node?.name}
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
                              Gist
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
                              Original
                            </button>
                          </div>
                          <span className="rounded-full border border-[color:var(--palace-line)] bg-white/50 px-2 py-1 text-[color:var(--palace-muted)]">
                            method: {data.node?.gist_method || 'n/a'}
                          </span>
                          <span className="rounded-full border border-[color:var(--palace-line)] bg-white/50 px-2 py-1 text-[color:var(--palace-muted)]">
                            quality: {gistQualityText}
                          </span>
                          <span className="rounded-full border border-[color:var(--palace-line)] bg-white/50 px-2 py-1 text-[color:var(--palace-muted)]">
                            source: {sourceHashShort}
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
                              className="palace-btn-ghost bg-white/50"
                            >
                              <X size={14} />
                              Cancel
                            </button>
                            <button
                              type="button"
                              onClick={onSaveEdit}
                              disabled={saving}
                              className="palace-btn-primary"
                            >
                              <Save size={14} />
                              {saving ? 'Saving...' : 'Save'}
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              type="button"
                              onClick={onStartEdit}
                              className="palace-btn-ghost bg-white/50"
                            >
                              <Edit3 size={14} />
                              Edit
                            </button>
                            <button
                              type="button"
                              onClick={onDeletePath}
                              disabled={deleting}
                              className="inline-flex cursor-pointer items-center gap-1 rounded-xl border border-rose-200/50 bg-rose-50/30 px-3 py-2 text-xs font-semibold text-rose-700 transition hover:bg-rose-100/50 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              <Trash2 size={14} />
                              {deleting ? 'Deleting...' : 'Delete Path'}
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
                        placeholder="Priority"
                      />
                      <input
                        value={editDisclosure}
                        onChange={(e) => setEditDisclosure(e.target.value)}
                        className="palace-input bg-white/60"
                        placeholder="Disclosure"
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
                            ? 'Root node does not store content. Create child memories from the Conversation Vault.'
                            : contentView === 'gist'
                              ? data.node?.gist_text || <span className="text-[color:var(--palace-muted)] italic">(gist unavailable)</span>
                              : data.node?.content || <span className="text-[color:var(--palace-muted)] italic">(empty content)</span>}
                        </pre>
                    </div>
                  )}
                </GlassCard>

                <GlassCard className="p-6">
                  <div className="mb-4 flex items-center justify-between">
                    <h3 className="text-sm font-semibold">Child Memories</h3>
                    <span className="rounded-full border border-[color:var(--palace-line)] bg-white/50 px-2 py-0.5 text-xs text-[color:var(--palace-muted)]">
                      {visibleChildren.length} / {data.children?.length || 0}
                    </span>
                  </div>
                  {visibleChildren.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-[color:var(--palace-line)] bg-[color:var(--palace-soft)]/50 px-4 py-12 text-center text-sm text-[color:var(--palace-muted)]">
                      <p>No child memory matches current filter.</p>
                    </div>
                  ) : (
                    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                      {visibleChildren.map((child) => (
                        <ChildCard
                          key={`${child.domain}:${child.path}`}
                          child={child}
                          onOpen={() => navigateTo(child.path, child.domain)}
                        />
                      ))}
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
