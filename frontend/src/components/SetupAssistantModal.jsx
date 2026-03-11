import React from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  KeyRound,
  Languages,
  Route,
  Save,
  Sparkles,
  Wand2,
  X,
} from 'lucide-react';

import GlassCard from './GlassCard';
import {
  extractApiError,
  getSetupStatus,
  saveSetupConfig,
  saveStoredMaintenanceAuth,
} from '../lib/api';

export const SETUP_ASSISTANT_DISMISSED_STORAGE_KEY = 'memory-palace.setupAssistantDismissed';

const defaultFormState = (authState) => ({
  dashboard_api_key: authState?.source === 'stored' ? authState.key : '',
  allow_insecure_local: false,
  embedding_backend: 'hash',
  embedding_api_base: '',
  embedding_api_key: '',
  embedding_model: '',
  reranker_enabled: false,
  reranker_api_base: '',
  reranker_api_key: '',
  reranker_model: '',
  write_guard_llm_enabled: false,
  write_guard_llm_api_base: '',
  write_guard_llm_api_key: '',
  write_guard_llm_model: '',
  intent_llm_enabled: false,
  intent_llm_api_base: '',
  intent_llm_api_key: '',
  intent_llm_model: '',
  router_api_base: '',
  router_api_key: '',
  router_chat_model: '',
  router_embedding_model: '',
  router_reranker_model: '',
});

function SetupInput({ label, hint, value, onChange, placeholder, type = 'text' }) {
  return (
    <label className="block space-y-2">
      <div>
        <div className="text-sm font-medium text-[color:var(--palace-ink)]">{label}</div>
        {hint ? (
          <div className="mt-1 text-xs leading-relaxed text-[color:var(--palace-muted)]">
            {hint}
          </div>
        ) : null}
      </div>
      <input
        type={type}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className="palace-input"
      />
    </label>
  );
}

function SetupSelect({ label, hint, value, onChange, options }) {
  return (
    <label className="block space-y-2">
      <div>
        <div className="text-sm font-medium text-[color:var(--palace-ink)]">{label}</div>
        {hint ? (
          <div className="mt-1 text-xs leading-relaxed text-[color:var(--palace-muted)]">
            {hint}
          </div>
        ) : null}
      </div>
      <select value={value} onChange={onChange} className="palace-input">
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function SetupToggle({ label, hint, checked, onChange }) {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-4 rounded-2xl border border-[color:var(--palace-line)] bg-white/55 px-4 py-3">
      <div className="min-w-0">
        <div className="text-sm font-medium text-[color:var(--palace-ink)]">{label}</div>
        {hint ? (
          <div className="mt-1 text-xs leading-relaxed text-[color:var(--palace-muted)]">
            {hint}
          </div>
        ) : null}
      </div>
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        className="mt-1 h-4 w-4 accent-[color:var(--palace-accent)]"
      />
    </label>
  );
}

function SummaryItem({ label, value, configuredText, missingText }) {
  const active = Boolean(value);
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-[color:var(--palace-line)] bg-white/45 px-3 py-2">
      <span className="text-sm text-[color:var(--palace-ink)]">{label}</span>
      <span
        className={
          active
            ? 'rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-xs font-semibold text-emerald-700'
            : 'rounded-full border border-amber-500/25 bg-amber-500/10 px-2 py-0.5 text-xs font-semibold text-amber-700'
        }
      >
        {active ? configuredText : missingText}
      </span>
    </div>
  );
}

