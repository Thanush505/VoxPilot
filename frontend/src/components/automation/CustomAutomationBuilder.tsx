import React, { useState, useEffect, useCallback } from 'react';
import {
  Wand2,
  Play,
  Trash2,
  Plus,
  Clock,
  CheckCircle2,
  AlertTriangle,
  Square,
  Save,
  Pause,
} from 'lucide-react';
import apiClient from '../../api/client';
import type {
  AutomationExecutionStatus,
  AutomationPreviewResponse,
  AutomationStep,
  AutomationTriggerConfig,
  AutomationTriggerType,
  CustomAutomation,
} from '../../api/types';

interface CustomAutomationBuilderProps {
  isBackendConnected: boolean;
}

const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export const CustomAutomationBuilder: React.FC<CustomAutomationBuilderProps> = ({
  isBackendConnected,
}) => {
  const [automations, setAutomations] = useState<CustomAutomation[]>([]);
  const [prompt, setPrompt] = useState('');
  const [preview, setPreview] = useState<AutomationPreviewResponse | null>(null);
  const [draftName, setDraftName] = useState('');
  const [draftTriggerType, setDraftTriggerType] = useState<AutomationTriggerType>('manual');
  const [draftSchedule, setDraftSchedule] = useState<AutomationTriggerConfig>({
    frequency: 'daily',
    time_of_day: '09:00',
    weekdays: [0],
    paused: false,
  });
  const [draftSteps, setDraftSteps] = useState<AutomationStep[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [runningId, setRunningId] = useState<string | null>(null);
  const [activeExec, setActiveExec] = useState<AutomationExecutionStatus | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const loadAutomations = useCallback(async () => {
    if (!isBackendConnected) return;
    const res = await apiClient.listAutomations();
    if (res.ok && res.data) {
      setAutomations(res.data.automations || []);
    }
  }, [isBackendConnected]);

  useEffect(() => {
    loadAutomations();
  }, [loadAutomations]);

  const handleGeneratePreview = async () => {
    if (!prompt.trim()) return;
    setFeedback(null);
    setIsGenerating(true);
    try {
      const res = await apiClient.previewAutomation(prompt.trim());
      if (res.ok && res.data) {
        setPreview(res.data);
        setDraftName(res.data.name);
        setDraftTriggerType(res.data.trigger_type);
        setDraftSchedule({
          frequency: res.data.trigger_config?.frequency || 'daily',
          time_of_day: res.data.trigger_config?.time_of_day || '09:00',
          weekdays: res.data.trigger_config?.weekdays || [0],
          paused: Boolean(res.data.trigger_config?.paused),
        });
        setDraftSteps(res.data.steps || []);
      } else {
        setFeedback({ type: 'error', text: res.error || 'Could not generate automation preview.' });
      }
    } finally {
      setIsGenerating(false);
    }
  };

  const handleSaveAutomation = async () => {
    if (!draftName.trim() || draftSteps.length === 0) {
      setFeedback({ type: 'error', text: 'Provide a name and at least one validated step.' });
      return;
    }
    setFeedback(null);
    setIsSaving(true);
    try {
      const res = await apiClient.createAutomation({
        name: draftName.trim(),
        description: preview?.description || prompt.trim(),
        trigger_type: draftTriggerType,
        trigger_config: draftTriggerType === 'scheduled' ? draftSchedule : {},
        steps: draftSteps,
        enabled: true,
      });
      if (res.ok && res.data) {
        setPreview(null);
        setPrompt('');
        setDraftSteps([]);
        setFeedback({ type: 'success', text: `Saved automation "${res.data.name}".` });
        await loadAutomations();
      } else {
        setFeedback({ type: 'error', text: res.error || 'Failed to save automation.' });
      }
    } finally {
      setIsSaving(false);
    }
  };

  const handleRunAutomation = async (automationId: string) => {
    setFeedback(null);
    setRunningId(automationId);
    try {
      const res = await apiClient.executeAutomation(automationId);
      if (res.ok && res.data) {
        setActiveExec(res.data);
        await loadAutomations();
      } else {
        setFeedback({ type: 'error', text: res.error || 'Automation execution failed.' });
      }
    } finally {
      setRunningId(null);
    }
  };

  const handleCancelExecution = async () => {
    if (!activeExec) return;
    const res = await apiClient.cancelAutomationExecution(activeExec.execution_id);
    if (res.ok && res.data) {
      setActiveExec(res.data);
      await loadAutomations();
    }
  };

  const handleToggleEnabled = async (item: CustomAutomation) => {
    const res = await apiClient.toggleAutomation(item.automation_id, !item.enabled);
    if (res.ok) {
      await loadAutomations();
    }
  };

  const handleToggleSchedulePause = async (item: CustomAutomation) => {
    const res = await apiClient.updateAutomationSchedule(item.automation_id, {
      ...item.trigger_config,
      paused: !item.trigger_config?.paused,
    });
    if (res.ok) {
      await loadAutomations();
    }
  };

  const handleDelete = async (automationId: string) => {
    const res = await apiClient.deleteAutomation(automationId);
    if (res.ok) {
      setConfirmDeleteId(null);
      setFeedback({ type: 'success', text: 'Automation deleted.' });
      await loadAutomations();
    }
  };

  const handleRemoveDraftStep = (idx: number) => {
    setDraftSteps((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleAddSafeCheckStep = () => {
    setDraftSteps((prev) => [
      ...prev,
      { action: 'get_system_info', parameters: {}, description: 'Check system readiness' },
    ]);
  };

  return (
    <div className="automation-section" data-testid="custom-automation-builder">
      <div className="section-title-row">
        <div className="section-title-group">
          <Wand2 size={18} className="text-primary" />
          <h2 className="section-title">Custom Automation Builder</h2>
        </div>
        <span className="status-pill status-completed">
          {automations.length} Saved {automations.length === 1 ? 'Workflow' : 'Workflows'}
        </span>
      </div>

      {/* Natural Language Prompt Input */}
      <div className="settings-card" style={{ marginBottom: '1rem' }}>
        <p className="settings-help-text" style={{ marginBottom: '0.5rem' }}>
          Describe a workflow in plain English (e.g., &ldquo;Create an automation that opens Chrome every morning at 9 AM&rdquo;). Preview and approve before saving.
        </p>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <input
            type="text"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Describe your automation workflow..."
            disabled={!isBackendConnected || isGenerating}
            data-testid="automation-nl-input"
            style={{ flex: 1, minWidth: '240px', padding: '0.55rem 0.75rem', borderRadius: '8px' }}
          />
          <button
            type="button"
            className="refresh-btn"
            onClick={handleGeneratePreview}
            disabled={!isBackendConnected || isGenerating || !prompt.trim()}
            data-testid="automation-preview-btn"
          >
            <Wand2 size={14} />
            <span>{isGenerating ? 'Building Preview...' : 'Generate Preview'}</span>
          </button>
        </div>

        {/* Preview & Edit Panel */}
        {preview && (
          <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px solid var(--border-color)' }} data-testid="automation-preview-panel">
            <div style={{ display: 'grid', gap: '0.75rem' }}>
              <div>
                <label style={{ fontSize: '0.82rem', fontWeight: 600 }}>Automation Name</label>
                <input
                  type="text"
                  value={draftName}
                  onChange={(e) => setDraftName(e.target.value)}
                  data-testid="automation-draft-name"
                  style={{ width: '100%', padding: '0.45rem 0.65rem', marginTop: '0.25rem', borderRadius: '6px' }}
                />
              </div>

              <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
                <div>
                  <label style={{ fontSize: '0.82rem', fontWeight: 600, marginRight: '0.5rem' }}>Trigger</label>
                  <select
                    value={draftTriggerType}
                    onChange={(e) => setDraftTriggerType(e.target.value as AutomationTriggerType)}
                    data-testid="automation-trigger-select"
                    style={{ padding: '0.4rem 0.6rem', borderRadius: '6px' }}
                  >
                    <option value="manual">Manual</option>
                    <option value="scheduled">Scheduled (While App Running)</option>
                    <option value="user_requested">User Requested</option>
                  </select>
                </div>

                {draftTriggerType === 'scheduled' && (
                  <>
                    <select
                      value={draftSchedule.frequency || 'daily'}
                      onChange={(e) =>
                        setDraftSchedule((s) => ({ ...s, frequency: e.target.value as 'daily' | 'weekly' }))
                      }
                      style={{ padding: '0.4rem 0.6rem', borderRadius: '6px' }}
                    >
                      <option value="daily">Daily</option>
                      <option value="weekly">Weekly</option>
                    </select>
                    <input
                      type="time"
                      value={draftSchedule.time_of_day || '09:00'}
                      onChange={(e) => setDraftSchedule((s) => ({ ...s, time_of_day: e.target.value }))}
                      style={{ padding: '0.35rem 0.5rem', borderRadius: '6px' }}
                    />
                    {draftSchedule.frequency === 'weekly' && (
                      <div style={{ display: 'flex', gap: '0.25rem' }}>
                        {WEEKDAY_LABELS.map((lbl, idx) => {
                          const active = (draftSchedule.weekdays || []).includes(idx);
                          return (
                            <button
                              key={lbl}
                              type="button"
                              className="refresh-btn"
                              style={{ opacity: active ? 1 : 0.5, padding: '0.25rem 0.45rem' }}
                              onClick={() =>
                                setDraftSchedule((s) => {
                                  const curr = s.weekdays || [];
                                  const next = curr.includes(idx)
                                    ? curr.filter((d) => d !== idx)
                                    : [...curr, idx];
                                  return { ...s, weekdays: next.length ? next : [0] };
                                })
                              }
                            >
                              {lbl}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </>
                )}
              </div>

              {(preview.warnings?.length > 0 || preview.clarification_needed) && (
                <div className="settings-alert warning">
                  <AlertTriangle size={15} />
                  <span>{preview.warnings?.join(' ') || 'Clarification needed before saving.'}</span>
                </div>
              )}

              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.35rem' }}>
                  <strong style={{ fontSize: '0.85rem' }}>Ordered Action Steps ({draftSteps.length})</strong>
                  <button type="button" className="refresh-btn" onClick={handleAddSafeCheckStep}>
                    <Plus size={13} />
                    <span>Add System Check</span>
                  </button>
                </div>
                <div style={{ display: 'grid', gap: '0.35rem' }}>
                  {draftSteps.map((step, idx) => (
                    <div
                      key={`${step.action}-${idx}`}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        padding: '0.45rem 0.65rem',
                        borderRadius: '6px',
                        border: '1px solid var(--border-color)',
                      }}
                    >
                      <div>
                        <strong>{idx + 1}. {step.action}</strong>{' '}
                        <span className="text-xs text-muted">{step.description}</span>
                      </div>
                      <button
                        type="button"
                        className="refresh-btn"
                        onClick={() => handleRemoveDraftStep(idx)}
                        aria-label={`Remove step ${idx + 1}`}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <button
                  type="button"
                  className="refresh-btn"
                  onClick={handleSaveAutomation}
                  disabled={isSaving || draftSteps.length === 0 || !draftName.trim()}
                  data-testid="automation-save-btn"
                >
                  <Save size={14} />
                  <span>{isSaving ? 'Saving...' : 'Approve & Save Automation'}</span>
                </button>
                <button type="button" className="refresh-btn" onClick={() => setPreview(null)}>
                  Discard Preview
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {feedback && (
        <div className={`settings-alert ${feedback.type === 'success' ? 'success' : 'error'}`} style={{ marginBottom: '0.75rem' }}>
          {feedback.type === 'success' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
          <span>{feedback.text}</span>
        </div>
      )}

      {/* Active / Latest Execution Result */}
      {activeExec && (
        <div className="settings-card" style={{ marginBottom: '1rem' }} data-testid="automation-execution-panel">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
            <strong>Execution: {activeExec.automation_name} ({activeExec.status})</strong>
            {activeExec.status === 'running' && (
              <button type="button" className="refresh-btn" onClick={handleCancelExecution}>
                <Square size={13} />
                <span>Cancel</span>
              </button>
            )}
          </div>
          {activeExec.error && <p className="text-xs" style={{ color: 'var(--color-danger)' }}>{activeExec.error}</p>}
          <div style={{ display: 'grid', gap: '0.25rem' }}>
            {activeExec.steps.map((st) => (
              <div key={st.step_id} className="text-xs">
                Step {st.step_index + 1} ({st.action}): <b>{st.status}</b> {st.message ? `— ${st.message}` : ''}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Saved Automations List */}
      {automations.length > 0 && (
        <div style={{ display: 'grid', gap: '0.65rem' }} data-testid="saved-automations-list">
          {automations.map((item) => (
            <div
              key={item.automation_id}
              className="settings-card"
              style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.75rem' }}
            >
              <div>
                <div style={{ fontWeight: 600 }}>
                  {item.name}{' '}
                  <span className={`status-pill ${item.enabled ? 'status-completed' : 'status-cancelled'}`}>
                    {item.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </div>
                <div className="text-xs text-muted">
                  Trigger: {item.trigger_type}
                  {item.trigger_type === 'scheduled' && item.trigger_config?.time_of_day
                    ? ` (${item.trigger_config.frequency || 'daily'} at ${item.trigger_config.time_of_day}${item.trigger_config.paused ? ', paused' : ''})`
                    : ''}{' '}
                  &bull; {item.steps.length} step(s)
                  {item.last_execution_status ? ` • Last: ${item.last_execution_status}` : ''}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '0.45rem', flexWrap: 'wrap' }}>
                <button
                  type="button"
                  className="refresh-btn"
                  disabled={!isBackendConnected || runningId === item.automation_id}
                  onClick={() => handleRunAutomation(item.automation_id)}
                  data-testid={`run-automation-${item.automation_id}`}
                >
                  <Play size={13} />
                  <span>{runningId === item.automation_id ? 'Running...' : 'Run'}</span>
                </button>
                <button type="button" className="refresh-btn" onClick={() => handleToggleEnabled(item)}>
                  <span>{item.enabled ? 'Disable' : 'Enable'}</span>
                </button>
                {item.trigger_type === 'scheduled' && (
                  <button type="button" className="refresh-btn" onClick={() => handleToggleSchedulePause(item)}>
                    {item.trigger_config?.paused ? <Clock size={13} /> : <Pause size={13} />}
                    <span>{item.trigger_config?.paused ? 'Resume Schedule' : 'Pause Schedule'}</span>
                  </button>
                )}
                {confirmDeleteId === item.automation_id ? (
                  <button
                    type="button"
                    className="refresh-btn"
                    onClick={() => handleDelete(item.automation_id)}
                    data-testid={`confirm-delete-${item.automation_id}`}
                  >
                    <span>Confirm Delete</span>
                  </button>
                ) : (
                  <button
                    type="button"
                    className="refresh-btn"
                    onClick={() => setConfirmDeleteId(item.automation_id)}
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default CustomAutomationBuilder;
