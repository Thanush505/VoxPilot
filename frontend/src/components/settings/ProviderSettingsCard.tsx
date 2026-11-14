import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Eye,
  EyeOff,
  KeyRound,
  Lock,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import apiClient from '../../api/client';
import type {
  AIProviderModelInfo,
  AIProviderSummary,
  ProfileProviderConfig,
} from '../../api/types';

interface ProviderSettingsCardProps {
  isBackendConnected: boolean;
}

const FALLBACK_OPENAI_MODELS: AIProviderModelInfo[] = [
  { id: 'gpt-4o-mini', name: 'GPT-4o mini', supports_tools: false, supports_vision: false },
  { id: 'gpt-4o', name: 'GPT-4o', supports_tools: false, supports_vision: false },
  { id: 'gpt-4.1-mini', name: 'GPT-4.1 mini', supports_tools: false, supports_vision: false },
  { id: 'gpt-4.1', name: 'GPT-4.1', supports_tools: false, supports_vision: false },
  { id: 'o4-mini', name: 'o4-mini', supports_tools: false, supports_vision: false },
];

export const ProviderSettingsCard: React.FC<ProviderSettingsCardProps> = ({
  isBackendConnected,
}) => {
  const [providers, setProviders] = useState<AIProviderSummary[]>([]);
  const [activeConfig, setActiveConfig] = useState<ProfileProviderConfig | null>(null);
  const [byokModels, setByokModels] = useState<AIProviderModelInfo[]>(FALLBACK_OPENAI_MODELS);

  const [apiKeyInput, setApiKeyInput] = useState('');
  const [showApiKey, setShowApiKey] = useState(false);
  const [selectedModel, setSelectedModel] = useState('gpt-4o-mini');
  const [confirmRemove, setConfirmRemove] = useState(false);

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const loadProviderState = useCallback(async () => {
    if (!isBackendConnected) return;
    setLoading(true);
    try {
      const [listRes, activeRes] = await Promise.all([
        apiClient.getProviders(),
        apiClient.getActiveProvider(),
      ]);
      if (listRes.ok && listRes.data) {
        setProviders(listRes.data.providers || []);
        if (listRes.data.byok_models && listRes.data.byok_models.length > 0) {
          setByokModels(listRes.data.byok_models);
        }
      }
      if (activeRes.ok && activeRes.data) {
        const cfg = activeRes.data.profile_config;
        setActiveConfig(cfg);
        if (cfg.byok_model) {
          setSelectedModel(cfg.byok_model);
        }
      }
    } finally {
      setLoading(false);
    }
  }, [isBackendConnected]);

  useEffect(() => {
    loadProviderState();
  }, [loadProviderState]);

  const handleSaveAndValidateKey = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = apiKeyInput.trim();
    if (!trimmed) {
      setFeedback({ type: 'error', text: 'Please enter an OpenAI API key.' });
      return;
    }

    setFeedback(null);
    setSaving(true);
    try {
      const res = await apiClient.configureOpenAIBYOK({
        api_key: trimmed,
        model: selectedModel,
        validate_connection: true,
      });
      if (res.ok && res.data) {
        setApiKeyInput('');
        setShowApiKey(false);
        setActiveConfig(res.data.profile_config);
        await loadProviderState();
        setFeedback({
          type: 'success',
          text:
            res.data.profile_config.active_provider_id === 'byok'
              ? 'OpenAI API key validated and encrypted with Windows DPAPI.'
              : 'OpenAI API key validated and encrypted with Windows DPAPI. Local AI remains active until you switch.',
        });
      } else {
        setFeedback({
          type: 'error',
          text: res.error || 'Could not validate or store OpenAI API key.',
        });
      }
    } finally {
      setSaving(false);
    }
  };

  const handleValidateStoredConnection = async () => {
    setFeedback(null);
    setValidating(true);
    try {
      const res = await apiClient.validateOpenAIBYOK({ model: selectedModel });
      if (res.ok && res.data) {
        setFeedback({
          type: 'success',
          text: `OpenAI connection verified (${res.data.health.active_model || selectedModel}${
            res.data.health.latency_ms ? `, ${Math.round(res.data.health.latency_ms)} ms` : ''
          }).`,
        });
      } else {
        setFeedback({
          type: 'error',
          text: res.error || 'OpenAI connection check failed.',
        });
      }
    } finally {
      setValidating(false);
    }
  };

  const handleModelChange = async (newModel: string) => {
    setSelectedModel(newModel);
    if (!isBackendConnected) return;
    setFeedback(null);
    const res = await apiClient.setOpenAIBYOKModel(newModel);
    if (res.ok && res.data) {
      setActiveConfig(res.data.profile_config);
      setFeedback({ type: 'success', text: `OpenAI BYOK model set to ${newModel}.` });
    } else {
      setFeedback({ type: 'error', text: res.error || 'Failed to update OpenAI model.' });
    }
  };

  const handleSelectProvider = async (providerId: string) => {
    setFeedback(null);
    setSwitching(true);
    try {
      const res = await apiClient.selectProvider(providerId);
      if (res.ok && res.data) {
        setActiveConfig(res.data.profile_config);
        await loadProviderState();
        setFeedback({
          type: 'success',
          text:
            providerId === 'local'
              ? `Switched active provider to Local AI (${res.data.profile_config.local_model}).`
              : `Switched active provider to BYOK (OpenAI: ${res.data.profile_config.byok_model || selectedModel}).`,
        });
      } else {
        setFeedback({
          type: 'error',
          text: res.error || `Could not switch to provider '${providerId}'.`,
        });
      }
    } finally {
      setSwitching(false);
    }
  };

  const handleRemoveKey = async () => {
    setFeedback(null);
    setSaving(true);
    try {
      const res = await apiClient.removeOpenAIBYOKCredentials();
      setConfirmRemove(false);
      setApiKeyInput('');
      setShowApiKey(false);
      if (res.ok && res.data) {
        setActiveConfig(res.data.profile_config);
        await loadProviderState();
        setFeedback({
          type: 'success',
          text: 'Stored OpenAI API key removed. Local AI (Ollama) is active.',
        });
      } else {
        setFeedback({
          type: 'error',
          text: res.error || 'Could not remove stored OpenAI API key.',
        });
      }
    } finally {
      setSaving(false);
    }
  };

  const hasCredential = Boolean(activeConfig?.has_byok_credential);
  const currentProviderId = activeConfig?.active_provider_id || 'local';
  const byokSummary = providers.find((p) => p.provider_id === 'byok');

  return (
    <section className="rounded-2xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900/70 p-6 shadow-sm space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="p-2.5 rounded-xl bg-blue-500/10 text-blue-600 dark:text-blue-400">
            <KeyRound className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">
              AI Provider &amp; BYOK (OpenAI)
            </h2>
            <p className="text-xs text-slate-600 dark:text-slate-400 mt-0.5">
              Choose between Local AI (Ollama, default) or your own OpenAI API key encrypted with Windows DPAPI.
            </p>
          </div>
        </div>

        <span
          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
            currentProviderId === 'byok'
              ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border border-emerald-500/20'
              : 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-200 dark:border-slate-700'
          }`}
        >
          <Cpu className="w-3.5 h-3.5" />
          Active: {currentProviderId === 'byok' ? 'BYOK (OpenAI)' : `Local (${activeConfig?.local_model || 'qwen3:4b'})`}
        </span>
      </div>

      {/* Privacy Notice */}
      <div className="flex items-start gap-2.5 rounded-xl border border-blue-500/20 bg-blue-500/5 px-3.5 py-2.5 text-xs text-slate-700 dark:text-slate-300">
        <ShieldCheck className="w-4 h-4 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" />
        <span>
          API keys are encrypted locally with Windows DPAPI per profile and never stored in plaintext.
          Prompts are sent to OpenAI only when you explicitly switch this profile to BYOK.
        </span>
      </div>

      {/* Active Provider Mode Selector */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
        <button
          type="button"
          disabled={!isBackendConnected || switching || loading}
          onClick={() => handleSelectProvider('local')}
          className={`text-left rounded-xl border px-3.5 py-2.5 transition ${
            currentProviderId === 'local'
              ? 'border-blue-500 bg-blue-500/10 text-slate-900 dark:text-slate-100'
              : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 text-slate-700 dark:text-slate-300'
          } disabled:opacity-50`}
        >
          <div className="text-xs font-semibold">Local AI (Ollama)</div>
          <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
            Default • {activeConfig?.local_model || 'qwen3:4b'}
          </div>
        </button>

        <button
          type="button"
          disabled={!isBackendConnected || switching || loading || !hasCredential}
          onClick={() => handleSelectProvider('byok')}
          className={`text-left rounded-xl border px-3.5 py-2.5 transition ${
            currentProviderId === 'byok'
              ? 'border-blue-500 bg-blue-500/10 text-slate-900 dark:text-slate-100'
              : 'border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700 text-slate-700 dark:text-slate-300'
          } disabled:opacity-50`}
        >
          <div className="text-xs font-semibold">BYOK (OpenAI)</div>
          <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
            {hasCredential ? `Ready • ${selectedModel}` : 'Save an OpenAI key first'}
          </div>
        </button>

        <div className="rounded-xl border border-dashed border-slate-200 dark:border-slate-800 px-3.5 py-2.5 opacity-60">
          <div className="text-xs font-semibold text-slate-700 dark:text-slate-300">
            VoxPilot Cloud
          </div>
          <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
            Coming in a later phase
          </div>
        </div>
      </div>

      {/* OpenAI Credential & Model Configuration */}
      <form onSubmit={handleSaveAndValidateKey} className="space-y-4 pt-2 border-t border-slate-200 dark:border-slate-800">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs font-medium text-slate-700 dark:text-slate-300">
            <Lock className="w-3.5 h-3.5 text-slate-500" />
            <span>OpenAI Credential Status:</span>
            {hasCredential ? (
              <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-semibold">
                <CheckCircle2 className="w-3.5 h-3.5" />
                Encrypted key stored ({byokSummary?.health?.status || 'ready'})
              </span>
            ) : (
              <span className="text-slate-500 dark:text-slate-400">Not configured</span>
            )}
          </div>

          {hasCredential && !confirmRemove && (
            <button
              type="button"
              onClick={() => setConfirmRemove(true)}
              disabled={saving}
              className="inline-flex items-center gap-1 text-xs text-rose-600 dark:text-rose-400 hover:underline disabled:opacity-50"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Remove Key
            </button>
          )}
        </div>

        {confirmRemove && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-3.5 py-2.5 text-xs">
            <span className="text-rose-700 dark:text-rose-300">
              Delete the DPAPI-encrypted OpenAI key for this profile?
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleRemoveKey}
                disabled={saving}
                className="px-2.5 py-1 rounded-lg bg-rose-600 text-white font-medium hover:bg-rose-700 disabled:opacity-50"
              >
                Confirm Remove
              </button>
              <button
                type="button"
                onClick={() => setConfirmRemove(false)}
                className="px-2.5 py-1 rounded-lg border border-slate-300 dark:border-slate-700 text-slate-700 dark:text-slate-300"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label
              htmlFor="openai-byok-key"
              className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1"
            >
              {hasCredential ? 'Replace OpenAI API Key' : 'OpenAI API Key'}
            </label>
            <div className="relative">
              <input
                id="openai-byok-key"
                type={showApiKey ? 'text' : 'password'}
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                placeholder={hasCredential ? '•••••••••••••••••••• (stored)' : 'sk-...'}
                autoComplete="off"
                spellCheck={false}
                disabled={!isBackendConnected || saving}
                className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-950 px-3 py-2 pr-9 text-xs text-slate-900 dark:text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/40"
              />
              <button
                type="button"
                onClick={() => setShowApiKey((v) => !v)}
                aria-label={showApiKey ? 'Hide API key' : 'Show API key'}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
              >
                {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          <div>
            <label
              htmlFor="openai-byok-model"
              className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1"
            >
              OpenAI Model
            </label>
            <select
              id="openai-byok-model"
              value={selectedModel}
              onChange={(e) => handleModelChange(e.target.value)}
              disabled={!isBackendConnected || saving}
              className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-950 px-3 py-2 text-xs text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500/40"
            >
              {byokModels.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name} ({m.id})
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <button
            type="submit"
            disabled={!isBackendConnected || saving || !apiKeyInput.trim()}
            className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3.5 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            <KeyRound className="w-3.5 h-3.5" />
            {saving ? 'Validating & Saving...' : 'Save & Validate Key'}
          </button>

          {hasCredential && (
            <button
              type="button"
              onClick={handleValidateStoredConnection}
              disabled={!isBackendConnected || validating || saving}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-300 dark:border-slate-700 px-3.5 py-2 text-xs font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${validating ? 'animate-spin' : ''}`} />
              {validating ? 'Testing...' : 'Validate Connection'}
            </button>
          )}

          {apiKeyInput && (
            <button
              type="button"
              onClick={() => {
                setApiKeyInput('');
                setShowApiKey(false);
              }}
              className="text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
            >
              Clear Input
            </button>
          )}
        </div>
      </form>

      {feedback && (
        <div
          role="status"
          className={`flex items-start gap-2 rounded-xl border px-3.5 py-2.5 text-xs ${
            feedback.type === 'success'
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
              : 'border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300'
          }`}
        >
          {feedback.type === 'success' ? (
            <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
          ) : (
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          )}
          <span>{feedback.text}</span>
        </div>
      )}
    </section>
  );
};

export default ProviderSettingsCard;
