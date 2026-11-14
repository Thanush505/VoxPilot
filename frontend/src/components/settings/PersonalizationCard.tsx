import React, { useState, useEffect, useCallback } from 'react';
import {
  Sliders,
  Plus,
  RefreshCw,
  ShieldCheck,
  AlertTriangle,
  Trash2,
  Edit3,
  Check,
  X,
  Database,
} from 'lucide-react';
import apiClient from '../../api/client';
import { PreferenceCategory, PreferenceItem } from '../../api/types';

interface PersonalizationCardProps {
  isBackendConnected: boolean;
}

const PREFERENCE_CATEGORY_LABELS: Record<string, string> = {
  communication: 'Communication',
  response_format: 'Response Format',
  technical: 'Technical',
  workflow: 'Workflow',
  ai_behavior: 'AI Behavior',
  interface: 'Interface',
};

const PREFERENCE_CATEGORIES: PreferenceCategory[] = [
  'communication',
  'response_format',
  'technical',
  'workflow',
  'ai_behavior',
  'interface',
];

const formatTimestamp = (iso?: string | null): string => {
  if (!iso) return 'Unknown date';
  try {
    const dt = new Date(iso);
    if (Number.isNaN(dt.getTime())) return iso;
    return dt.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
};

export const PersonalizationCard: React.FC<PersonalizationCardProps> = ({
  isBackendConnected,
}) => {
  const [preferences, setPreferences] = useState<PreferenceItem[]>([]);
  const [totalCount, setTotalCount] = useState<number>(0);
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [enabledOnly, setEnabledOnly] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [statusNotice, setStatusNotice] = useState<string | null>(null);

  // Add / Edit form state
  const [showForm, setShowForm] = useState<boolean>(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formCategory, setFormCategory] = useState<PreferenceCategory>('communication');
  const [formKey, setFormKey] = useState<string>('');
  const [formValue, setFormValue] = useState<string>('');
  const [formDescription, setFormDescription] = useState<string>('');
  const [formPriority, setFormPriority] = useState<number>(3);
  const [formEnabled, setFormEnabled] = useState<boolean>(true);
  const [isSaving, setIsSaving] = useState<boolean>(false);

  // Toggle / Delete state
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const loadPreferences = useCallback(async () => {
    if (!isBackendConnected) return;
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const res = await apiClient.getPreferences({
        category: selectedCategory !== 'all' ? selectedCategory : undefined,
        enabled_only: enabledOnly,
        limit: 100,
      });
      if (res.ok && res.data) {
        setPreferences(res.data.preferences || []);
        setTotalCount(res.data.total ?? res.data.preferences.length);
      } else {
        setErrorMsg(res.error || 'Failed to load personalization preferences.');
      }
    } catch (err: unknown) {
      setErrorMsg(
        err instanceof Error ? err.message : 'Unexpected error loading preferences.'
      );
    } finally {
      setIsLoading(false);
    }
  }, [isBackendConnected, selectedCategory, enabledOnly]);

  useEffect(() => {
    loadPreferences();
  }, [loadPreferences]);

  const resetForm = () => {
    setEditingId(null);
    setFormCategory('communication');
    setFormKey('');
    setFormValue('');
    setFormDescription('');
    setFormPriority(3);
    setFormEnabled(true);
    setShowForm(false);
  };

  const openAddForm = () => {
    resetForm();
    setShowForm(true);
    setErrorMsg(null);
    setStatusNotice(null);
  };

  const openEditForm = (pref: PreferenceItem) => {
    setEditingId(pref.id);
    setFormCategory(
      (PREFERENCE_CATEGORIES.includes(pref.category as PreferenceCategory)
        ? pref.category
        : 'communication') as PreferenceCategory
    );
    setFormKey(pref.key);
    setFormValue(pref.value);
    setFormDescription(pref.description || '');
    setFormPriority(pref.priority || 3);
    setFormEnabled(pref.enabled);
    setShowForm(true);
    setErrorMsg(null);
    setStatusNotice(null);
  };

  const handleSavePreference = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanKey = formKey.trim();
    const cleanValue = formValue.trim();
    if (!cleanKey || !cleanValue) {
      setErrorMsg('Both preference key and value are required.');
      return;
    }

    setIsSaving(true);
    setErrorMsg(null);
    setStatusNotice(null);
    try {
      if (editingId) {
        const res = await apiClient.updatePreference(editingId, {
          category: formCategory,
          key: cleanKey,
          value: cleanValue,
          description: formDescription.trim() || null,
          priority: formPriority,
          enabled: formEnabled,
        });
        if (res.ok && res.data) {
          setStatusNotice(`Updated preference "${res.data.key}".`);
          resetForm();
          await loadPreferences();
        } else {
          setErrorMsg(res.error || 'Failed to update preference.');
        }
      } else {
        const res = await apiClient.createPreference({
          category: formCategory,
          key: cleanKey,
          value: cleanValue,
          description: formDescription.trim() || null,
          priority: formPriority,
          enabled: formEnabled,
        });
        if (res.ok && res.data) {
          setStatusNotice(`Saved preference "${res.data.key}" locally.`);
          resetForm();
          await loadPreferences();
        } else {
          setErrorMsg(res.error || 'Failed to save preference.');
        }
      }
    } catch (err: unknown) {
      setErrorMsg(
        err instanceof Error ? err.message : 'Unexpected error saving preference.'
      );
    } finally {
      setIsSaving(false);
    }
  };

  const handleToggleEnabled = async (pref: PreferenceItem) => {
    setTogglingId(pref.id);
    setErrorMsg(null);
    setStatusNotice(null);
    try {
      const res = await apiClient.updatePreference(pref.id, {
        enabled: !pref.enabled,
      });
      if (res.ok && res.data) {
        setPreferences((prev) =>
          prev.map((item) => (item.id === pref.id ? res.data! : item))
        );
        setStatusNotice(
          `Preference "${pref.key}" ${res.data.enabled ? 'enabled' : 'disabled'}.`
        );
      } else {
        setErrorMsg(res.error || 'Failed to update preference status.');
      }
    } catch (err: unknown) {
      setErrorMsg(
        err instanceof Error ? err.message : 'Unexpected error updating status.'
      );
    } finally {
      setTogglingId(null);
    }
  };

  const handleConfirmDelete = async (preferenceId: string) => {
    setDeletingId(preferenceId);
    setErrorMsg(null);
    setStatusNotice(null);
    try {
      const res = await apiClient.deletePreference(preferenceId);
      if (res.ok) {
        setPreferences((prev) => prev.filter((item) => item.id !== preferenceId));
        setTotalCount((prev) => Math.max(0, prev - 1));
        setConfirmDeleteId(null);
        setStatusNotice('Preference deleted from local storage.');
      } else {
        setErrorMsg(res.error || 'Failed to delete preference.');
      }
    } catch (err: unknown) {
      setErrorMsg(
        err instanceof Error ? err.message : 'Unexpected error deleting preference.'
      );
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="settings-card memory-management-card" data-testid="personalization-card">
      <div className="settings-card-header">
        <div className="settings-card-title-row">
          <Sliders size={18} className="settings-icon" />
          <div>
            <h3>Personalization &amp; Preferences</h3>
            <p className="settings-card-subtitle">
              Explicit rules and preferences that guide VoxPilot responses and behavior
            </p>
          </div>
        </div>
        <div className="memory-header-actions">
          <button
            type="button"
            className="btn-secondary btn-sm"
            onClick={loadPreferences}
            disabled={!isBackendConnected || isLoading}
            title="Refresh preferences"
          >
            <RefreshCw size={14} className={isLoading ? 'spin' : ''} />
            <span>Refresh</span>
          </button>
          <button
            type="button"
            className="btn-primary btn-sm"
            onClick={() => (showForm ? resetForm() : openAddForm())}
            disabled={!isBackendConnected}
            data-testid="add-preference-btn"
          >
            {showForm ? <X size={14} /> : <Plus size={14} />}
            <span>{showForm ? 'Close' : 'Add Preference'}</span>
          </button>
        </div>
      </div>

      <div className="settings-card-body">
        <div className="memory-privacy-notice">
          <ShieldCheck size={15} />
          <span>
            Stored <strong>100% locally in SQLite</strong> — no cloud sync. Sensitive secrets
            (API keys, passwords, tokens) are automatically blocked.
          </span>
        </div>

        <div className="memory-toolbar">
          <select
            className="status-filter-select"
            value={selectedCategory}
            onChange={(e) => setSelectedCategory(e.target.value)}
            disabled={!isBackendConnected}
            aria-label="Filter preferences by category"
          >
            <option value="all">All Categories</option>
            {PREFERENCE_CATEGORIES.map((cat) => (
              <option key={cat} value={cat}>
                {PREFERENCE_CATEGORY_LABELS[cat]}
              </option>
            ))}
          </select>

          <label className="settings-toggle-label">
            <input
              type="checkbox"
              checked={enabledOnly}
              onChange={(e) => setEnabledOnly(e.target.checked)}
              disabled={!isBackendConnected}
            />
            <span>Active only</span>
          </label>

          <div className="memory-count-badge" title="Total stored preferences">
            <Database size={13} />
            <span>{totalCount} stored</span>
          </div>
        </div>

        {showForm && (
          <form
            className="memory-add-form"
            onSubmit={handleSavePreference}
            data-testid="preference-form"
          >
            <div className="memory-add-row">
              <select
                className="status-filter-select"
                value={formCategory}
                onChange={(e) => setFormCategory(e.target.value as PreferenceCategory)}
                aria-label="Preference Category"
              >
                {PREFERENCE_CATEGORIES.map((cat) => (
                  <option key={cat} value={cat}>
                    {PREFERENCE_CATEGORY_LABELS[cat]}
                  </option>
                ))}
              </select>

              <input
                type="text"
                className="memory-add-input"
                placeholder="Key (e.g. response_style, coding_language)"
                value={formKey}
                onChange={(e) => setFormKey(e.target.value)}
                maxLength={100}
                aria-label="Preference Key"
              />

              <select
                className="status-filter-select"
                value={formPriority}
                onChange={(e) => setFormPriority(Number(e.target.value))}
                aria-label="Preference Priority"
              >
                <option value={5}>Priority 5 (Highest)</option>
                <option value={4}>Priority 4 (High)</option>
                <option value={3}>Priority 3 (Normal)</option>
                <option value={2}>Priority 2 (Low)</option>
                <option value={1}>Priority 1 (Lowest)</option>
              </select>
            </div>

            <div className="memory-add-row">
              <input
                type="text"
                className="memory-add-input"
                placeholder="Value (e.g. Concise bullet points, Python 3.12)"
                value={formValue}
                onChange={(e) => setFormValue(e.target.value)}
                maxLength={500}
                aria-label="Preference Value"
              />
            </div>

            <div className="memory-add-row">
              <input
                type="text"
                className="memory-add-input"
                placeholder="Optional note or context (e.g. Use for technical answers)"
                value={formDescription}
                onChange={(e) => setFormDescription(e.target.value)}
                maxLength={300}
                aria-label="Preference Description"
              />
              <label className="settings-toggle-label">
                <input
                  type="checkbox"
                  checked={formEnabled}
                  onChange={(e) => setFormEnabled(e.target.checked)}
                />
                <span>Enabled</span>
              </label>
              <button
                type="submit"
                className="btn-primary btn-sm"
                disabled={isSaving || !formKey.trim() || !formValue.trim()}
                data-testid="save-preference-btn"
              >
                <Check size={14} />
                <span>{isSaving ? 'Saving...' : editingId ? 'Update' : 'Save'}</span>
              </button>
              <button
                type="button"
                className="btn-secondary btn-sm"
                onClick={resetForm}
              >
                <span>Cancel</span>
              </button>
            </div>
          </form>
        )}

        {errorMsg && (
          <div className="settings-banner error" role="alert">
            <AlertTriangle size={15} />
            <span>{errorMsg}</span>
          </div>
        )}

        {statusNotice && !errorMsg && (
          <div className="settings-banner success" role="status">
            <ShieldCheck size={15} />
            <span>{statusNotice}</span>
          </div>
        )}

        {!isBackendConnected ? (
          <div className="memory-empty-state">
            Connect to the local VoxPilot backend to manage your preferences.
          </div>
        ) : isLoading && preferences.length === 0 ? (
          <div className="memory-empty-state">Loading local preferences...</div>
        ) : preferences.length === 0 ? (
          <div className="memory-empty-state">
            No preferences configured yet. Click &ldquo;Add Preference&rdquo; to customize
            VoxPilot&apos;s communication style, formatting, or technical defaults.
          </div>
        ) : (
          <div className="memory-list">
            {preferences.map((pref) => (
              <div
                key={pref.id}
                className="memory-item"
                data-testid={`preference-item-${pref.id}`}
                style={{ opacity: pref.enabled ? 1 : 0.65 }}
              >
                <div className="memory-item-main">
                  <div className="memory-item-meta">
                    <span className="memory-type-badge">
                      {PREFERENCE_CATEGORY_LABELS[pref.category] || pref.category}
                    </span>
                    <span className="memory-timestamp">
                      Priority {pref.priority} &middot;{' '}
                      {pref.enabled ? 'Active' : 'Disabled'} &middot;{' '}
                      {formatTimestamp(pref.updated_at || pref.created_at)}
                    </span>
                  </div>
                  <p className="memory-item-content">
                    <strong>{pref.key}:</strong> {pref.value}
                  </p>
                  {pref.description && (
                    <p className="settings-hint" style={{ marginTop: 4 }}>
                      {pref.description}
                    </p>
                  )}
                </div>

                <div className="memory-item-actions">
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => handleToggleEnabled(pref)}
                    disabled={togglingId === pref.id}
                    aria-label={
                      pref.enabled
                        ? `Disable preference ${pref.key}`
                        : `Enable preference ${pref.key}`
                    }
                  >
                    <span>{pref.enabled ? 'Disable' : 'Enable'}</span>
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => openEditForm(pref)}
                    aria-label={`Edit preference ${pref.key}`}
                  >
                    <Edit3 size={13} />
                    <span>Edit</span>
                  </button>
                  {confirmDeleteId === pref.id ? (
                    <div className="memory-confirm-delete">
                      <button
                        type="button"
                        className="btn-danger btn-sm"
                        onClick={() => handleConfirmDelete(pref.id)}
                        disabled={deletingId === pref.id}
                      >
                        {deletingId === pref.id ? 'Deleting...' : 'Confirm Delete'}
                      </button>
                      <button
                        type="button"
                        className="btn-secondary btn-sm"
                        onClick={() => setConfirmDeleteId(null)}
                        disabled={deletingId === pref.id}
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="btn-secondary btn-sm memory-delete-btn"
                      onClick={() => setConfirmDeleteId(pref.id)}
                      title="Delete this preference"
                      aria-label={`Delete preference ${pref.key}`}
                    >
                      <Trash2 size={14} />
                      <span>Delete</span>
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default PersonalizationCard;
