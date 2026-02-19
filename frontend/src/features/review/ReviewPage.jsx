import React, { useEffect, useState, useRef } from 'react';
import { format } from 'date-fns';
import {
  approveSnapshot,
  clearSession,
  extractApiError,
  getDiff,
  getSessions,
  getSnapshots,
  rollbackResource,
} from '../../lib/api';
import SnapshotList from '../../components/SnapshotList';
import { SimpleDiff } from '../../components/DiffViewer'; // Uses the Memory Palace styled diff
import { 
  Activity, 
  Check, 
  ChevronRight, 
  Clock, 
  Database, 
  FileText,
  Layout, 
  Link2,
  RefreshCw, 
  RotateCcw, 
  Settings2,
  ShieldCheck, 
  Trash2
} from 'lucide-react';
import clsx from 'clsx';

function ReviewPage() {
  const [sessions, setSessions] = useState([]);
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [snapshots, setSnapshots] = useState([]);
  const [selectedSnapshot, setSelectedSnapshot] = useState(null);
  const [diffData, setDiffData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [diffError, setDiffError] = useState(null);
  const [mutationInFlight, setMutationInFlight] = useState(false);
  
  const diffRequestRef = useRef(0);
  const snapshotsRequestRef = useRef(0);
  const mutationInFlightRef = useRef(false);

  const beginMutation = () => {
    if (mutationInFlightRef.current) return false;
    mutationInFlightRef.current = true;
    setMutationInFlight(true);
    return true;
  };

  const endMutation = () => {
    mutationInFlightRef.current = false;
    setMutationInFlight(false);
  };

  // --- Data Loading Logic (Keep existing logic, refine UI) ---
  useEffect(() => { loadSessions(); }, []);

  const loadSessions = async () => {
    try {
      const list = await getSessions();
      setSessions(list);
      // Logic to auto-select or maintain selection
      if (currentSessionId && !list.find(s => s.session_id === currentSessionId)) {
        setCurrentSessionId(list.length > 0 ? list[0].session_id : null);
        setSelectedSnapshot(null);
      } else if (list.length > 0 && !currentSessionId) {
        setSelectedSnapshot(null);
        setCurrentSessionId(list[0].session_id);
      }
    } catch (err) {
      setDiffError("Disconnected from Neural Core (Backend offline).");
    }
  };

  useEffect(() => {
    if (currentSessionId) {
      setSelectedSnapshot(null);
      loadSnapshots(currentSessionId);
    }
  }, [currentSessionId]);

  const loadSnapshots = async (sessionId) => {
    const requestId = ++snapshotsRequestRef.current;
    setLoading(true);
    try {
      const list = await getSnapshots(sessionId);
      if (requestId !== snapshotsRequestRef.current) return;
      setSnapshots(list);
      if (list.length > 0) setSelectedSnapshot(list[0]);
      else {
        setSelectedSnapshot(null);
        setDiffData(null);
      }
    } catch (err) {
      if (requestId !== snapshotsRequestRef.current) return;
      if (err.response?.status === 404) {
        setSnapshots([]);
        setSelectedSnapshot(null);
        setDiffData(null);
        return;
      }
      setSnapshots([]);
      setSelectedSnapshot(null);
      setDiffData(null);
      setDiffError(extractApiError(err, 'Failed to load snapshots.'));
    } finally {
      if (requestId !== snapshotsRequestRef.current) return;
      setLoading(false);
    }
  };

  useEffect(() => {
    if (currentSessionId && selectedSnapshot) {
      loadDiff(currentSessionId, selectedSnapshot.resource_id);
    }
  }, [currentSessionId, selectedSnapshot]);

  const loadDiff = async (sessionId, resourceId) => {
    const requestId = ++diffRequestRef.current;
    setDiffError(null);
    setDiffData(null);
    try {
      const data = await getDiff(sessionId, resourceId);
      if (requestId === diffRequestRef.current) setDiffData(data);
    } catch (err) {
      if (requestId === diffRequestRef.current) {
        setDiffError(err.response?.data?.detail || "Failed to retrieve memory fragment.");
        setDiffData(null);
      }
    }
  };

  // --- Handlers ---
  const handleRollback = async () => {
    if (!currentSessionId || !selectedSnapshot) return;
    if (!beginMutation()) return;
    if (!confirm(`Reject changes to ${selectedSnapshot.resource_id}? This will revert the memory.`)) {
      endMutation();
      return;
    }
    try {
      const rollbackResult = await rollbackResource(currentSessionId, selectedSnapshot.resource_id);
      if (!rollbackResult?.success) {
        throw new Error(rollbackResult?.message || 'Rollback failed.');
      }
      // Approving after rollback to clear it from the queue, logically "Processing" the rejection
      await approveSnapshot(currentSessionId, selectedSnapshot.resource_id); 
      await loadSnapshots(currentSessionId);
      await loadSessions();
    } catch (err) {
      alert("Rejection failed: " + extractApiError(err, err?.message || 'Rollback request failed.'));
    } finally {
      endMutation();
    }
  };

  const handleApprove = async () => {
    if (!currentSessionId || !selectedSnapshot) return;
    if (!beginMutation()) return;
    try {
      await approveSnapshot(currentSessionId, selectedSnapshot.resource_id);
      await loadSnapshots(currentSessionId);
      await loadSessions();
    } catch (err) {
      alert("Integration failed: " + extractApiError(err, err?.message || 'Approve request failed.'));
    } finally {
      endMutation();
    }
  };
  
  const handleClearSession = async () => {
    if (!currentSessionId) return;
    if (!beginMutation()) return;
    if (!confirm("Integrate ALL pending memories from this session?")) {
      endMutation();
      return;
    }
    try {
      await clearSession(currentSessionId);
      await loadSessions();
    } catch (err) {
      alert("Mass integration failed: " + extractApiError(err, err?.message || 'Clear session request failed.'));
    } finally {
      endMutation();
    }
  }

  // --- Render Helpers ---
  
  // Surviving Paths Renderer (for DELETE operations)
  const renderSurvivingPaths = () => {
    if (!selectedSnapshot || selectedSnapshot.operation_type !== 'delete') return null;
    if (!diffData?.current_data) return null;
    
    const survivingPaths = diffData.current_data.surviving_paths;
    if (survivingPaths === undefined) return null;  // Data not loaded yet
    
    const isFullDeletion = survivingPaths.length === 0;

    return (
      <div className={clsx(
        "mb-8 p-4 rounded-lg border backdrop-blur-sm",
        isFullDeletion 
          ? "bg-rose-950/20 border-rose-800/40" 
          : "bg-stone-900/40 border-stone-800/60"
      )}>
        <h3 className="text-xs font-bold uppercase mb-3 flex items-center gap-2 tracking-widest">
          {isFullDeletion ? (
            <>
              <Trash2 size={12} className="text-rose-500" />
              <span className="text-rose-400">Memory Fully Orphaned</span>
            </>
          ) : (
            <>
              <Link2 size={12} className="text-stone-500" />
              <span className="text-stone-500">Surviving Paths</span>
            </>
          )}
        </h3>
        
        {isFullDeletion ? (
          <p className="text-xs text-rose-300/70">
            No other paths point to this memory. This deletion removes all access to the content.
          </p>
        ) : (
          <div className="space-y-1.5">
            <p className="text-xs text-stone-500 mb-2">
              This memory is still reachable via {survivingPaths.length} other path{survivingPaths.length > 1 ? 's' : ''}:
            </p>
            {survivingPaths.map((path, idx) => (
              <div key={idx} className="flex items-center gap-2 text-xs font-mono text-emerald-400/80 bg-emerald-950/20 rounded px-2.5 py-1.5 border border-emerald-900/30">
                <Link2 size={10} className="text-emerald-600 flex-shrink-0" />
                <span className="truncate">{path}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  // Custom Metadata Renderer
  const renderMetadataChanges = () => {
    if (!diffData?.snapshot_data || !diffData?.current_data) return null;
    const metaKeys = ['priority', 'disclosure'];
    const changes = metaKeys.filter(key => {
      const oldVal = diffData.snapshot_data[key];
      const newVal = diffData.current_data[key];
      return JSON.stringify(oldVal) !== JSON.stringify(newVal);
    });

    if (changes.length === 0) return null;

    return (
      <div className="mb-8 p-4 bg-stone-900/40 border border-stone-800/60 rounded-lg backdrop-blur-sm">
        <h3 className="text-xs font-bold text-stone-500 uppercase mb-4 flex items-center gap-2 tracking-widest">
          <Activity size={12} /> Metadata Shifts
        </h3>
        <div className="space-y-3">
          {changes.map(key => {
            const oldVal = diffData.snapshot_data[key];
            const newVal = diffData.current_data[key];
            return (
              <div key={key} className="grid grid-cols-[100px_1fr_20px_1fr] gap-4 text-sm items-start">
                <span className="text-stone-400 font-medium capitalize text-xs pt-0.5">{key}</span>
                <div className="text-rose-400/70 line-through text-xs font-mono text-right break-words">
                  {oldVal != null ? String(oldVal) : '∅'}
                </div>
                <div className="text-center text-stone-700 pt-0.5">→</div>
                <div className="text-emerald-400 text-xs font-mono font-bold break-words">
                  {newVal != null ? String(newVal) : '∅'}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  return (
    <div className="palace-harmonized relative flex h-full overflow-hidden bg-[color:var(--palace-bg)] text-[color:var(--palace-ink)] font-sans selection:bg-[rgba(179,133,79,0.28)] selection:text-[color:var(--palace-ink)]">
      
      {/* Sidebar: The Void */}
      <div className="w-72 flex-shrink-0 flex flex-col border-r border-[color:var(--palace-line)] bg-[rgba(255,250,244,0.9)] backdrop-blur-sm">
        {/* Header */}
        <div className="border-b border-[color:var(--palace-line)]/90 bg-[linear-gradient(180deg,rgba(255,252,247,0.88),rgba(244,235,223,0.68))] p-5">
          <div className="flex items-center gap-3 text-stone-100 mb-6">
            <div className="w-8 h-8 rounded bg-gradient-to-br from-amber-500 to-amber-600 flex items-center justify-center shadow-lg shadow-amber-900/20">
              <ShieldCheck className="w-4 h-4 text-white" />
            </div>
            <span className="font-display text-sm tracking-wide text-amber-50">Review Ledger</span>
          </div>
          
          <div className="relative group">
            <label
              htmlFor="review-session-select"
              className="text-[10px] text-stone-600 uppercase font-bold mb-1.5 block tracking-widest pl-1"
            >
              Target Session
            </label>
            <div className="relative">
              <select 
                id="review-session-select"
                name="review_session_id"
                className="w-full cursor-pointer appearance-none rounded-md border border-[color:var(--palace-line)] bg-white/90 px-3 py-2 text-xs text-[color:var(--palace-ink)] outline-none transition-all hover:border-[color:var(--palace-accent)] focus:border-[color:var(--palace-accent)] focus:ring-1 focus:ring-[color:var(--palace-accent)]/40"
                value={currentSessionId || ''}
                onChange={(e) => {
                  setSelectedSnapshot(null);
                  setCurrentSessionId(e.target.value);
                }}
              >
                {sessions.length === 0 && <option>No active sessions</option>}
                {sessions.map(s => (
                  <option key={s.session_id} value={s.session_id}>
                    {s.session_id}
                  </option>
                ))}
              </select>
              <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none text-stone-600">
                <ChevronRight size={12} className="rotate-90" />
              </div>
            </div>
          </div>
        </div>

        {/* Snapshot List */}
        <div className="flex-1 overflow-y-auto py-2">
            {loading ? (
                <div className="p-8 flex justify-center">
                    <div className="w-6 h-6 border-2 border-amber-500/30 border-t-amber-500 rounded-full animate-spin"></div>
                </div>
            ) : (
                <SnapshotList 
                    snapshots={snapshots} 
                    selectedId={selectedSnapshot?.resource_id} 
                    onSelect={setSelectedSnapshot} 
                />
            )}
        </div>

        {/* Footer */}
        {snapshots.length > 0 && (
             <div className="border-t border-[color:var(--palace-line)] bg-[rgba(255,250,244,0.86)] p-4 backdrop-blur-sm">
                 <button 
                    onClick={handleClearSession}
                    disabled={mutationInFlight}
                    className="group flex w-full items-center justify-center gap-2 rounded-md border border-[color:var(--palace-line)] bg-white/90 py-2.5 text-xs font-medium text-[color:var(--palace-muted)] transition-all duration-300 hover:border-[color:var(--palace-accent)] hover:bg-[rgba(237,226,211,0.72)] hover:text-[color:var(--palace-ink)]"
                 >
                     <Check size={14} className="group-hover:scale-110 transition-transform" /> 
                     <span>Integrate All</span>
                 </button>
             </div>
        )}
      </div>

      {/* Main Stage */}
      <div className="relative flex min-w-0 flex-1 flex-col bg-[rgba(255,250,244,0.7)]">
        {/* Background Ambient Gradient */}
        <div className="pointer-events-none absolute left-0 right-0 top-0 h-96 bg-[radial-gradient(circle_at_top_left,rgba(198,165,126,0.2),rgba(246,242,234,0.08)_52%,transparent_72%)]" />

        {selectedSnapshot ? (
          <>
            {/* Context Header */}
            <div className="relative z-10 flex h-20 items-center justify-between border-b border-[color:var(--palace-line)] bg-white/62 px-8 backdrop-blur-sm">
              <div className="flex items-center gap-4 min-w-0">
                 <div className={clsx(
                    "w-10 h-10 rounded-full flex items-center justify-center border",
                    {
                      'create':         "bg-amber-950/10 border-amber-500/20 text-amber-400 shadow-[0_0_14px_rgba(179,133,79,0.16)]",
                      'create_alias':   "bg-amber-950/10 border-amber-500/20 text-amber-400 shadow-[0_0_14px_rgba(179,133,79,0.16)]",
                      'delete':         "bg-amber-950/10 border-amber-500/20 text-amber-400 shadow-[0_0_14px_rgba(163,124,82,0.14)]",
                      'modify_meta':    "bg-amber-950/10 border-amber-500/20 text-amber-400 shadow-[0_0_14px_rgba(163,124,82,0.14)]",
                      'modify_content': "bg-amber-950/10 border-amber-500/20 text-amber-400 shadow-[0_0_14px_rgba(163,124,82,0.14)]",
                      'modify':         "bg-amber-950/10 border-amber-500/20 text-amber-400 shadow-[0_0_14px_rgba(163,124,82,0.14)]",
                    }[selectedSnapshot.operation_type] || "bg-amber-950/10 border-amber-500/20 text-amber-400 shadow-[0_0_14px_rgba(163,124,82,0.14)]"
                 )}>
                    {{
                      'create':         <Database size={18} />,
                      'create_alias':   <Link2 size={18} />,
                      'delete':         <Trash2 size={18} />,
                      'modify_meta':    <Settings2 size={18} />,
                      'modify_content': <FileText size={18} />,
                      'modify':         <RefreshCw size={18} />,
                    }[selectedSnapshot.operation_type] || <RefreshCw size={18} />}
                 </div>
                 <div className="min-w-0 flex flex-col">
                    <h2 className="font-display text-lg tracking-tight text-amber-50 truncate">
                        {selectedSnapshot.uri || selectedSnapshot.resource_id}
                    </h2>
                    <div className="flex items-center gap-2 text-xs text-stone-500">
                        <span className="bg-stone-800/50 px-1.5 py-0.5 rounded text-stone-400">{selectedSnapshot.resource_type}</span>
                        <span>•</span>
                        <span className="flex items-center gap-1 font-mono opacity-70">
                            <Clock size={10} />
                            {format(new Date(selectedSnapshot.snapshot_time), 'HH:mm:ss')}
                        </span>
                    </div>
                 </div>
              </div>
              
              <div className="flex items-center gap-3">
                <button 
                    onClick={handleRollback}
                    disabled={mutationInFlight}
                    className="flex items-center gap-2 px-5 py-2 bg-stone-900 hover:bg-rose-950/30 border border-stone-700 hover:border-rose-800 text-stone-400 hover:text-rose-400 rounded-md transition-all duration-200 text-xs font-medium uppercase tracking-wider"
                >
                    <RotateCcw size={14} /> Reject
                </button>
                <button 
                    onClick={handleApprove}
                    disabled={mutationInFlight}
                    className="flex items-center gap-2 rounded-md border border-amber-600/40 bg-amber-950/35 px-6 py-2 text-xs font-bold uppercase tracking-wider text-amber-100 transition-all duration-200 hover:bg-amber-900/45 hover:border-amber-500/60 shadow-[0_0_15px_rgba(245,158,11,0.18)] hover:shadow-[0_0_20px_rgba(245,158,11,0.28)]"
                >
                    <Check size={14} /> Integrate
                </button>
              </div>
            </div>

            {/* Reading/Diff Area */}
            <div className="flex-1 overflow-y-auto px-8 py-8 custom-scrollbar">
               <div className="max-w-4xl mx-auto">
                   
                   {diffError ? (
                       <div className="mt-20 flex flex-col items-center justify-center text-rose-500 gap-6 animate-in fade-in zoom-in duration-300">
                           <div className="w-20 h-20 bg-rose-950/20 rounded-full flex items-center justify-center border border-rose-900/50 shadow-xl">
                                <Activity size={32} />
                           </div>
                           <div className="text-center">
                                <p className="text-lg font-medium text-rose-200">Memory Retrieval Failed</p>
                                <p className="text-rose-400/60 mt-2 max-w-md text-sm">{diffError}</p>
                           </div>
                           <button 
                               onClick={() => loadDiff(currentSessionId, selectedSnapshot.resource_id)} 
                               className="px-6 py-2 bg-stone-800/50 hover:bg-stone-800 rounded-full text-stone-300 text-xs transition-colors border border-stone-700"
                           >
                               Retry Connection
                           </button>
                       </div>
                   ) : diffData ? (
                       <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                           {/* Diff Summary Badge */}
                           <div className="mb-6 flex justify-end">
                               <div className={clsx(
                                   "inline-flex items-center gap-2 px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-widest border",
                                   diffData.has_changes 
                                    ? "bg-amber-500/5 border-amber-500/20 text-amber-500" 
                                    : "bg-stone-800/50 border-stone-700 text-stone-500"
                               )}>
                                   {diffData.has_changes ? "Modification Detected" : "No Content Deviation"}
                               </div>
                           </div>

                           {renderMetadataChanges()}
                           {renderSurvivingPaths()}
                           
                           {/* The Core Content */}
                           <div className="bg-stone-900/50 rounded-xl border border-stone-800/50 p-1 min-h-[200px] shadow-2xl relative overflow-hidden">
                                <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-amber-500/20 to-transparent opacity-50"></div>
                                <div className="p-6 md:p-10">
                                    <SimpleDiff 
                                        oldText={diffData.snapshot_data?.content ?? ''} 
                                        newText={diffData.current_data?.content ?? ''} 
                                    />
                                </div>
                           </div>
                       </div>
                   ) : (
                       <div className="flex flex-col items-center justify-center h-64 text-stone-700">
                           <div className="w-2 h-2 bg-amber-500 rounded-full animate-ping mb-4"></div>
                           <span className="text-xs tracking-widest uppercase opacity-50">Synchronizing...</span>
                       </div>
                   )}
               </div>
            </div>
          </>
        ) : diffError ? (
           <div className="flex-1 flex flex-col items-center justify-center text-rose-500 gap-4">
             <Activity size={48} className="opacity-20" />
             <p className="text-sm font-medium opacity-50">Connection Lost</p>
           </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-stone-700 gap-6 select-none">
            <div className="relative">
                <div className="absolute inset-0 bg-amber-500/20 blur-3xl rounded-full opacity-20 animate-pulse"></div>
                <Layout size={64} className="opacity-20 relative z-10" />
            </div>
            <div className="text-center">
                <p className="text-lg font-light text-stone-500">Awaiting Input</p>
                <p className="text-xs text-stone-600 mt-2 tracking-wide uppercase">Select a memory fragment to inspect</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default ReviewPage
