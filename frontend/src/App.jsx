import React from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { ShieldCheck, Database, LibraryBig, Feather, Eye, Languages } from 'lucide-react';
import clsx from 'clsx';
import { motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';

import ReviewPage from './features/review/ReviewPage';
import MemoryBrowser from './features/memory/MemoryBrowser';
import MaintenancePage from './features/maintenance/MaintenancePage';
import ObservabilityPage from './features/observability/ObservabilityPage';
import AgentationLite from './components/AgentationLite';
import FluidBackground from './components/FluidBackground';
import SetupAssistantModal, {
  SETUP_ASSISTANT_DISMISSED_STORAGE_KEY,
} from './components/SetupAssistantModal';
import {
  clearStoredMaintenanceAuth,
  getMaintenanceAuthState,
} from './lib/api';
import { CHINESE_LOCALE, DEFAULT_LOCALE } from './i18n';

const DEFAULT_MEMORY_ROUTE = '/memory?domain=notes&path=projects';

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

function AuthControls({ authState, onOpenSetup, onClearApiKey }) {
  const { t } = useTranslation();

  if (authState?.source === 'runtime') {
    return (
      <div className="flex items-center gap-2">
        <div className="hidden md:flex items-center rounded-full border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs font-medium text-emerald-700 shadow-sm">
          {t('app.auth.runtimeBadge')}
        </div>
        <button
          type="button"
          onClick={onOpenSetup}
          data-testid="auth-open-setup"
          className="rounded-full border border-white/40 bg-white/40 px-3 py-2 text-xs font-medium text-[color:var(--palace-ink)] backdrop-blur-md transition hover:bg-white/60"
        >
          {t('app.auth.openSetup')}
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={onOpenSetup}
        data-testid="auth-set-api-key"
        className="rounded-full border border-white/40 bg-white/40 px-3 py-2 text-xs font-medium text-[color:var(--palace-ink)] backdrop-blur-md transition hover:bg-white/60"
      >
        {authState ? t('app.auth.updateApiKey') : t('app.auth.setApiKey')}
      </button>
      {authState ? (
        <button
          type="button"
          onClick={onClearApiKey}
          data-testid="auth-clear-api-key"
          className="rounded-full border border-white/30 bg-white/20 px-3 py-2 text-xs font-medium text-[color:var(--palace-muted)] backdrop-blur-md transition hover:bg-white/40 hover:text-[color:var(--palace-ink)]"
        >
          {t('app.auth.clearKey')}
        </button>
      ) : null}
    </div>
  );
}

function LanguageToggle() {
  const { t, i18n } = useTranslation();
  const currentLocale = i18n.resolvedLanguage || DEFAULT_LOCALE;
  const nextLocale = currentLocale === DEFAULT_LOCALE ? CHINESE_LOCALE : DEFAULT_LOCALE;
  const nextLabel = nextLocale === CHINESE_LOCALE
    ? t('common.language.chinese')
    : t('common.language.english');
  const ariaLabel = nextLocale === CHINESE_LOCALE
    ? t('common.language.switchToChinese')
    : t('common.language.switchToEnglish');

  const handleToggle = React.useCallback(() => {
    void i18n.changeLanguage(nextLocale);
  }, [i18n, nextLocale]);

  return (
    <button
      type="button"
      onClick={handleToggle}
      data-testid="language-toggle"
      aria-label={ariaLabel}
      title={ariaLabel}
      className="inline-flex items-center gap-2 rounded-full border border-white/40 bg-white/40 px-3 py-2 text-xs font-medium text-[color:var(--palace-ink)] backdrop-blur-md transition hover:bg-white/60"
    >
      <Languages size={14} />
      <span>{nextLabel}</span>
    </button>
  );
}

export function buildRoutesKey(authState, authRevision) {
  return authState
    ? `${authState.source}:${authState.mode}:${authRevision}`
    : `no-auth:${authRevision}`;
}

function Layout({ authState, authRevision, onOpenSetup, onClearApiKey }) {
  const { t } = useTranslation();
  const routesKey = buildRoutesKey(authState, authRevision);

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
            <span className="font-display text-lg font-semibold tracking-wide text-[color:var(--palace-ink)]">{t('common.appName')}</span>
          </motion.div>

          <motion.nav
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex min-w-0 max-w-full items-center gap-1 overflow-x-auto rounded-full border border-white/30 bg-white/20 p-1.5 backdrop-blur-xl shadow-[0_8px_32px_rgba(179,133,79,0.05)] scrollbar-hide"
          >
            <NavItem to={DEFAULT_MEMORY_ROUTE} icon={Database} label={t('app.nav.memory')} />
            <NavItem to="/review" icon={ShieldCheck} label={t('app.nav.review')} />
            <NavItem to="/maintenance" icon={Feather} label={t('app.nav.maintenance')} />
            <NavItem to="/observability" icon={Eye} label={t('app.nav.observability')} />
          </motion.nav>

          <div className="flex items-center gap-2">
            <LanguageToggle />
            <AuthControls
              authState={authState}
              onOpenSetup={onOpenSetup}
              onClearApiKey={onClearApiKey}
            />
          </div>
        </div>
      </div>

      {/* Main Area */}
      <div className="relative z-10 flex-1 min-h-0 overflow-hidden px-6 pb-6 pt-2">
        <div className="h-full w-full max-w-7xl mx-auto">
            <Routes key={routesKey}>
              <Route path="/" element={<Navigate to={DEFAULT_MEMORY_ROUTE} replace />} />
              <Route path="/review" element={<ReviewPage />} />
              <Route path="/memory" element={<MemoryBrowser />} />
              <Route path="/maintenance" element={<MaintenancePage />} />
              <Route path="/observability" element={<ObservabilityPage />} />
              <Route path="*" element={<Navigate to={DEFAULT_MEMORY_ROUTE} replace />} />
            </Routes>
        </div>
      </div>

      {import.meta.env.DEV && <AgentationLite />}
    </div>
  );
}

function App() {
  const { t, i18n } = useTranslation();
  const [authState, setAuthState] = React.useState(() => getMaintenanceAuthState());
  const [authRevision, setAuthRevision] = React.useState(0);
  const [setupOpen, setSetupOpen] = React.useState(false);

  const readDismissedState = React.useCallback(() => {
    if (typeof window === 'undefined') return null;
    try {
      return window.localStorage.getItem(SETUP_ASSISTANT_DISMISSED_STORAGE_KEY);
    } catch (_error) {
      return null;
    }
  }, []);

  const clearDismissedState = React.useCallback(() => {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.removeItem(SETUP_ASSISTANT_DISMISSED_STORAGE_KEY);
    } catch (_error) {
      // Storage can be unavailable in private / restricted browser contexts.
    }
  }, []);

  const persistDismissedState = React.useCallback((value) => {
    if (typeof window === 'undefined') return;
    try {
      if (value) {
        window.localStorage.setItem(SETUP_ASSISTANT_DISMISSED_STORAGE_KEY, '1');
      } else {
        window.localStorage.removeItem(SETUP_ASSISTANT_DISMISSED_STORAGE_KEY);
      }
    } catch (_error) {
      // Storage can be unavailable in private / restricted browser contexts.
    }
  }, []);

  React.useEffect(() => {
    document.title = t('app.documentTitle');
  }, [i18n.resolvedLanguage, t]);

  React.useEffect(() => {
    if (typeof window === 'undefined') return;
    if (authState?.source === 'runtime') return;
    if (authState?.source === 'stored') return;
    const dismissed = readDismissedState();
    if (dismissed === '1') return;
    setSetupOpen(true);
  }, [authState?.source, readDismissedState]);

  const handleOpenSetup = React.useCallback(() => {
    clearDismissedState();
    setSetupOpen(true);
  }, [clearDismissedState]);

  const handleCloseSetup = React.useCallback(() => {
    persistDismissedState(true);
    setSetupOpen(false);
  }, [persistDismissedState]);

  const handleAuthUpdated = React.useCallback((nextAuth) => {
    setAuthState(nextAuth ?? getMaintenanceAuthState());
    setAuthRevision((value) => value + 1);
  }, []);

  const handleClearApiKey = React.useCallback(() => {
    clearStoredMaintenanceAuth();
    persistDismissedState(false);
    setAuthState(getMaintenanceAuthState());
    setAuthRevision((value) => value + 1);
  }, [persistDismissedState]);

  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Layout
        authState={authState}
        authRevision={authRevision}
        onOpenSetup={handleOpenSetup}
        onClearApiKey={handleClearApiKey}
      />
      <SetupAssistantModal
        open={setupOpen}
        authState={authState}
        onAuthUpdated={handleAuthUpdated}
        onClose={handleCloseSetup}
      />
    </BrowserRouter>
  );
}

export default App;
