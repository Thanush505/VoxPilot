import React, { useState, useEffect, useCallback } from 'react';
import {
  ShieldCheck,
  RefreshCw,
  Keyboard,
  MousePointer,
  AppWindow,
  Globe,
  FolderTree,
  AlertTriangle,
  Info,
  Cpu,
  Lock,
} from 'lucide-react';
import apiClient from '../api/client';
import { EmergencyStopStatus, ToolInfo } from '../api/types';
import EmergencyStopPanel from '../components/safety/EmergencyStopPanel';
import LoadingState from '../components/common/LoadingState';
import ErrorState from '../components/common/ErrorState';

interface AutomationPageProps {
  isBackendConnected: boolean;
  emergencyStatus: EmergencyStopStatus | null;
  onRefreshEmergencyStatus: () => void;
}

interface SubsystemDef {
  id: string;
  name: string;
  category: string;
  icon: React.ReactNode;
  description: string;
}

const SUBSYSTEMS: SubsystemDef[] = [
  {
    id: 'keyboard',
    name: 'Keyboard Controller',
    category: 'keyboard_control',
    icon: <Keyboard size={20} className="subsystem-icon" />,
    description: 'Hardware keystroke synthesis, hotkey combinations, and text typing via pywinauto/win32.',
  },
  {
    id: 'mouse',
    name: 'Mouse Controller',
    category: 'mouse_control',
    icon: <MousePointer size={20} className="subsystem-icon" />,
    description: 'Cursor positioning, click dispatch, drag-and-drop, and scroll actions via pywinauto/win32.',
  },
  {
    id: 'window',
    name: 'Window Manager',
    category: 'window_management',
    icon: <AppWindow size={20} className="subsystem-icon" />,
    description: 'Desktop window discovery, focus activation, minimize/maximize, and window placement.',
  },
  {
    id: 'browser',
    name: 'Browser Automation',
    category: 'browser_control',
    icon: <Globe size={20} className="subsystem-icon" />,
    description: 'Playwright-driven Chromium browser automation, element interaction, and secure web downloads.',
  },
  {
    id: 'filesystem',
    name: 'Filesystem Controller',
    category: 'file_management',
    icon: <FolderTree size={20} className="subsystem-icon" />,
    description: 'Approved directory containment, file reading, writing, and directory management.',
  },
];

