import React, { useCallback, useEffect, useState } from 'react';
import { Crosshair, MessageSquarePlus, Send, X } from 'lucide-react';
import { useLocation } from 'react-router-dom';

const AGENTATION_HTTP = 'http://127.0.0.1:4747';
const SESSION_KEY = 'agentation_session_memory_palace';

function buildElementPath(element) {
  if (!element || element.nodeType !== 1) return 'body';
  const parts = [];
  let current = element;

  while (current && current.nodeType === 1 && current.tagName.toLowerCase() !== 'html') {
    const tag = current.tagName.toLowerCase();
    const id = current.id ? `#${current.id}` : '';
    const className = typeof current.className === 'string'
      ? current.className.trim().split(/\s+/).slice(0, 2).map((name) => `.${name}`).join('')
      : '';
    parts.unshift(`${tag}${id}${className}`);
    current = current.parentElement;
  }

  return parts.join(' > ') || 'body';
}

export default function AgentationLite() {
  const location = useLocation();
  const [sessionId, setSessionId] = useState(null);
  const [status, setStatus] = useState('connecting');
  const [captureMode, setCaptureMode] = useState(false);
  const [composerOpen, setComposerOpen] = useState(false);
  const [comment, setComment] = useState('');
  const [intent, setIntent] = useState('change');
  const [severity, setSeverity] = useState('important');
  const [target, setTarget] = useState(null);
  const [feedback, setFeedback] = useState('');

  const ensureSession = useCallback(async () => {
    setStatus('connecting');
    setFeedback('');
    try {
      let session = localStorage.getItem(SESSION_KEY);
      if (session) {
        const probe = await fetch(`${AGENTATION_HTTP}/sessions/${session}`);
        if (!probe.ok) {
          session = null;
        }
      }

      if (!session) {
        const response = await fetch(`${AGENTATION_HTTP}/sessions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url: window.location.href,
            projectId: 'memory-palace',
          }),
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const created = await response.json();
        session = created.id;
        localStorage.setItem(SESSION_KEY, session);
      }
      setSessionId(session);
      setStatus('ready');
    } catch (error) {
      setStatus('offline');
      setFeedback(`Agentation offline: ${error.message}`);
    }
  }, []);

  useEffect(() => {
    ensureSession();
  }, [ensureSession, location.pathname, location.search]);

  useEffect(() => {
    if (!captureMode) {
      document.body.style.cursor = '';
      return;
    }

    document.body.style.cursor = 'crosshair';
    const onCapture = (event) => {
      const targetElement = event.target;
      if (targetElement.closest('[data-agentation-lite="toolbar"]')) {
        return;
      }

      event.preventDefault();
      event.stopPropagation();

      const text = typeof targetElement.innerText === 'string'
        ? targetElement.innerText.trim().slice(0, 280)
        : '';

      setTarget({
        x: Math.round(event.clientX),
        y: Math.round(event.clientY),
        element: targetElement.tagName.toLowerCase(),
        elementPath: buildElementPath(targetElement),
        selectedText: text,
        url: window.location.href,
      });
      setCaptureMode(false);
      setComposerOpen(true);
      setFeedback('');
    };

    window.addEventListener('click', onCapture, true);
    return () => {
      document.body.style.cursor = '';
      window.removeEventListener('click', onCapture, true);
    };
  }, [captureMode]);

  const submitAnnotation = async () => {
    if (!sessionId || !target || !comment.trim()) return;

    setStatus('sending');
    try {
      const payload = {
        ...target,
        timestamp: Date.now(),
        comment: comment.trim(),
        intent,
        severity,
      };

      const response = await fetch(`${AGENTATION_HTTP}/sessions/${sessionId}/annotations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        if (response.status === 404) {
          localStorage.removeItem(SESSION_KEY);
        }
        throw new Error(`HTTP ${response.status}`);
      }

      setComment('');
      setComposerOpen(false);
      setTarget(null);
      setStatus('ready');
      setFeedback('Annotation sent to Agentation.');
    } catch (error) {
      setStatus('offline');
      setFeedback(`Submit failed: ${error.message}`);
    }
  };

  const statusLabel = {
    connecting: 'Connecting',
    ready: 'Ready',
    sending: 'Sending',
    offline: 'Offline',
  }[status];

  return (
    <div data-agentation-lite="toolbar" className="fixed bottom-5 right-5 z-50 w-80 space-y-2">
      <div className="rounded-xl border border-stone-700/80 bg-stone-900/95 p-3 shadow-[0_14px_36px_rgba(0,0,0,0.45)] backdrop-blur">
        <div className="mb-2 flex items-center justify-between">
          <div className="font-display text-sm text-amber-50">Agentation</div>
          <span
            className={`rounded border px-2 py-0.5 text-[10px] uppercase tracking-[0.14em] ${
              status === 'ready'
                ? 'border-emerald-700/50 bg-emerald-950/25 text-emerald-300'
                : status === 'offline'
                  ? 'border-rose-700/50 bg-rose-950/25 text-rose-300'
                  : 'border-amber-700/50 bg-amber-950/25 text-amber-300'
            }`}
          >
            {statusLabel}
          </span>
        </div>

        <div className="mb-3 text-xs text-stone-400">
          Session: <code className="text-amber-200">{sessionId ? sessionId.slice(0, 14) : '-'}</code>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setCaptureMode((prev) => !prev)}
            disabled={status !== 'ready'}
            className={`flex-1 rounded-lg border px-3 py-2 text-xs font-medium transition ${
              captureMode
                ? 'border-amber-500/60 bg-amber-950/35 text-amber-100'
                : 'border-stone-700 bg-stone-800/70 text-stone-200 hover:border-amber-700/50 hover:text-amber-100'
            } disabled:cursor-not-allowed disabled:opacity-50`}
          >
            <span className="inline-flex items-center gap-1.5">
              <Crosshair size={13} />
              {captureMode ? 'Click target...' : 'Capture Element'}
            </span>
          </button>

          <button
            type="button"
            onClick={() => setComposerOpen((prev) => !prev)}
            className="rounded-lg border border-stone-700 bg-stone-800/70 p-2 text-stone-200 transition hover:border-amber-700/50 hover:text-amber-100"
          >
            {composerOpen ? <X size={14} /> : <MessageSquarePlus size={14} />}
          </button>
        </div>

        {feedback && <p className="mt-2 text-[11px] text-stone-400">{feedback}</p>}
      </div>

      {composerOpen && (
        <div className="rounded-xl border border-stone-700/80 bg-stone-900/95 p-3 shadow-[0_14px_36px_rgba(0,0,0,0.45)] backdrop-blur">
          <div className="mb-2 text-[11px] uppercase tracking-[0.14em] text-amber-400">New Annotation</div>
          <div className="mb-2 text-xs text-stone-400">
            Target: <code className="text-amber-200">{target?.elementPath || 'Capture an element first'}</code>
          </div>

          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="Describe the UI issue or change request..."
            className="mb-2 h-24 w-full resize-none rounded-lg border border-amber-900/40 bg-stone-950/80 px-3 py-2 text-sm text-stone-100 placeholder:text-stone-500 focus:outline-none focus:ring-2 focus:ring-amber-500/60"
          />

          <div className="mb-2 grid grid-cols-2 gap-2">
            <select
              value={intent}
              onChange={(event) => setIntent(event.target.value)}
              className="rounded-lg border border-amber-900/40 bg-stone-950/80 px-2 py-1.5 text-xs text-stone-200 focus:outline-none focus:ring-2 focus:ring-amber-500/60"
            >
              <option value="fix">fix</option>
              <option value="change">change</option>
              <option value="question">question</option>
              <option value="approve">approve</option>
            </select>
            <select
              value={severity}
              onChange={(event) => setSeverity(event.target.value)}
              className="rounded-lg border border-amber-900/40 bg-stone-950/80 px-2 py-1.5 text-xs text-stone-200 focus:outline-none focus:ring-2 focus:ring-amber-500/60"
            >
              <option value="blocking">blocking</option>
              <option value="important">important</option>
              <option value="suggestion">suggestion</option>
            </select>
          </div>

          <button
            type="button"
            onClick={submitAnnotation}
            disabled={status !== 'ready' || !target || !comment.trim()}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-amber-700/60 bg-amber-950/30 px-3 py-2 text-sm font-semibold text-amber-100 transition hover:bg-amber-900/40 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Send size={14} />
            Send to Agentation
          </button>
        </div>
      )}
    </div>
  );
}
