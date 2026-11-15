import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Cloud,
  Cpu,
  Eye,
  EyeOff,
  KeyRound,
  Lock,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
} from 'lucide-react';
import apiClient from '../../api/client';
import type {
  AIProviderModelInfo,
  BYOKVendorId,
  BYOKVendorStatus,
  ProfileProviderConfig,
} from '../../api/types';
import { useOptionalAICore } from '../../context/AICoreContext';
import { StatusPill } from '../ui/DesignSystem';

interface ProviderSettingsCardProps {
  isBackendConnected: boolean;
}

const DEFAULT_VENDORS: BYOKVendorStatus[] = [
  {
    vendor: 'openai',
    display_name: 'OpenAI',
    configured: false,
    is_active: false,
    selected_model: 'gpt-4o-mini',
    capabilities: {
      streaming: false,
      structured_output: true,
      tool_calling: false,
      vision: false,
      local_only: false,
    },
    models: [
      { id: 'gpt-4o-mini', name: 'GPT-4o mini', supports_tools: false, supports_vision: false },
      { id: 'gpt-4o', name: 'GPT-4o', supports_tools: false, supports_vision: false },
      { id: 'gpt-4.1-mini', name: 'GPT-4.1 mini', supports_tools: false, supports_vision: false },
      { id: 'gpt-4.1', name: 'GPT-4.1', supports_tools: false, supports_vision: false },
      { id: 'o4-mini', name: 'o4-mini', supports_tools: false, supports_vision: false },
    ],
  },
  {
    vendor: 'anthropic',
    display_name: 'Anthropic Claude',
    configured: false,
    is_active: false,
    selected_model: 'claude-sonnet-4-20250514',
    capabilities: {
      streaming: false,
      structured_output: false,
      tool_calling: false,
      vision: false,
      local_only: false,
    },
    models: [
      { id: 'claude-sonnet-4-20250514', name: 'Claude Sonnet 4', supports_tools: false, supports_vision: false },
      { id: 'claude-3-7-sonnet-latest', name: 'Claude 3.7 Sonnet', supports_tools: false, supports_vision: false },
      { id: 'claude-3-5-haiku-latest', name: 'Claude 3.5 Haiku', supports_tools: false, supports_vision: false },
    ],
  },
  {
    vendor: 'google',
    display_name: 'Google Gemini',
    configured: false,
    is_active: false,
    selected_model: 'gemini-2.5-flash',
    capabilities: {
      streaming: false,
      structured_output: true,
      tool_calling: false,
      vision: false,
      local_only: false,
    },
    models: [
      { id: 'gemini-2.5-flash', name: 'Gemini 2.5 Flash', supports_tools: false, supports_vision: false },
      { id: 'gemini-2.5-pro', name: 'Gemini 2.5 Pro', supports_tools: false, supports_vision: false },
      { id: 'gemini-2.0-flash', name: 'Gemini 2.0 Flash', supports_tools: false, supports_vision: false },
    ],
  },
];

const VENDOR_KEY_PLACEHOLDERS: Record<BYOKVendorId, string> = {
  openai: 'sk-...',
  anthropic: 'sk-ant-...',
  google: 'AIza...',
};

const VENDOR_SHORT_LABELS: Record<BYOKVendorId, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic Claude',
  google: 'Google Gemini',
};