export const AutomationPage: React.FC<AutomationPageProps> = ({
  isBackendConnected,
  emergencyStatus,
  onRefreshEmergencyStatus,
}) => {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTools = useCallback(async () => {
    if (!isBackendConnected) {
      setTools([]);
      return;
    }

    setIsLoading(true);
    setError(null);
    try {
      const res = await apiClient.getTools();
      if (res.ok && res.data) {
        setTools(res.data);
      } else {
        setError(res.error || 'Failed to query registered automation tool capabilities.');
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error fetching automation tools.');
    } finally {
      setIsLoading(false);
    }
  }, [isBackendConnected]);

  useEffect(() => {
    fetchTools();
  }, [fetchTools]);

  const handleFullRefresh = () => {
    onRefreshEmergencyStatus();
    fetchTools();
  };

  const activeOperations = emergencyStatus?.active_operations || [];
  const activeOpsCount = emergencyStatus?.active_operations_count ?? 0;
  const isEmergencyActive = emergencyStatus?.is_active ?? false;

  return (
    <div className="automation-page">
      {/* Page Header */}
      <div className="page-header">
        <div>
          <h1 className="page-title">Automation Monitoring & Safety Telemetry</h1>
          <p className="page-description">
            Live telemetry for desktop automation controllers, active background operations, and safety isolation.
          </p>
        </div>

        <button
          className="refresh-btn"
          onClick={handleFullRefresh}
          disabled={isLoading}
          type="button"
          aria-label="Reload automation telemetry"
        >
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          <span>Refresh Telemetry</span>
        </button>
      </div>

      {/* Emergency Stop Panel */}
      <EmergencyStopPanel
        status={emergencyStatus}
        onStatusChange={onRefreshEmergencyStatus}
      />

      {!isBackendConnected ? (
        <ErrorState
          title="Backend Disconnected"
          message="Cannot poll automation controllers while backend service is offline."
          onRetry={handleFullRefresh}
          isRetrying={isLoading}
        />
      ) : (
        <>
          {/* Active Operations Section */}
          <div className="automation-section">
            <div className="section-title-row">
              <div className="section-title-group">
                <Cpu size={18} className="text-primary" />
                <h2 className="section-title">Active Automation Operations</h2>
              </div>
              <span className={`status-pill ${activeOpsCount > 0 ? 'status-executing' : 'status-completed'}`}>
                {activeOpsCount} Active {activeOpsCount === 1 ? 'Operation' : 'Operations'}
              </span>
            </div>

            {activeOpsCount === 0 ? (
              <div className="active-ops-empty">
                <ShieldCheck size={28} className="text-success" />
                <div className="empty-text-group">
                  <p className="empty-lead">All automation controllers are idle</p>
                  <p className="empty-sub">
                    No active background keyboard, mouse, browser, or filesystem operations are currently executing.
                  </p>
                </div>
              </div>
            ) : (
              <div className="active-ops-table-container">
                <table className="active-ops-table">
                  <thead>
                    <tr>
                      <th>Operation ID</th>
                      <th>Category</th>
                      <th>Description</th>
                      <th>Status</th>
                      <th>Started</th>
                      <th>PIDs</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activeOperations.map((op) => (
                      <tr key={op.operation_id}>
                        <td className="font-mono text-xs">{op.operation_id}</td>
                        <td>
                          <span className="category-pill">{op.category}</span>
                        </td>
                        <td>{op.description}</td>
                        <td>
                          <span className="status-pill status-executing">{op.status}</span>
                        </td>
                        <td className="text-xs text-muted">
                          {op.start_time ? new Date(op.start_time * 1000).toLocaleTimeString() : 'N/A'}
                        </td>
                        <td className="font-mono text-xs">
                          {op.pids && op.pids.length > 0 ? op.pids.join(', ') : 'None'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Subsystems Status & Allowlisted Capabilities */}
          <div className="automation-section">
            <div className="section-title-row">
              <div className="section-title-group">
                <Lock size={18} className="text-primary" />
                <h2 className="section-title">Automation Subsystems & Controller Isolation</h2>
              </div>
            </div>

            {error && (
              <div className="alert-banner alert-warning">
                <AlertTriangle size={16} />
                <span>{error}</span>
              </div>
            )}

            {isLoading && tools.length === 0 ? (
              <LoadingState message="Querying registered automation tool capabilities..." />
            ) : (
              <div className="subsystems-grid">
                {SUBSYSTEMS.map((sub) => {
                  const subTools = tools.filter(
                    (t) => t.category.toLowerCase() === sub.category.toLowerCase()
                  );

                  return (
                    <div key={sub.id} className="subsystem-card">
                      <div className="subsystem-card-header">
                        <div className="subsystem-title-row">
                          {sub.icon}
                          <h3 className="subsystem-name">{sub.name}</h3>
                        </div>
                        {/* Truthful status display: no fake polling route */}
                        <span className="status-pill status-unavailable" title="Internal backend automation engine without unauthenticated public polling routes">
                          Status unavailable
                        </span>
                      </div>

                      <p className="subsystem-desc">{sub.description}</p>

                      <div className="subsystem-meta">
                        <div className="meta-label">Controller Status:</div>
                        <div className="meta-text text-muted text-xs">
                          {isEmergencyActive ? (
                            <span className="text-danger font-semibold">
                              Interrupted by Emergency Stop latch
                            </span>
                          ) : (
                            'Internal Python engine (Protected behind ToolExecutor mediation)'
                          )}
                        </div>
                      </div>

                      <div className="subsystem-tools-section">
                        <div className="meta-label">
                          Allowlisted Tools ({subTools.length}):
                        </div>
                        {subTools.length === 0 ? (
                          <div className="text-xs text-muted italic">No tools loaded from backend registry.</div>
                        ) : (
                          <div className="tool-chips-list">
                            {subTools.map((t) => (
                              <span
                                key={t.name}
                                className={`tool-chip ${t.is_consequential ? 'chip-consequential' : 'chip-safe'}`}
                                title={`${t.description} (${t.is_consequential ? 'Requires Confirmation' : 'Safe Action'})`}
                              >
                                {t.name}
                                {t.is_consequential && (
                                  <span className="chip-badge">Approval</span>
                                )}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Safety Architecture Policy Disclosure */}
          <div className="safety-disclosure-card">
            <div className="disclosure-header">
              <Info size={18} className="text-primary" />
              <h3 className="disclosure-title">Safety Architecture & Direct Hardware Controls Policy</h3>
            </div>
            <div className="disclosure-content">
              <p>
                <strong>Zero Direct Hardware Controls:</strong> This monitoring page is strictly read-only and does not expose direct unconstrained keyboard typing, mouse clicking, or shell command buttons. All automation operations must be mediated by the LangGraph agent, validate safety checkpoints, respect rate limits, and request cryptographic confirmation for consequential actions.
              </p>
              <p>
                <strong>Hardware Emergency Stop:</strong> The global hotkey <kbd>Ctrl+Alt+Escape</kbd> halts all background automation loops immediately at the OS level, independent of browser connectivity or frontend rendering.
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default AutomationPage;
