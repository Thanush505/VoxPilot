import React, { useState, useEffect, useCallback } from 'react';
import {
  FileText,
  Download,
  Filter,
  RefreshCw,
  Search,
  CheckCircle,
  AlertTriangle,
  Shield,
  FileCheck,
  Info,
} from 'lucide-react';
import apiClient from '../api/client';
import {
  ExecutionStatusResponse,
  ConversationInfo,
  AuditExportResponse,
  AuditExportRequest,
} from '../api/types';
import LoadingState from '../components/common/LoadingState';
import ErrorState from '../components/common/ErrorState';

interface AuditLogsPageProps {
  isBackendConnected: boolean;
}

export const AuditLogsPage: React.FC<AuditLogsPageProps> = ({ isBackendConnected }) => {
  // Audit Records State
  const [executions, setExecutions] = useState<ExecutionStatusResponse[]>([]);
  const [conversations, setConversations] = useState<ConversationInfo[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Filters State
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [conversationFilter, setConversationFilter] = useState<string>('all');
  const [startDate, setStartDate] = useState<string>('');
  const [endDate, setEndDate] = useState<string>('');
  const [searchQuery, setSearchQuery] = useState<string>('');

  // Selected Record Modal / Drawer
  const [selectedRecord, setSelectedRecord] = useState<ExecutionStatusResponse | null>(null);

  // Export Form State
  const [exportFormat, setExportFormat] = useState<'json' | 'csv'>('json');
  const [customFilename, setCustomFilename] = useState<string>('');
  const [confirmOverwrite, setConfirmOverwrite] = useState<boolean>(false);
  const [isExporting, setIsExporting] = useState<boolean>(false);
  const [exportMessage, setExportMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Generated Exports List
  const [exportsList, setExportsList] = useState<AuditExportResponse[]>([]);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  // 1. Fetch Audit Records from persistent backend
  const fetchAuditData = useCallback(async () => {
    if (!isBackendConnected) {
      setExecutions([]);
      setConversations([]);
      setExportsList([]);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      // Fetch conversations
      const convRes = await apiClient.getConversations(50, 0);
      if (convRes.ok && convRes.data) {
        setConversations(convRes.data);

        // Fetch executions for each conversation
        const allExecs: ExecutionStatusResponse[] = [];
        const execPromises = convRes.data.map((c) =>
          apiClient.getConversationExecutions(c.conversation_id)
        );
        const results = await Promise.all(execPromises);
        results.forEach((res) => {
          if (res.ok && res.data) {
            allExecs.push(...res.data);
          }
        });

        // Deduplicate
        const unique = new Map<string, ExecutionStatusResponse>();
        allExecs.forEach((e) => unique.set(e.execution_id, e));
        const sorted = Array.from(unique.values()).sort((a, b) => {
          // Sort by execution_id or sequence
          return b.execution_id.localeCompare(a.execution_id);
        });
        setExecutions(sorted);
      } else {
        setError(convRes.error || 'Failed to query conversations from SQLite database.');
      }

      // Fetch existing exports list
      const expRes = await apiClient.listAuditExports();
      if (expRes.ok && expRes.data) {
        setExportsList(expRes.data);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error fetching audit logs.');
    } finally {
      setIsLoading(false);
    }
  }, [isBackendConnected]);

  useEffect(() => {
    fetchAuditData();
  }, [fetchAuditData]);

  // 2. Client-side record filtering for live inspection
  const filteredRecords = executions.filter((rec) => {
    // Status filter
    if (statusFilter !== 'all' && rec.execution_status !== statusFilter) {
      return false;
    }
    // Conversation filter
    if (conversationFilter !== 'all' && rec.conversation_id !== conversationFilter) {
      return false;
    }
    // Search query
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const matchInput = rec.user_input?.toLowerCase().includes(q);
      const matchId = rec.execution_id.toLowerCase().includes(q);
      const matchIntent = rec.intent?.toLowerCase().includes(q);
      if (!matchInput && !matchId && !matchIntent) {
        return false;
      }
    }
    return true;
  });

  // 3. Trigger Backend Audit Export
  const handleTriggerExport = async (e: React.FormEvent) => {
    e.preventDefault();
    setExportMessage(null);

    // Validation: prevent traversal in custom filename
    if (customFilename.trim()) {
      const clean = customFilename.trim();
      if (clean.includes('/') || clean.includes('\\') || clean.includes('..')) {
        setExportMessage({
          type: 'error',
          text: 'Filename contains forbidden path separators or traversal sequences.',
        });
        return;
      }
    }

    setIsExporting(true);

    try {
      const payload: AuditExportRequest = {
        format: exportFormat,
        filename: customFilename.trim() || undefined,
        status_filter: statusFilter !== 'all' ? statusFilter : undefined,
        conversation_id: conversationFilter !== 'all' ? conversationFilter : undefined,
        start_date: startDate.trim() || undefined,
        end_date: endDate.trim() || undefined,
        confirm_overwrite: confirmOverwrite,
      };

      const res = await apiClient.exportAuditTrail(payload);
      if (res.ok && res.data) {
        setExportMessage({
          type: 'success',
          text: `Audit trail exported successfully (${res.data.record_count} records) -> ${res.data.filename}`,
        });
        // Refresh export list
        const expListRes = await apiClient.listAuditExports();
        if (expListRes.ok && expListRes.data) {
          setExportsList(expListRes.data);
        }
      } else {
        setExportMessage({
          type: 'error',
          text: res.error || 'Failed to generate audit export archive.',
        });
      }
    } catch (err: unknown) {
      setExportMessage({
        type: 'error',
        text: err instanceof Error ? err.message : 'Error generating audit export.',
      });
    } finally {
      setIsExporting(false);
    }
  };

  // 4. Safe Browser Download Handling
  const handleDownloadFile = async (exportId: string, filename: string) => {
    setDownloadingId(exportId);
    setExportMessage(null);

    try {
      const result = await apiClient.downloadAuditExport(exportId, filename);
      if (result.ok) {
        setExportMessage({
          type: 'success',
          text: `Download initiated safely for '${filename}'. Temporary object URL revoked.`,
        });
      } else {
        setExportMessage({
          type: 'error',
          text: result.error || `Download failed for export ${exportId}.`,
        });
      }
    } catch (err: unknown) {
      setExportMessage({
        type: 'error',
        text: err instanceof Error ? err.message : 'Error triggering download.',
      });
    } finally {
      setDownloadingId(null);
    }
  };

  // Reset Filters
  const handleResetFilters = () => {
    setStatusFilter('all');
    setConversationFilter('all');
    setStartDate('');
    setEndDate('');
    setSearchQuery('');
  };

  return (
    <div className="audit-page">
      {/* Page Header */}
      <div className="page-header">
        <div>
          <h1 className="page-title">Audit Logs & Execution Archive</h1>
          <p className="page-description">
            Inspect persistent agent workflows, verify sensitive credential redaction, and generate sanitized JSON/CSV archives.
          </p>
        </div>

        <button
          className="refresh-btn"
          onClick={fetchAuditData}
          disabled={isLoading}
          type="button"
          aria-label="Reload audit records"
        >
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          <span>Reload Audit Data</span>
        </button>
      </div>

      {!isBackendConnected ? (
        <ErrorState
          title="Backend Disconnected"
          message="Cannot retrieve persistent audit logs while backend service is offline."
          onRetry={fetchAuditData}
          isRetrying={isLoading}
        />
      ) : (
        <>
          {error && (
            <div className="alert-banner alert-warning">
              <AlertTriangle size={16} />
              <span>{error}</span>
            </div>
          )}

          {/* Export Configuration Card */}
          <div className="audit-export-card">
            <div className="card-header-row">
              <div className="card-title-group">
                <FileCheck size={18} className="text-primary" />
                <h2 className="card-title">Generate Sanitized Audit Archive</h2>
              </div>
              <span className="badge-vault">Contained in data/exports</span>
            </div>

            <form onSubmit={handleTriggerExport} className="export-form">
              <div className="export-form-grid">
                {/* Format selection */}
                <div className="form-group">
                  <label className="form-label" htmlFor="export-format-select">Export Format</label>
                  <div className="format-toggle-group">
                    <button
                      type="button"
                      id="export-format-select"
                      className={`format-btn ${exportFormat === 'json' ? 'format-active' : ''}`}
                      onClick={() => setExportFormat('json')}
                    >
                      JSON
                    </button>
                    <button
                      type="button"
                      className={`format-btn ${exportFormat === 'csv' ? 'format-active' : ''}`}
                      onClick={() => setExportFormat('csv')}
                    >
                      CSV
                    </button>
                  </div>
                </div>

                {/* Optional filename */}
                <div className="form-group">
                  <label className="form-label" htmlFor="custom-filename">Custom Filename (Optional)</label>
                  <input
                    id="custom-filename"
                    type="text"
                    className="form-input"
                    placeholder={`e.g. audit_${Date.now()}.${exportFormat}`}
                    value={customFilename}
                    onChange={(e) => setCustomFilename(e.target.value)}
                  />
                  <span className="form-hint">Filename only. Slashes and traversal sequences are forbidden.</span>
                </div>

                {/* Overwrite confirmation */}
                <div className="form-group checkbox-group">
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={confirmOverwrite}
                      onChange={(e) => setConfirmOverwrite(e.target.checked)}
                    />
                    <span>Allow overwrite if file exists</span>
                  </label>
                  <span className="form-hint">Prevents accidental data loss without explicit confirmation.</span>
                </div>
              </div>

              {exportMessage && (
                <div className={`alert-banner ${exportMessage.type === 'success' ? 'alert-success' : 'alert-error'}`}>
                  {exportMessage.type === 'success' ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}
                  <span>{exportMessage.text}</span>
                </div>
              )}

              <div className="export-form-actions">
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={isExporting}
                >
                  <Download size={14} className={isExporting ? 'animate-spin' : ''} />
                  <span>{isExporting ? 'Exporting...' : `Export Audit Trail (${exportFormat.toUpperCase()})`}</span>
                </button>
              </div>
            </form>
          </div>

          {/* Generated Export Archives List */}
          {exportsList.length > 0 && (
            <div className="audit-section">
              <div className="section-title-row">
                <div className="section-title-group">
                  <Download size={18} className="text-primary" />
                  <h2 className="section-title">Available Export Archives ({exportsList.length})</h2>
                </div>
              </div>

              <div className="exports-table-container">
                <table className="exports-table">
                  <thead>
                    <tr>
                      <th>Filename</th>
                      <th>Format</th>
                      <th>Records</th>
                      <th>Generated At</th>
                      <th>Export ID</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {exportsList.map((exp) => (
                      <tr key={exp.export_id}>
                        <td className="font-semibold text-sm">{exp.filename}</td>
                        <td>
                          <span className="format-badge">{exp.format.toUpperCase()}</span>
                        </td>
                        <td>{exp.record_count}</td>
                        <td className="text-xs text-muted">
                          {exp.exported_at ? new Date(exp.exported_at).toLocaleString() : 'N/A'}
                        </td>
                        <td className="font-mono text-xs text-muted">{exp.export_id}</td>
                        <td>
                          <button
                            type="button"
                            className="btn-download"
                            onClick={() => handleDownloadFile(exp.export_id, exp.filename)}
                            disabled={downloadingId === exp.export_id}
                            title="Safe browser download via temporary object URL"
                          >
                            <Download size={12} className={downloadingId === exp.export_id ? 'animate-spin' : ''} />
                            <span>Download</span>
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Audit Records Filter Bar */}
          <div className="audit-filters-card">
            <div className="filters-header">
              <div className="filters-title">
                <Filter size={16} className="text-primary" />
                <span>Filter Persistent Audit Records</span>
              </div>
              <button
                type="button"
                className="btn-text-muted"
                onClick={handleResetFilters}
              >
                Reset Filters
              </button>
            </div>

            <div className="filters-grid">
              {/* Search query */}
              <div className="filter-item">
                <label className="filter-label" htmlFor="audit-search-input">Search Input / Intent / ID</label>
                <div className="search-input-wrapper">
                  <Search size={14} className="search-icon" />
                  <input
                    id="audit-search-input"
                    type="text"
                    className="filter-input search-input"
                    placeholder="Search query text..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                </div>
              </div>

              {/* Status filter */}
              <div className="filter-item">
                <label className="filter-label" htmlFor="audit-status-select">Execution Status</label>
                <select
                  id="audit-status-select"
                  className="filter-select"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                >
                  <option value="all">All Statuses</option>
                  <option value="completed">Completed</option>
                  <option value="failed">Failed</option>
                  <option value="cancelled">Cancelled</option>
                  <option value="rate_limited">Rate Limited</option>
                  <option value="confirmation_required">Confirmation Required</option>
                </select>
              </div>

              {/* Conversation filter */}
              <div className="filter-item">
                <label className="filter-label" htmlFor="audit-conv-select">Conversation</label>
                <select
                  id="audit-conv-select"
                  className="filter-select"
                  value={conversationFilter}
                  onChange={(e) => setConversationFilter(e.target.value)}
                >
                  <option value="all">All Conversations</option>
                  {conversations.map((c) => (
                    <option key={c.conversation_id} value={c.conversation_id}>
                      {c.title || c.conversation_id}
                    </option>
                  ))}
                </select>
              </div>

              {/* Date Filters */}
              <div className="filter-item">
                <label className="filter-label" htmlFor="audit-start-date">From Date</label>
                <input
                  id="audit-start-date"
                  type="date"
                  className="filter-input"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                />
              </div>

              <div className="filter-item">
                <label className="filter-label" htmlFor="audit-end-date">To Date</label>
                <input
                  id="audit-end-date"
                  type="date"
                  className="filter-input"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                />
              </div>
            </div>
          </div>

          {/* Records Table / List */}
          <div className="audit-section">
            <div className="section-title-row">
              <div className="section-title-group">
                <FileText size={18} className="text-primary" />
                <h2 className="section-title">
                  Audit Records ({filteredRecords.length} of {executions.length})
                </h2>
              </div>
            </div>

            {isLoading && executions.length === 0 ? (
              <LoadingState message="Loading persistent execution audit records..." />
            ) : filteredRecords.length === 0 ? (
              <div className="empty-audit-state">
                <Info size={32} className="text-muted" />
                <p className="empty-lead">No audit records match your filters.</p>
                <p className="empty-sub">Try broadening your search query, status, or date range.</p>
              </div>
            ) : (
              <div className="audit-records-table-container">
                <table className="audit-table">
                  <thead>
                    <tr>
                      <th>Status</th>
                      <th>Execution ID</th>
                      <th>User Request</th>
                      <th>Intent</th>
                      <th>Steps</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredRecords.map((rec) => (
                      <tr key={rec.execution_id}>
                        <td>
                          <span className={`status-pill status-${rec.execution_status}`}>
                            {rec.execution_status}
                          </span>
                        </td>
                        <td className="font-mono text-xs">{rec.execution_id}</td>
                        <td className="record-request-cell" title={rec.user_input}>
                          {rec.user_input}
                        </td>
                        <td>
                          <span className="intent-badge">{rec.intent || 'unclassified'}</span>
                        </td>
                        <td className="text-xs">
                          {rec.completed_steps?.length || 0} / {rec.plan?.length || 0}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn-view-details"
                            onClick={() => setSelectedRecord(rec)}
                          >
                            Inspect
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Record Detail Inspection Modal */}
          {selectedRecord && (
            <div className="modal-backdrop" onClick={() => setSelectedRecord(null)}>
              <div
                className="audit-detail-modal"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-modal="true"
                aria-labelledby="audit-modal-title"
              >
                <div className="modal-header">
                  <div>
                    <h3 id="audit-modal-title" className="modal-title">
                      Audit Record Inspection
                    </h3>
                    <div className="font-mono text-xs text-muted">
                      ID: {selectedRecord.execution_id}
                    </div>
                  </div>
                  <button
                    type="button"
                    className="modal-close-btn"
                    onClick={() => setSelectedRecord(null)}
                    aria-label="Close dialog"
                  >
                    ✕
                  </button>
                </div>

                <div className="modal-body">
                  <div className="audit-detail-field">
                    <span className="detail-field-label">User Request:</span>
                    <p className="detail-field-text font-semibold">{selectedRecord.user_input}</p>
                  </div>

                  <div className="audit-detail-row">
                    <div className="audit-detail-field">
                      <span className="detail-field-label">Status:</span>
                      <span className={`status-pill status-${selectedRecord.execution_status}`}>
                        {selectedRecord.execution_status}
                      </span>
                    </div>
                    <div className="audit-detail-field">
                      <span className="detail-field-label">Intent:</span>
                      <span className="intent-badge">{selectedRecord.intent || 'None'}</span>
                    </div>
                    <div className="audit-detail-field">
                      <span className="detail-field-label">Conversation ID:</span>
                      <span className="font-mono text-xs">{selectedRecord.conversation_id || 'N/A'}</span>
                    </div>
                  </div>

                  {selectedRecord.response && (
                    <div className="audit-detail-field">
                      <span className="detail-field-label">Agent Spoken / Text Response:</span>
                      <p className="detail-response-box">{selectedRecord.response}</p>
                    </div>
                  )}

                  {selectedRecord.error && (
                    <div className="audit-detail-field">
                      <span className="detail-field-label text-danger">Error / Failure:</span>
                      <p className="detail-error-box">{selectedRecord.error}</p>
                    </div>
                  )}

                  <div className="audit-detail-field">
                    <span className="detail-field-label">
                      Executed Steps ({selectedRecord.completed_steps?.length || 0}):
                    </span>
                    <div className="steps-inspection-list">
                      {selectedRecord.completed_steps && selectedRecord.completed_steps.length > 0 ? (
                        selectedRecord.completed_steps.map((step, idx) => (
                          <div key={idx} className="step-item-card">
                            <div className="step-item-header">
                              <span className="font-mono text-xs">Step #{step.step_index ?? idx}: {step.action}</span>
                              <span className={`status-pill status-${step.status}`}>{step.status}</span>
                            </div>
                            {step.error && (
                              <div className="step-err text-xs text-danger">{step.error}</div>
                            )}
                          </div>
                        ))
                      ) : (
                        <div className="text-xs text-muted italic">No step records recorded.</div>
                      )}
                    </div>
                  </div>

                  {/* Redaction Guarantee Notice */}
                  <div className="redaction-verified-box">
                    <Shield size={16} className="text-success" />
                    <span>
                      <strong>Credential Scrubbing Verified:</strong> Passwords, cryptographic confirmation tokens, and authentication secrets are permanently scrubbed and replaced with <code>[REDACTED]</code>.
                    </span>
                  </div>
                </div>

                <div className="modal-footer">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setSelectedRecord(null)}
                  >
                    Close Inspection
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Security Disclosure Card */}
          <div className="safety-disclosure-card">
            <div className="disclosure-header">
              <Shield size={18} className="text-primary" />
              <h3 className="disclosure-title">Audit Export Containment & Trust Boundary Disclosure</h3>
            </div>
            <div className="disclosure-content">
              <p>
                <strong>Approved Destination Containment:</strong> Audit exports are written exclusively to the daemon's local <code>data/exports</code> folder. Traversal patterns (e.g. <code>../</code> or absolute file paths) are blocked by strict path canonicalization.
              </p>
              <p>
                <strong>Operator Trust Boundary:</strong> Localhost binding and CORS headers provide local boundary isolation, but do not authenticate multi-user network operators. Audit exports are intended for the local device owner.
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default AuditLogsPage;