export const ProviderSettingsCard: React.FC<ProviderSettingsCardProps> = ({
  isBackendConnected,
}) => {
  const [activeConfig, setActiveConfig] = useState<ProfileProviderConfig | null>(null);
  const [vendors, setVendors] = useState<BYOKVendorStatus[]>(DEFAULT_VENDORS);
  const [editingVendor, setEditingVendor] = useState<BYOKVendorId>('openai');

  const [apiKeyInput, setApiKeyInput] = useState('');
  const [showApiKey, setShowApiKey] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [discoveredModels, setDiscoveredModels] = useState<
    Partial<Record<BYOKVendorId, AIProviderModelInfo[]>>
  >({});

  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [validating, setValidating] = useState(false);
  const [refreshingModels, setRefreshingModels] = useState(false);
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
      if (listRes.ok && listRes.data?.byok_vendors && listRes.data.byok_vendors.length > 0) {
        setVendors(listRes.data.byok_vendors);
      }
      if (activeRes.ok && activeRes.data) {
        const cfg = activeRes.data.profile_config;
        setActiveConfig(cfg);
        if (
          cfg.active_provider_id === 'byok' &&
          (cfg.byok_vendor === 'openai' || cfg.byok_vendor === 'anthropic' || cfg.byok_vendor === 'google')
        ) {
          setEditingVendor(cfg.byok_vendor);
        }
      }
    } finally {
      setLoading(false);
    }
  }, [isBackendConnected]);

  useEffect(() => {
    loadProviderState();
  }, [loadProviderState]);

  const selectedVendorStatus =
    vendors.find((v) => v.vendor === editingVendor) ||
    DEFAULT_VENDORS.find((v) => v.vendor === editingVendor) ||
    DEFAULT_VENDORS[0];

  const availableModels =
    discoveredModels[editingVendor] && discoveredModels[editingVendor]!.length > 0
      ? discoveredModels[editingVendor]!
      : selectedVendorStatus.models;

  const selectedModel = selectedVendorStatus.selected_model;

  const handleSelectEditingVendor = (vendor: BYOKVendorId) => {
    setEditingVendor(vendor);
    setApiKeyInput('');
    setShowApiKey(false);
    setConfirmRemove(false);
    setFeedback(null);
  };

  const handleSaveAndValidateKey = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = apiKeyInput.trim();
    if (!trimmed) {
      setFeedback({
        type: 'error',
        text: `Please enter a ${VENDOR_SHORT_LABELS[editingVendor]} API key.`,
      });
      return;
    }

    setFeedback(null);
    setSaving(true);
    try {
      const res = await apiClient.configureBYOKVendor(editingVendor, {
        api_key: trimmed,
        model: selectedModel,
        validate_connection: true,
      });
      if (res.ok && res.data) {
        setApiKeyInput('');
        setShowApiKey(false);
        setActiveConfig(res.data.profile_config);
        await loadProviderState();
        const isCurrentlyActive =
          res.data.profile_config.active_provider_id === 'byok' &&
          res.data.profile_config.byok_vendor === editingVendor;
        setFeedback({
          type: 'success',
          text: isCurrentlyActive
            ? `${VENDOR_SHORT_LABELS[editingVendor]} API key validated and encrypted with Windows DPAPI.`
            : `${VENDOR_SHORT_LABELS[editingVendor]} API key validated and encrypted with Windows DPAPI. Your current active provider was not changed.`,
        });
      } else {
        setFeedback({
          type: 'error',
          text: res.error || `Could not validate or store ${VENDOR_SHORT_LABELS[editingVendor]} API key.`,
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
      const res = await apiClient.validateBYOKVendor(editingVendor, {
        model: selectedModel,
      });
      if (res.ok && res.data) {
        setFeedback({
          type: 'success',
          text: `${VENDOR_SHORT_LABELS[editingVendor]} connection verified (${res.data.health.active_model || selectedModel
            }${res.data.health.latency_ms ? `, ${Math.round(res.data.health.latency_ms)} ms` : ''}).`,
        });
      } else {
        setFeedback({
          type: 'error',
          text: res.error || `${VENDOR_SHORT_LABELS[editingVendor]} connection check failed.`,
        });
      }
    } finally {
      setValidating(false);
    }
  };

  const handleRefreshModels = async () => {
    setFeedback(null);
    setRefreshingModels(true);
    try {
      const res = await apiClient.getBYOKVendorModels(editingVendor, true);
      if (res.ok && res.data?.models) {
        setDiscoveredModels((prev) => ({
          ...prev,
          [editingVendor]: res.data!.models,
        }));
        setFeedback({
          type: 'success',
          text: `Loaded ${res.data.models.length} models from ${VENDOR_SHORT_LABELS[editingVendor]}.`,
        });
      } else {
        setFeedback({
          type: 'error',
          text: res.error || `Could not fetch models for ${VENDOR_SHORT_LABELS[editingVendor]}.`,
        });
      }
    } finally {
      setRefreshingModels(false);
    }
  };

  const handleModelChange = async (newModel: string) => {
    if (!isBackendConnected) return;
    setFeedback(null);
    const res = await apiClient.setBYOKVendorModel(editingVendor, newModel);
    if (res.ok && res.data) {
      setActiveConfig(res.data.profile_config);
      await loadProviderState();
      setFeedback({
        type: 'success',
        text: `${VENDOR_SHORT_LABELS[editingVendor]} model set to ${newModel}.`,
      });
    } else {
      setFeedback({
        type: 'error',
        text: res.error || `Failed to update ${VENDOR_SHORT_LABELS[editingVendor]} model.`,
      });
    }
  };

  const handleActivateLocal = async () => {
    setFeedback(null);
    setSwitching(true);
    try {
      const res = await apiClient.selectProvider('local');
      if (res.ok && res.data) {
        setActiveConfig(res.data.profile_config);
        await loadProviderState();
        setFeedback({
          type: 'success',
          text: `Switched active provider to Local AI (${res.data.profile_config.local_model}).`,
        });
      } else {
        setFeedback({
          type: 'error',
          text: res.error || 'Could not switch to Local AI.',
        });
      }
    } finally {
      setSwitching(false);
    }
  };

  const handleActivateVendor = async (vendor: BYOKVendorId) => {
    setFeedback(null);
    setSwitching(true);
    try {
      const vStatus = vendors.find((v) => v.vendor === vendor);
      const res = await apiClient.selectProvider('byok', {
        byok_vendor: vendor,
        byok_model: vStatus?.selected_model,
      });
      if (res.ok && res.data) {
        setActiveConfig(res.data.profile_config);
        await loadProviderState();
        setFeedback({
          type: 'success',
          text: `Switched active provider to ${VENDOR_SHORT_LABELS[vendor]} (${res.data.profile_config.byok_model || vStatus?.selected_model
            }).`,
        });
      } else {
        setFeedback({
          type: 'error',
          text: res.error || `Could not switch to ${VENDOR_SHORT_LABELS[vendor]}.`,
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
      const res = await apiClient.removeBYOKVendorCredentials(editingVendor);
      setConfirmRemove(false);
      setApiKeyInput('');
      setShowApiKey(false);
      if (res.ok && res.data) {
        setActiveConfig(res.data.profile_config);
        await loadProviderState();
        setFeedback({
          type: 'success',
          text: `${VENDOR_SHORT_LABELS[editingVendor]} API key removed.${res.data.profile_config.active_provider_id === 'local'
            ? ' Local AI (Ollama) is active.'
            : ''
            }`,
        });
      } else {
        setFeedback({
          type: 'error',
          text: res.error || `Could not remove ${VENDOR_SHORT_LABELS[editingVendor]} API key.`,
        });
      }
    } finally {
      setSaving(false);
    }
  };

  const currentProviderId = activeConfig?.active_provider_id || 'local';
  const activeVendorId =
    currentProviderId === 'byok'
      ? ((activeConfig?.byok_vendor as BYOKVendorId | undefined) || 'openai')
      : null;

  const aiCore = useOptionalAICore();

  return (
    <section
      className="settings-card vp-provider-stack"
      data-testid="provider-settings-card"
    >
      <div className="vp-provider-section-header">
        <div className="settings-card-title-row">
          <div className="vp-page-icon-badge" style={{ width: 38, height: 38 }}>
            <KeyRound size={18} />
          </div>
          <div>
            <h2 className="vp-provider-title">
              AI Providers &amp; BYOK Credentials
            </h2>
            <p className="vp-provider-subtext">
              Local AI (Ollama) is the default. Optionally connect OpenAI, Anthropic Claude, or Google Gemini with Windows DPAPI encryption.
            </p>
          </div>
        </div>

        <StatusPill
          variant={currentProviderId === 'byok' ? 'success' : 'accent'}
          icon={<Cpu size={13} />}
          testId="provider-active-pill"
        >
          Active:{' '}
          {currentProviderId === 'byok' && activeVendorId
            ? `${VENDOR_SHORT_LABELS[activeVendorId] || activeVendorId} (${activeConfig?.byok_model || selectedModel
            })`
            : `Local (${activeConfig?.local_model || 'qwen3:4b'})`}
        </StatusPill>
      </div>

      {/* Privacy Notice */}
      <div className="memory-privacy-notice">
        <ShieldCheck size={16} />
        <span>
          API keys are encrypted locally with Windows DPAPI per profile and never stored in plaintext or browser storage.
          Prompts and relevant context are sent to an external provider only when you explicitly activate it.
        </span>
      </div>

      {/* Five-Mode Provider Selection Grid */}
      <div
        className="vp-provider-selector-grid"
        role="group"
        aria-label="AI Providers"
      >
        {/* Local Ollama */}
        <button
          type="button"
          disabled={!isBackendConnected || switching || loading}
          onClick={async () => {
            await handleActivateLocal();
            void aiCore?.refreshActiveProvider();
          }}
          className={`vp-provider-option-btn ${currentProviderId === 'local' ? 'is-selected' : ''
            }`}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>Local Ollama</span>
            {currentProviderId === 'local' && (
              <StatusPill variant="accent">Active</StatusPill>
            )}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Default • {activeConfig?.local_model || 'qwen3:4b'}
          </div>
        </button>

        {/* BYOK Vendor Cards: OpenAI, Anthropic, Gemini */}
        {vendors.map((v) => {
          const isEditing = editingVendor === v.vendor;
          return (
            <button
              key={v.vendor}
              type="button"
              onClick={() => handleSelectEditingVendor(v.vendor)}
              className={`vp-provider-option-btn ${v.is_active || isEditing ? 'is-selected' : ''
                }`}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>
                  {VENDOR_SHORT_LABELS[v.vendor]}
                </span>
                {v.is_active ? (
                  <StatusPill variant="success">Active</StatusPill>
                ) : v.configured ? (
                  <StatusPill variant="neutral">Ready</StatusPill>
                ) : null}
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: 'var(--text-secondary)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {v.configured ? v.selected_model : 'Not configured'}
              </div>
            </button>
          );
        })}

        {/* VoxPilot Cloud Placeholder */}
        <div
          aria-disabled="true"
          className="vp-provider-option-btn"
          style={{
            borderStyle: 'dashed',
            opacity: 0.6,
            cursor: 'not-allowed',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <Cloud size={14} />
            <span style={{ fontSize: 13, fontWeight: 600 }}>VoxPilot Cloud</span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            Coming soon
          </div>
        </div>
      </div>

      {/* Selected BYOK Vendor Configuration Panel */}
      <form
        onSubmit={async (e) => {
          await handleSaveAndValidateKey(e);
          void aiCore?.refreshActiveProvider();
        }}
        className="vp-provider-section-card"
      >
        <div className="vp-provider-section-header">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', flexWrap: 'wrap' }}>
              <Lock size={14} />
              <span>{selectedVendorStatus.display_name} Configuration</span>
              {selectedVendorStatus.configured ? (
                <StatusPill variant="success" icon={<CheckCircle2 size={12} />}>
                  Encrypted key stored
                </StatusPill>
              ) : (
                <StatusPill variant="neutral">Not configured</StatusPill>
              )}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
              <Sparkles size={13} />
              <span>
                Capabilities: Text generation
                {selectedVendorStatus.capabilities.structured_output
                  ? ' • Structured JSON'
                  : ' • Native JSON mode unsupported'}
                {' • Vision & native tool calling unsupported'}
              </span>
            </div>
          </div>

          <div className="vp-provider-actions-row">
            {selectedVendorStatus.configured && !selectedVendorStatus.is_active && (
              <button
                type="button"
                onClick={async () => {
                  await handleActivateVendor(editingVendor);
                  void aiCore?.refreshActiveProvider();
                }}
                disabled={!isBackendConnected || switching || saving}
                className="vp-btn-primary"
              >
                Use {VENDOR_SHORT_LABELS[editingVendor]}
              </button>
            )}

            {selectedVendorStatus.configured && !confirmRemove && (
              <button
                type="button"
                onClick={() => setConfirmRemove(true)}
                disabled={saving}
                className="vp-btn-danger"
              >
                <Trash2 size={13} />
                <span>Remove Key</span>
              </button>
            )}
          </div>
        </div>

        {confirmRemove && (
          <div className="settings-alert warning" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
            <span>
              Delete the DPAPI-encrypted {VENDOR_SHORT_LABELS[editingVendor]} key for this profile?
            </span>
            <div className="vp-provider-actions-row">
              <button
                type="button"
                onClick={async () => {
                  await handleRemoveKey();
                  void aiCore?.refreshActiveProvider();
                }}
                disabled={saving}
                className="vp-btn-danger"
              >
                Confirm Remove
              </button>
              <button
                type="button"
                onClick={() => setConfirmRemove(false)}
                className="vp-btn-secondary"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        <div className="vp-provider-form-grid">
          <div className="vp-provider-field">
            <label htmlFor={`byok-key-${editingVendor}`}>
              {selectedVendorStatus.configured
                ? `Replace ${VENDOR_SHORT_LABELS[editingVendor]} API Key`
                : `${VENDOR_SHORT_LABELS[editingVendor]} API Key`}
            </label>
            <div style={{ position: 'relative' }}>
              <input
                id={`byok-key-${editingVendor}`}
                type={showApiKey ? 'text' : 'password'}
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                placeholder={
                  selectedVendorStatus.configured
                    ? '•••••••••••••••••••• (stored)'
                    : VENDOR_KEY_PLACEHOLDERS[editingVendor]
                }
                autoComplete="off"
                spellCheck={false}
                disabled={!isBackendConnected || saving}
                className="vp-provider-input"
                style={{ paddingRight: 36 }}
              />
              <button
                type="button"
                onClick={() => setShowApiKey((v) => !v)}
                aria-label={showApiKey ? 'Hide API key' : 'Show API key'}
                style={{
                  position: 'absolute',
                  right: 8,
                  top: '50%',
                  transform: 'translateY(-50%)',
                  color: 'var(--text-muted)',
                  display: 'inline-flex',
                }}
              >
                {showApiKey ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>

          <div className="vp-provider-field">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
              <label htmlFor={`byok-model-${editingVendor}`}>
                {VENDOR_SHORT_LABELS[editingVendor]} Model
              </label>
              {selectedVendorStatus.configured && (
                <button
                  type="button"
                  onClick={handleRefreshModels}
                  disabled={!isBackendConnected || refreshingModels || saving}
                  style={{
                    fontSize: 11.5,
                    color: 'var(--accent-blue)',
                    fontWeight: 600,
                  }}
                >
                  {refreshingModels ? 'Refreshing...' : 'Refresh from API'}
                </button>
              )}
            </div>
            <select
              id={`byok-model-${editingVendor}`}
              value={selectedModel}
              onChange={(e) => handleModelChange(e.target.value)}
              disabled={!isBackendConnected || saving}
              className="vp-provider-input"
            >
              {availableModels.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name} ({m.id})
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="vp-provider-actions-row">
          <button
            type="submit"
            disabled={!isBackendConnected || saving || !apiKeyInput.trim()}
            className="vp-btn-primary"
          >
            <KeyRound size={14} />
            <span>{saving ? 'Validating & Saving...' : 'Save & Validate Key'}</span>
          </button>

          {selectedVendorStatus.configured && (
            <button
              type="button"
              onClick={handleValidateStoredConnection}
              disabled={!isBackendConnected || validating || saving}
              className="vp-btn-secondary"
            >
              <RefreshCw size={14} className={validating ? 'animate-spin' : ''} />
              <span>{validating ? 'Testing...' : 'Validate Connection'}</span>
            </button>
          )}

          {apiKeyInput && (
            <button
              type="button"
              onClick={() => {
                setApiKeyInput('');
                setShowApiKey(false);
              }}
              className="vp-btn-secondary"
            >
              Clear Input
            </button>
          )}
        </div>
      </form>

      {feedback && (
        <div
          role="status"
          className={`settings-alert ${feedback.type === 'success' ? 'success' : 'error'}`}
        >
          {feedback.type === 'success' ? (
            <CheckCircle2 size={16} />
          ) : (
            <AlertTriangle size={16} />
          )}
          <span>{feedback.text}</span>
        </div>
      )}
    </section>
  );
};

export default ProviderSettingsCard;
