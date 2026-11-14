import React, { useState, useEffect, useCallback } from 'react';
import {
  History,
  RefreshCw,
  Trash2,
  RotateCcw,
  Square,
  ChevronDown,
  ChevronUp,
  AlertTriangle,
  CheckCircle2,
} from 'lucide-react';
import apiClient from '../../api/client';
import type {
  AutomationExecutionHistoryItem,
  AutomationExecutionStatus,
  CustomAutomation,
} from '../../api/types';

interface AutomationHistoryPanelProps {
  isBackendConnected: boolean;
}

const PAGE_SIZE = 10;

export const AutomationHistoryPanel: React.FC<AutomationHistoryPanelProps> = ({
  isBackendConnected,
}) => {
  const [items, setItems] = useState<AutomationExecutionHistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [automations, setAutomations] = useState<CustomAutomation[]>([]);
  const [filterAutomationId, setFilterAutomationId] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [filterTrigger, setFilterTrigger] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [expandedExec, setExpandedExec] = useState<AutomationExecutionStatus | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [retryConfirmItem, setRetryConfirmItem] = useState<AutomationExecutionHistoryItem | null>(null);

  const loadHistory = useCallback(async () => {
    if (!isBackendConnected) return;
    setLoading(true);
    setError(null);
    try {
      const [histRes, autoRes] = await Promise.all([
        apiClient.listAutomationHistory({
          automation_id: filterAutomationId || undefined,
          status: filterStatus || undefined,
          trigger_type: filterTrigger || undefined,
          date_from: dateFrom ? `${dateFrom}T00:00:00Z` : undefined,
          date_to: dateTo ? `${dateTo}T23:59:59Z` : undefined,
          limit: PAGE_SIZE,
          offset,
        }),
        apiClient.listAutomations(),
      ]);

      if (histRes.ok && histRes.data) {
        setItems(histRes.data.items || []);
        setTotal(histRes.data.total || 0);
      } else {
        setError(histRes.error || 'Failed to load automation history.');
      }
      if (autoRes.ok && autoRes.data) {
        setAutomations(autoRes.data.automations || []);
      }
    } finally {
      setLoading(false);
    }
  }, [isBackendConnected, filterAutomationId, filterStatus, filterTrigger, dateFrom, dateTo, offset]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const handleInspect = async (executionId: string) => {
    if (expandedExec?.execution_id === executionId) {
      setExpandedExec(null);
      return;
    }
    const res = await apiClient.getAutomationExecution(executionId);
    if (res.ok && res.data) {
      setExpandedExec(res.data);
    }
  };

  const handleCancel = async (executionId: string) => {
    const res = await apiClient.cancelAutomationExecution(executionId);
    if (res.ok) {
      setFeedback({ type: 'success', text: 'Execution cancelled.' });
      await loadHistory();
    } else {
      setFeedback({ type: 'error', text: res.error || 'Failed to cancel execution.' });
    }
  };

  const handleRetry = async (item: AutomationExecutionHistoryItem, allowRerun: boolean) => {
    setFeedback(null);
    if (item.completed_steps > 0 && !allowRerun) {
      setRetryConfirmItem(item);
      return;
    }
    const res = await apiClient.retryAutomationExecution(item.execution_id, allowRerun);
    setRetryConfirmItem(null);
    if (res.ok && res.data) {
      setFeedback({ type: 'success', text: `Rerun started (${res.data.status}).` });
      setExpandedExec(res.data);
      await loadHistory();
    } else {
      setFeedback({ type: 'error', text: res.error || 'Could not retry execution.' });
    }
  };

  const handleDeleteOne = async (executionId: string) => {
    const res = await apiClient.deleteAutomationHistory(executionId);
    if (res.ok) {
      if (expandedExec?.execution_id === executionId) {
        setExpandedExec(null);
      }
      setFeedback({ type: 'success', text: 'History record deleted.' });
      await loadHistory();
    } else {
      setFeedback({ type: 'error', text: res.error || 'Failed to delete history record.' });
    }
  };

  const handleClearAll = async () => {
    const res = await apiClient.clearAutomationHistory(true);
    setConfirmClear(false);
    if (res.ok && res.data) {
      setExpandedExec(null);
      setOffset(0);
      setFeedback({
        type: 'success',
        text: `Cleared ${res.data.deleted_count} history record(s) for this profile.`,
      });
      await loadHistory();
    } else {
      setFeedback({ type: 'error', text: res.error || 'Failed to clear history.' });
    }
  };

  return (
    <div className="automation-section" data-testid="automation-history-panel">
      <div className="section-title-row">
        <div className="section-title-group">
          <History size={18} className="text-primary" />
          <h2 className="section-title">Automation Execution History</h2>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <button type="button" className="refresh-btn" onClick={loadHistory} disabled={loading}>
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            <span>Refresh</span>
          </button>
          {confirmClear ? (
            <button
              type="button"
              className="refresh-btn"
              onClick={handleClearAll}
              data-testid="confirm-clear-history-btn"
            >
              <span>Confirm Clear Profile History</span>
            </button>
          ) : (
            <button
              type="button"
              className="refresh-btn"
              onClick={() => setConfirmClear(true)}
              disabled={items.length === 0}
              data-testid="clear-history-btn"
            >
              <Trash2 size={13} />
              <span>Clear History</span>
            </button>
          )}
        </div>
      </div>

      {/* Filters */}
      <div className="settings-card" style={{ marginBottom: '0.75rem' }}>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <select
            value={filterAutomationId}
            onChange={(e) => {
              setOffset(0);
              setFilterAutomationId(e.target.value);
            }}
            data-testid="history-filter-automation"
            style={{ padding: '0.4rem 0.6rem', borderRadius: '6px' }}
          >
            <option value="">All Automations</option>
            {automations.map((a) => (
              <option key={a.automation_id} value={a.automation_id}>
                {a.name}
              </option>
            ))}
          </select>

          <select
            value={filterStatus}
            onChange={(e) => {
              setOffset(0);
              setFilterStatus(e.target.value);
            }}
            data-testid="history-filter-status"
            style={{ padding: '0.4rem 0.6rem', borderRadius: '6px' }}
          >
            <option value="">All Statuses</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
            <option value="confirmation_required">Confirmation Required</option>
            <option value="running">Running</option>
            <option value="interrupted">Interrupted</option>
          </select>

          <select
            value={filterTrigger}
            onChange={(e) => {
              setOffset(0);
              setFilterTrigger(e.target.value);
            }}
            data-testid="history-filter-trigger"
            style={{ padding: '0.4rem 0.6rem', borderRadius: '6px' }}
          >
            <option value="">All Triggers</option>
            <option value="manual">Manual</option>
            <option value="scheduled">Scheduled</option>
            <option value="retry">Retry</option>
          </select>

          <input
            type="date"
            value={dateFrom}
            onChange={(e) => {
              setOffset(0);
              setDateFrom(e.target.value);
            }}
            aria-label="Filter from date"
            style={{ padding: '0.35rem 0.5rem', borderRadius: '6px' }}
          />
          <input
            type="date"
            value={dateTo}
            onChange={(e) => {
              setOffset(0);
              setDateTo(e.target.value);
            }}
            aria-label="Filter to date"
            style={{ padding: '0.35rem 0.5rem', borderRadius: '6px' }}
          />
        </div>
      </div>

      {error && (
        <div className="settings-alert error" style={{ marginBottom: '0.75rem' }}>
          <AlertTriangle size={15} />
          <span>{error}</span>
        </div>
      )}

      {feedback && (
        <div
          className={`settings-alert ${feedback.type === 'success' ? 'success' : 'error'}`}
          style={{ marginBottom: '0.75rem' }}
        >
          {feedback.type === 'success' ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
          <span>{feedback.text}</span>
        </div>
      )}

      {/* Retry Side-Effects Confirmation Notice */}
      {retryConfirmItem && (
        <div className="settings-card" style={{ marginBottom: '0.75rem', borderColor: 'var(--color-warning)' }}>
          <p style={{ marginBottom: '0.5rem', fontSize: '0.88rem' }}>
            <strong>Confirm Full Rerun:</strong> &ldquo;{retryConfirmItem.automation_name}&rdquo; already completed{' '}
            {retryConfirmItem.completed_steps} of {retryConfirmItem.total_steps} steps before stopping.
            Rerunning executes from Step 1 and may repeat earlier actions.
          </p>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              type="button"
              className="refresh-btn"
              onClick={() => handleRetry(retryConfirmItem, true)}
              data-testid="confirm-retry-rerun-btn"
            >
              <RotateCcw size={13} />
              <span>Rerun From Beginning</span>
            </button>
            <button type="button" className="refresh-btn" onClick={() => setRetryConfirmItem(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* History List */}
      {items.length === 0 ? (
        <div className="active-ops-empty">
          <History size={24} className="text-muted" />
          <div className="empty-text-group">
            <p className="empty-lead">No automation executions recorded</p>
            <p className="empty-sub">Run a custom automation or adjust filters to inspect execution history.</p>
          </div>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '0.5rem' }}>
          {items.map((item) => {
            const isExpanded = expandedExec?.execution_id === item.execution_id;
            const canRetry = ['failed', 'cancelled', 'interrupted'].includes(item.status);
            return (
              <div key={item.execution_id} className="settings-card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
                  <div>
                    <div style={{ fontWeight: 600 }}>
                      {item.automation_name}{' '}
                      <span className={`status-pill status-${item.status === 'completed' ? 'completed' : item.status === 'running' ? 'executing' : 'cancelled'}`}>
                        {item.status}
                      </span>
                    </div>
                    <div className="text-xs text-muted">
                      {new Date(item.started_at).toLocaleString()} &bull; Trigger: {item.trigger_type} &bull;{' '}
                      Steps: {item.completed_steps}/{item.total_steps}
                      {item.duration_ms !== null && item.duration_ms !== undefined ? ` • ${item.duration_ms}ms` : ''}
                    </div>
                    {item.error_summary && (
                      <div className="text-xs" style={{ color: 'var(--color-danger)', marginTop: '0.2rem' }}>
                        {item.error_summary}
                      </div>
                    )}
                  </div>

                  <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
                    <button type="button" className="refresh-btn" onClick={() => handleInspect(item.execution_id)}>
                      {isExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                      <span>Details</span>
                    </button>
                    {item.status === 'running' && (
                      <button type="button" className="refresh-btn" onClick={() => handleCancel(item.execution_id)}>
                        <Square size={13} />
                        <span>Cancel</span>
                      </button>
                    )}
                    {canRetry && (
                      <button
                        type="button"
                        className="refresh-btn"
                        onClick={() => handleRetry(item, item.completed_steps === 0)}
                      >
                        <RotateCcw size={13} />
                        <span>Retry</span>
                      </button>
                    )}
                    {item.status !== 'running' && (
                      <button type="button" className="refresh-btn" onClick={() => handleDeleteOne(item.execution_id)}>
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                </div>

                {isExpanded && expandedExec && (
                  <div style={{ marginTop: '0.75rem', paddingTop: '0.75rem', borderTop: '1px solid var(--border-color)', display: 'grid', gap: '0.35rem' }}>
                    {expandedExec.steps.map((st) => (
                      <div key={st.step_id} className="text-xs" style={{ padding: '0.35rem 0.5rem', borderRadius: '6px', border: '1px solid var(--border-color)' }}>
                        <div>
                          <strong>Step {st.step_index + 1}: {st.action}</strong> ({st.status})
                        </div>
                        {st.input_summary && <div className="text-muted">Input: {st.input_summary}</div>}
                        {st.output_summary && <div>Output: {st.output_summary}</div>}
                        {st.error_summary && <div style={{ color: 'var(--color-danger)' }}>Error: {st.error_summary}</div>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}

          {total > PAGE_SIZE && (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '0.5rem' }}>
              <button
                type="button"
                className="refresh-btn"
                disabled={offset === 0}
                onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
              >
                Previous
              </button>
              <span className="text-xs text-muted">
                Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
              </span>
              <button
                type="button"
                className="refresh-btn"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
              >
                Next
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default AutomationHistoryPanel;