export default function SetupAssistantModal({
  open,
  authState,
  onClose,
  onAuthUpdated,
}) {
  const { t, i18n } = useTranslation();
  const [form, setForm] = React.useState(() => defaultFormState(authState));
  const [statusLoading, setStatusLoading] = React.useState(false);
  const [setupStatus, setSetupStatus] = React.useState(null);
  const [statusError, setStatusError] = React.useState(null);
  const [saveError, setSaveError] = React.useState(null);
  const [saveSuccess, setSaveSuccess] = React.useState(null);
  const [savingMode, setSavingMode] = React.useState(null);

  React.useEffect(() => {
    if (!open) return undefined;

    let cancelled = false;
    setForm(defaultFormState(authState));
    setStatusLoading(true);
    setSetupStatus(null);
    setStatusError(null);
    setSaveError(null);
    setSaveSuccess(null);

    getSetupStatus()
      .then((payload) => {
        if (cancelled) return;
        setSetupStatus(payload);
        setForm((current) => ({
          ...current,
          allow_insecure_local: payload?.summary?.allow_insecure_local ?? current.allow_insecure_local,
          embedding_backend: payload?.summary?.embedding_backend ?? current.embedding_backend,
          reranker_enabled: payload?.summary?.reranker_enabled ?? current.reranker_enabled,
          write_guard_llm_enabled:
            payload?.summary?.write_guard_enabled ?? current.write_guard_llm_enabled,
          intent_llm_enabled: payload?.summary?.intent_llm_enabled ?? current.intent_llm_enabled,
        }));
      })
      .catch((error) => {
        if (cancelled) return;
        setStatusError(extractApiError(error, t('setup.messages.statusUnavailable')));
      })
      .finally(() => {
        if (cancelled) return;
        setStatusLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [authState, open, t]);

  const updateField = React.useCallback((key, value) => {
    setForm((current) => ({ ...current, [key]: value }));
  }, []);

  const applyPreset = React.useCallback((preset) => {
    setForm((current) => {
      if (preset === 'b') {
        return {
          ...current,
          embedding_backend: 'hash',
          reranker_enabled: false,
          write_guard_llm_enabled: false,
          intent_llm_enabled: false,
        };
      }
      if (preset === 'c') {
        return {
          ...current,
          embedding_backend: 'api',
          reranker_enabled: false,
        };
      }
      return {
        ...current,
        embedding_backend: 'api',
        reranker_enabled: true,
      };
    });
  }, []);

  const saveBrowserOnlyDisabled = !String(form.dashboard_api_key || '').trim();
  const showEmbeddingApiFields = form.embedding_backend === 'api';
  const showRouterFields = form.embedding_backend === 'router';
  const showRerankerApiFields = form.reranker_enabled && form.embedding_backend !== 'router';
  const canPersistServer = setupStatus?.apply_supported === true;
  const restartTargets = setupStatus?.restart_targets || saveSuccess?.restart_targets || [];
  const summary = setupStatus?.summary || {};
  const rerankerStatus =
    typeof summary.reranker_enabled === 'boolean'
      ? (!summary.reranker_enabled || summary.reranker_configured)
      : false;
  const writeGuardStatus =
    typeof summary.write_guard_enabled === 'boolean'
      ? (!summary.write_guard_enabled || summary.write_guard_configured)
      : false;
  const intentStatus =
    typeof summary.intent_llm_enabled === 'boolean'
      ? (!summary.intent_llm_enabled || summary.intent_llm_configured)
      : false;

  const handleSaveBrowserOnly = React.useCallback(() => {
    const saved = saveStoredMaintenanceAuth(form.dashboard_api_key, authState?.mode ?? 'header');
    if (saved === false) {
      setSaveError(t('setup.messages.saveFailed'));
      return;
    }
    if (!saved) {
      setSaveError(t('setup.messages.browserOnlyRequiresKey'));
      return;
    }
    onAuthUpdated?.(saved);
    setSaveError(null);
    setSaveSuccess({
      kind: 'browser',
      message: t('setup.messages.browserOnlySaved'),
    });
    onClose();
  }, [authState?.mode, form.dashboard_api_key, onAuthUpdated, onClose, t]);

  const handlePersistConfig = React.useCallback(async () => {
    setSavingMode('server');
    setSaveError(null);
    setSaveSuccess(null);
    try {
      const payload = {
        ...form,
      };
      const response = await saveSetupConfig(payload);
      if (String(form.dashboard_api_key || '').trim()) {
        const saved = saveStoredMaintenanceAuth(form.dashboard_api_key, authState?.mode ?? 'header');
        if (saved) {
          onAuthUpdated?.(saved);
        }
      }
      setSetupStatus((current) => ({
        ...(current || {}),
        ...response,
        apply_supported: current?.apply_supported ?? true,
      }));
      setSaveSuccess({
        kind: 'server',
        message: t('setup.messages.serverSaved', { target: response.target_label || '.env' }),
        restart_targets: response.restart_targets || [],
      });
    } catch (error) {
      setSaveError(extractApiError(error, t('setup.messages.saveFailed')));
    } finally {
      setSavingMode(null);
    }
  }, [authState?.mode, form, onAuthUpdated, t]);

  const currentLocale = i18n.resolvedLanguage || 'en';
  const nextLocale = currentLocale === 'en' ? 'zh-CN' : 'en';
  const nextLocaleLabel =
    nextLocale === 'zh-CN'
      ? t('common.language.chinese')
      : t('common.language.english');
  const nextLocaleAriaLabel =
    nextLocale === 'zh-CN'
      ? t('common.language.switchToChinese')
      : t('common.language.switchToEnglish');

  const handleToggleLanguage = React.useCallback(() => {
    void i18n.changeLanguage(nextLocale);
  }, [i18n, nextLocale]);

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          className="fixed inset-0 z-40 flex items-center justify-center bg-[rgba(47,42,36,0.18)] px-4 py-6 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="setup-assistant-title"
            initial={{ opacity: 0, y: 18, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 18, scale: 0.98 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
            className="glass-card flex max-h-[92vh] w-full max-w-6xl flex-col overflow-hidden border-white/60 bg-white/70"
          >
            <div className="flex items-start justify-between gap-4 border-b border-[color:var(--palace-line)] bg-white/55 px-6 py-5">
              <div className="min-w-0">
                <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-[color:var(--palace-line)] bg-white/60 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-[color:var(--palace-muted)]">
                  <Wand2 size={13} />
                  {t('setup.kicker')}
                </div>
                <h2
                  id="setup-assistant-title"
                  className="font-display text-2xl font-semibold text-[color:var(--palace-ink)]"
                >
                  {t('setup.title')}
                </h2>
                <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[color:var(--palace-muted)]">
                  {t('setup.subtitle')}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleToggleLanguage}
                  data-testid="setup-language-toggle"
                  aria-label={nextLocaleAriaLabel}
                  title={nextLocaleAriaLabel}
                  className="inline-flex items-center gap-2 rounded-full border border-white/50 bg-white/50 px-3 py-2 text-xs font-medium text-[color:var(--palace-ink)] transition hover:bg-white/80"
                >
                  <Languages size={14} />
                  <span>{nextLocaleLabel}</span>
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-full border border-white/50 bg-white/50 p-2 text-[color:var(--palace-muted)] transition hover:bg-white/80 hover:text-[color:var(--palace-ink)]"
                  aria-label={t('setup.actions.close')}
                >
                  <X size={16} />
                </button>
              </div>
            </div>

            <div className="grid min-h-0 flex-1 gap-5 overflow-hidden px-5 py-5 lg:grid-cols-[minmax(0,1.55fr)_360px]">
              <div className="space-y-5 overflow-y-auto pr-1">
                <GlassCard className="space-y-4 p-5">
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5 inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-[linear-gradient(135deg,var(--palace-accent),var(--palace-accent-2))] text-white shadow-sm">
                      <KeyRound size={18} />
                    </div>
                    <div>
                      <div className="text-lg font-semibold text-[color:var(--palace-ink)]">
                        {t('setup.dashboard.title')}
                      </div>
                      <div className="mt-1 text-sm leading-relaxed text-[color:var(--palace-muted)]">
                        {t('setup.dashboard.description')}
                      </div>
                    </div>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <SetupInput
                      label={t('setup.dashboard.apiKeyLabel')}
                      hint={t('setup.dashboard.apiKeyHint')}
                      value={form.dashboard_api_key}
                      onChange={(event) => updateField('dashboard_api_key', event.target.value)}
                      placeholder={t('setup.dashboard.apiKeyPlaceholder')}
                      type="password"
                    />
                    <SetupToggle
                      label={t('setup.dashboard.insecureLocalLabel')}
                      hint={t('setup.dashboard.insecureLocalHint')}
                      checked={form.allow_insecure_local}
                      onChange={(event) => updateField('allow_insecure_local', event.target.checked)}
                    />
                  </div>

                  <div className="rounded-2xl border border-[color:var(--palace-line)] bg-white/45 p-4 text-sm leading-relaxed text-[color:var(--palace-muted)]">
                    {t('setup.dashboard.browserScope')}
                  </div>
                </GlassCard>

                <GlassCard className="space-y-4 p-5">
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5 inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-[rgba(212,175,55,0.12)] text-[color:var(--palace-accent-2)]">
                      <Route size={18} />
                    </div>
                    <div>
                      <div className="text-lg font-semibold text-[color:var(--palace-ink)]">
                        {t('setup.retrieval.title')}
                      </div>
                      <div className="mt-1 text-sm leading-relaxed text-[color:var(--palace-muted)]">
                        {t('setup.retrieval.description')}
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    {['b', 'c', 'd'].map((preset) => (
                      <button
                        key={preset}
                        type="button"
                        onClick={() => applyPreset(preset)}
                        className="rounded-full border border-white/40 bg-white/55 px-3 py-1.5 text-xs font-medium text-[color:var(--palace-ink)] transition hover:bg-white/80"
                      >
                        {t(`setup.retrieval.presets.${preset}`)}
                      </button>
                    ))}
                  </div>

                  <SetupSelect
                    label={t('setup.retrieval.embeddingBackendLabel')}
                    hint={t('setup.retrieval.embeddingBackendHint')}
                    value={form.embedding_backend}
                    onChange={(event) => updateField('embedding_backend', event.target.value)}
                    options={[
                      { value: 'none', label: t('setup.retrieval.backends.none') },
                      { value: 'hash', label: t('setup.retrieval.backends.hash') },
                      { value: 'api', label: t('setup.retrieval.backends.api') },
                      { value: 'router', label: t('setup.retrieval.backends.router') },
                    ]}
                  />

                  {showEmbeddingApiFields ? (
                    <div className="grid gap-4 md:grid-cols-2">
                      <SetupInput
                        label={t('setup.retrieval.embeddingApiBaseLabel')}
                        value={form.embedding_api_base}
                        onChange={(event) => updateField('embedding_api_base', event.target.value)}
                        placeholder={t('setup.retrieval.embeddingApiBasePlaceholder')}
                      />
                      <SetupInput
                        label={t('setup.retrieval.embeddingModelLabel')}
                        value={form.embedding_model}
                        onChange={(event) => updateField('embedding_model', event.target.value)}
                        placeholder={t('setup.retrieval.embeddingModelPlaceholder')}
                      />
                      <SetupInput
                        label={t('setup.retrieval.embeddingApiKeyLabel')}
                        hint={t('setup.retrieval.optionalApiKeyHint')}
                        value={form.embedding_api_key}
                        onChange={(event) => updateField('embedding_api_key', event.target.value)}
                        placeholder={t('setup.retrieval.embeddingApiKeyPlaceholder')}
                        type="password"
                      />
                    </div>
                  ) : null}

                  {showRouterFields ? (
                    <div className="grid gap-4 md:grid-cols-2">
                      <SetupInput
                        label={t('setup.retrieval.routerApiBaseLabel')}
                        value={form.router_api_base}
                        onChange={(event) => updateField('router_api_base', event.target.value)}
                        placeholder={t('setup.retrieval.routerApiBasePlaceholder')}
                      />
                      <SetupInput
                        label={t('setup.retrieval.routerApiKeyLabel')}
                        hint={t('setup.retrieval.optionalApiKeyHint')}
                        value={form.router_api_key}
                        onChange={(event) => updateField('router_api_key', event.target.value)}
                        placeholder={t('setup.retrieval.routerApiKeyPlaceholder')}
                        type="password"
                      />
                      <SetupInput
                        label={t('setup.retrieval.routerEmbeddingModelLabel')}
                        value={form.router_embedding_model}
                        onChange={(event) => updateField('router_embedding_model', event.target.value)}
                        placeholder={t('setup.retrieval.routerEmbeddingModelPlaceholder')}
                      />
                      <SetupInput
                        label={t('setup.retrieval.routerRerankerModelLabel')}
                        value={form.router_reranker_model}
                        onChange={(event) => updateField('router_reranker_model', event.target.value)}
                        placeholder={t('setup.retrieval.routerRerankerModelPlaceholder')}
                      />
                    </div>
                  ) : null}

                  <SetupToggle
                    label={t('setup.retrieval.rerankerEnabledLabel')}
                    hint={t('setup.retrieval.rerankerEnabledHint')}
                    checked={form.reranker_enabled}
                    onChange={(event) => updateField('reranker_enabled', event.target.checked)}
                  />

                  {showRerankerApiFields ? (
                    <div className="grid gap-4 md:grid-cols-2">
                      <SetupInput
                        label={t('setup.retrieval.rerankerApiBaseLabel')}
                        value={form.reranker_api_base}
                        onChange={(event) => updateField('reranker_api_base', event.target.value)}
                        placeholder={t('setup.retrieval.rerankerApiBasePlaceholder')}
                      />
                      <SetupInput
                        label={t('setup.retrieval.rerankerModelLabel')}
                        value={form.reranker_model}
                        onChange={(event) => updateField('reranker_model', event.target.value)}
                        placeholder={t('setup.retrieval.rerankerModelPlaceholder')}
                      />
                      <SetupInput
                        label={t('setup.retrieval.rerankerApiKeyLabel')}
                        hint={t('setup.retrieval.optionalApiKeyHint')}
                        value={form.reranker_api_key}
                        onChange={(event) => updateField('reranker_api_key', event.target.value)}
                        placeholder={t('setup.retrieval.rerankerApiKeyPlaceholder')}
                        type="password"
                      />
                    </div>
                  ) : null}
                </GlassCard>

                <GlassCard className="space-y-4 p-5">
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5 inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-[rgba(184,150,46,0.12)] text-[color:var(--palace-accent-2)]">
                      <Bot size={18} />
                    </div>
                    <div>
                      <div className="text-lg font-semibold text-[color:var(--palace-ink)]">
                        {t('setup.llm.title')}
                      </div>
                      <div className="mt-1 text-sm leading-relaxed text-[color:var(--palace-muted)]">
                        {t('setup.llm.description')}
                      </div>
                    </div>
                  </div>

                  <SetupToggle
                    label={t('setup.llm.writeGuardEnabledLabel')}
                    hint={t('setup.llm.writeGuardEnabledHint')}
                    checked={form.write_guard_llm_enabled}
                    onChange={(event) => updateField('write_guard_llm_enabled', event.target.checked)}
                  />

                  {form.write_guard_llm_enabled ? (
                    <div className="grid gap-4 md:grid-cols-2">
                      <SetupInput
                        label={t('setup.llm.writeGuardApiBaseLabel')}
                        value={form.write_guard_llm_api_base}
                        onChange={(event) => updateField('write_guard_llm_api_base', event.target.value)}
                        placeholder={t('setup.llm.writeGuardApiBasePlaceholder')}
                      />
                      <SetupInput
                        label={t('setup.llm.writeGuardModelLabel')}
                        value={form.write_guard_llm_model}
                        onChange={(event) => updateField('write_guard_llm_model', event.target.value)}
                        placeholder={t('setup.llm.writeGuardModelPlaceholder')}
                      />
                      <SetupInput
                        label={t('setup.llm.writeGuardApiKeyLabel')}
                        hint={t('setup.retrieval.optionalApiKeyHint')}
                        value={form.write_guard_llm_api_key}
                        onChange={(event) => updateField('write_guard_llm_api_key', event.target.value)}
                        placeholder={t('setup.llm.writeGuardApiKeyPlaceholder')}
                        type="password"
                      />
                    </div>
                  ) : null}

                  <SetupToggle
                    label={t('setup.llm.intentEnabledLabel')}
                    hint={t('setup.llm.intentEnabledHint')}
                    checked={form.intent_llm_enabled}
                    onChange={(event) => updateField('intent_llm_enabled', event.target.checked)}
                  />

                  {form.intent_llm_enabled ? (
                    <div className="grid gap-4 md:grid-cols-2">
                      <SetupInput
                        label={t('setup.llm.intentApiBaseLabel')}
                        value={form.intent_llm_api_base}
                        onChange={(event) => updateField('intent_llm_api_base', event.target.value)}
                        placeholder={t('setup.llm.intentApiBasePlaceholder')}
                      />
                      <SetupInput
                        label={t('setup.llm.intentModelLabel')}
                        value={form.intent_llm_model}
                        onChange={(event) => updateField('intent_llm_model', event.target.value)}
                        placeholder={t('setup.llm.intentModelPlaceholder')}
                      />
                      <SetupInput
                        label={t('setup.llm.intentApiKeyLabel')}
                        hint={t('setup.retrieval.optionalApiKeyHint')}
                        value={form.intent_llm_api_key}
                        onChange={(event) => updateField('intent_llm_api_key', event.target.value)}
                        placeholder={t('setup.llm.intentApiKeyPlaceholder')}
                        type="password"
                      />
                      {showRouterFields ? (
                        <SetupInput
                          label={t('setup.llm.routerChatModelLabel')}
                          value={form.router_chat_model}
                          onChange={(event) => updateField('router_chat_model', event.target.value)}
                          placeholder={t('setup.llm.routerChatModelPlaceholder')}
                        />
                      ) : null}
                    </div>
                  ) : null}

                  <div className="rounded-2xl border border-[color:var(--palace-line)] bg-white/45 p-4 text-sm leading-relaxed text-[color:var(--palace-muted)]">
                    {t('setup.llm.gistFallbackHint')}
                  </div>
                </GlassCard>
              </div>

              <div className="space-y-4 overflow-y-auto pr-1">
                <GlassCard className="space-y-4 p-5">
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5 inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-[rgba(212,175,55,0.12)] text-[color:var(--palace-accent-2)]">
                      <Sparkles size={18} />
                    </div>
                    <div>
                      <div className="text-lg font-semibold text-[color:var(--palace-ink)]">
                        {t('setup.summary.title')}
                      </div>
                      <div className="mt-1 text-sm leading-relaxed text-[color:var(--palace-muted)]">
                        {statusLoading
                          ? t('setup.summary.loading')
                          : statusError || t('setup.summary.subtitle')}
                      </div>
                    </div>
                  </div>

                  <div className="rounded-2xl border border-[color:var(--palace-line)] bg-white/45 p-4">
                    <div className="text-sm font-medium text-[color:var(--palace-ink)]">
                      {t('setup.summary.targetLabel', {
                        target: setupStatus?.target_label || '.env',
                      })}
                    </div>
                    <div className="mt-2 text-sm leading-relaxed text-[color:var(--palace-muted)]">
                      {canPersistServer
                        ? t('setup.summary.applySupported')
                        : t(`setup.reasons.${setupStatus?.apply_reason || 'status_unavailable'}`)}
                    </div>
                  </div>

                  <div className="space-y-2">
                    <SummaryItem
                      label={t('setup.summary.dashboardAuth')}
                      value={summary.dashboard_auth_configured}
                      configuredText={t('setup.summary.configured')}
                      missingText={t('setup.summary.missing')}
                    />
                    <SummaryItem
                      label={t('setup.summary.embedding')}
                      value={summary.embedding_configured}
                      configuredText={t('setup.summary.configured')}
                      missingText={t('setup.summary.missing')}
                    />
                    <SummaryItem
                      label={t('setup.summary.reranker')}
                      value={rerankerStatus}
                      configuredText={summary.reranker_enabled
                        ? t('setup.summary.configured')
                        : t('setup.summary.notEnabled')}
                      missingText={t('setup.summary.missing')}
                    />
                    <SummaryItem
                      label={t('setup.summary.writeGuard')}
                      value={writeGuardStatus}
                      configuredText={summary.write_guard_enabled
                        ? t('setup.summary.configured')
                        : t('setup.summary.notEnabled')}
                      missingText={t('setup.summary.missing')}
                    />
                    <SummaryItem
                      label={t('setup.summary.intent')}
                      value={intentStatus}
                      configuredText={summary.intent_llm_enabled
                        ? t('setup.summary.configured')
                        : t('setup.summary.notEnabled')}
                      missingText={t('setup.summary.missing')}
                    />
                  </div>
                </GlassCard>

                <GlassCard className="space-y-4 p-5">
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5 inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-[rgba(47,42,36,0.08)] text-[color:var(--palace-ink)]">
                      <Save size={18} />
                    </div>
                    <div>
                      <div className="text-lg font-semibold text-[color:var(--palace-ink)]">
                        {t('setup.actions.nextTitle')}
                      </div>
                      <div className="mt-1 text-sm leading-relaxed text-[color:var(--palace-muted)]">
                        {t('setup.actions.nextHint')}
                      </div>
                    </div>
                  </div>

                  <div className="rounded-2xl border border-[color:var(--palace-line)] bg-white/45 p-4 text-sm leading-relaxed text-[color:var(--palace-muted)]">
                    {restartTargets.length > 0
                      ? t('setup.summary.restartHint', {
                          targets: restartTargets.join(' / '),
                        })
                      : t('setup.summary.browserOnlyHint')}
                  </div>

                  {saveSuccess ? (
                    <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/10 p-4 text-sm text-emerald-800">
                      <div className="mb-1 flex items-center gap-2 font-medium">
                        <CheckCircle2 size={16} />
                        {saveSuccess.message}
                      </div>
                      {saveSuccess.kind === 'server' && restartTargets.length > 0 ? (
                        <div>{t('setup.summary.restartHint', { targets: restartTargets.join(' / ') })}</div>
                      ) : null}
                    </div>
                  ) : null}

                  {saveError ? (
                    <div className="rounded-2xl border border-amber-500/25 bg-amber-500/10 p-4 text-sm text-amber-800">
                      <div className="mb-1 flex items-center gap-2 font-medium">
                        <AlertTriangle size={16} />
                        {t('setup.messages.saveFailed')}
                      </div>
                      <div>{saveError}</div>
                    </div>
                  ) : null}

                  <div className="space-y-3">
                    <button
                      type="button"
                      onClick={handlePersistConfig}
                      disabled={!canPersistServer || savingMode === 'server'}
                      className="palace-btn-primary w-full justify-center disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {savingMode === 'server'
                        ? t('setup.actions.savingEnv')
                        : t('setup.actions.saveEnv')}
                    </button>
                    <button
                      type="button"
                      onClick={handleSaveBrowserOnly}
                      disabled={saveBrowserOnlyDisabled || savingMode === 'server'}
                      className="palace-btn-ghost w-full justify-center border border-[color:var(--palace-line)] bg-white/45 text-[color:var(--palace-ink)] disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {t('setup.actions.saveBrowserOnly')}
                    </button>
                    <button
                      type="button"
                      onClick={onClose}
                      className="palace-btn-ghost w-full justify-center"
                    >
                      {t('setup.actions.close')}
                    </button>
                  </div>
                </GlassCard>
              </div>
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
