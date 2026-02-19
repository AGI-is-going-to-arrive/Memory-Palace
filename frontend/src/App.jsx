import React from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { ShieldCheck, Database, LibraryBig, Feather, Eye } from 'lucide-react';
import clsx from 'clsx';
import { motion } from 'framer-motion';

import ReviewPage from './features/review/ReviewPage';
import MemoryBrowser from './features/memory/MemoryBrowser';
import MaintenancePage from './features/maintenance/MaintenancePage';
import ObservabilityPage from './features/observability/ObservabilityPage';
import AgentationLite from './components/AgentationLite';
import FluidBackground from './components/FluidBackground';

function NavItem({ to, icon: Icon, label }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) => clsx(
        "relative flex h-10 items-center gap-2 rounded-full px-4 text-sm font-medium transition-all duration-300",
        isActive
          ? "text-[color:var(--palace-ink)]"
          : "text-[color:var(--palace-muted)] hover:text-[color:var(--palace-ink)]"
      )}
    >
      {({ isActive }) => (
        <>
          {isActive && (
            <motion.div
              layoutId="nav-pill"
              className="absolute inset-0 rounded-full bg-white shadow-[0_2px_12px_rgba(212,175,55,0.15)] ring-1 ring-[color:var(--palace-accent)]/20"
              transition={{ type: "spring", bounce: 0.2, duration: 0.6 }}
            />
          )}
          <span className="relative z-10 flex items-center gap-2">
            <Icon size={16} className={clsx(isActive ? "text-[color:var(--palace-accent)]" : "text-current")} />
            {label}
          </span>
        </>
      )}
    </NavLink>
  );
}

function Layout() {
  return (
    <div className="relative flex h-screen flex-col overflow-hidden text-[color:var(--palace-ink)]">
      <FluidBackground />

      {/* Floating Header */}
      <div className="relative z-20 shrink-0 px-6 pt-6 pb-2">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <motion.div
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            className="flex items-center gap-3 rounded-2xl bg-white/40 px-4 py-2 backdrop-blur-md border border-white/40 shadow-sm"
          >
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[linear-gradient(135deg,var(--palace-accent),var(--palace-accent-2))] text-white shadow-md">
              <LibraryBig size={18} />
            </div>
            <span className="font-display text-lg font-semibold tracking-wide text-[color:var(--palace-ink)]">Memory Palace</span>
          </motion.div>

          <motion.nav
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex min-w-0 max-w-full items-center gap-1 overflow-x-auto rounded-full border border-white/30 bg-white/20 p-1.5 backdrop-blur-xl shadow-[0_8px_32px_rgba(179,133,79,0.05)] scrollbar-hide"
          >
            <NavItem to="/memory" icon={Database} label="Memory" />
            <NavItem to="/review" icon={ShieldCheck} label="Review" />
            <NavItem to="/maintenance" icon={Feather} label="Maintenance" />
            <NavItem to="/observability" icon={Eye} label="Observability" />
          </motion.nav>

           <div className="hidden md:block w-[140px]" /> {/* Spacer for centering if needed, or actions */}
        </div>
      </div>

      {/* Main Area */}
      <div className="relative z-10 flex-1 min-h-0 overflow-hidden px-6 pb-6 pt-2">
        <div className="h-full w-full max-w-7xl mx-auto">
             <Routes>
              <Route path="/" element={<Navigate to="/memory" replace />} />
              <Route path="/review" element={<ReviewPage />} />
              <Route path="/memory" element={<MemoryBrowser />} />
              <Route path="/maintenance" element={<MaintenancePage />} />
              <Route path="/observability" element={<ObservabilityPage />} />
            </Routes>
        </div>
      </div>

      {import.meta.env.DEV && <AgentationLite />}
    </div>
  );
}

function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Layout />
    </BrowserRouter>
  );
}

export default App;
