import React from 'react';
import { Server, Activity, ShieldCheck, ShieldAlert, Clock, RefreshCw } from 'lucide-react';
import { BackendConnectionState } from '../../hooks/useBackendHealth';
import { HealthResponse, EmergencyStopStatus } from '../../api/types';
import apiClient from '../../api/client';

interface BackendStatusProps {
  connectionState: BackendConnectionState;
  health: HealthResponse | null;
  emergencyStatus: EmergencyStopStatus | null;
  lastChecked: Date | null;
  error: string | null;
  isLoading: boolean;
  onRefresh: () => void;
}

export const BackendStatus: React.FC<BackendStatusProps> = ({
  connectionState,
  health,
  emergencyStatus,
  lastChecked,
  error,
  isLoading,
  onRefresh,
}) => {
  const baseUrl = apiClient.getBaseUrl();
  const isEmergencyActive = emergencyStatus?.is_active ?? false;

  const formatLastChecked = (date: Date | null): string => {
    if (!date) return 'Never checked';
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  return (
    <div className="dashboard-card">
      <div className="dashboard-card-header">
        <div className="dashboard-card-title-group">
          <Server size={18} className="text-accent-blue" />
          <div>
            <h2 className="dashboard-card-title">Backend Connectivity & Services</h2>
            <p className="dashboard-card-description">
              Live status and telemetry for the local FastAPI daemon and automation controllers.
            </p>
          </div>
        </div>
        <button
          className="refresh-btn"
          onClick={onRefresh}
          disabled={isLoading}
          type="button"
        >
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          <span>{isLoading ? 'Checking...' : 'Check Now'}</span>
        </button>
      </div>

      <div className="metrics-grid">
        {/* Endpoint Info */}
        <div className="metric-box">
          <div className="metric-box-header">
            <span>API Host</span>
            <Server size={14} />
          </div>
          <div className="metric-box-value" style={{ fontSize: '15px' }}>
            {baseUrl}
          </div>
          <div className="metric-box-footer">
            Binding: Localhost daemon (127.0.0.1)
          </div>
        </div>

        {/* Backend Health */}
        <div className="metric-box">
          <div className="metric-box-header">
            <span>FastAPI Status</span>
            <Activity size={14} />
          </div>
          <div className="metric-box-value" style={{ fontSize: '18px' }}>
            {connectionState === 'connected' ? (
              <span style={{ color: 'var(--status-success)' }}>Healthy</span>
            ) : connectionState === 'disconnected' ? (
              <span style={{ color: 'var(--status-danger)' }}>Unreachable</span>
            ) : (
              <span style={{ color: 'var(--status-warning)' }}>Verifying...</span>
            )}
          </div>
          <div className="metric-box-footer">
            {health ? `${health.service} v${health.version}` : error || 'No connection'}
          </div>
        </div>

        {/* Emergency Stop Latch */}
        <div className="metric-box">
          <div className="metric-box-header">
            <span>Safety State</span>
            {isEmergencyActive ? (
              <ShieldAlert size={14} style={{ color: 'var(--status-danger)' }} />
            ) : (
              <ShieldCheck size={14} style={{ color: 'var(--status-success)' }} />
            )}
          </div>
          <div className="metric-box-value" style={{ fontSize: '18px' }}>
            {isEmergencyActive ? (
              <span style={{ color: 'var(--status-danger)' }}>EMERGENCY ACTIVE</span>
            ) : (
              <span style={{ color: 'var(--status-success)' }}>Latched Normal</span>
            )}
          </div>
          <div className="metric-box-footer">
            {emergencyStatus
              ? `${emergencyStatus.active_operations_count} active operation(s)`
              : 'Unknown state'}
          </div>
        </div>

        {/* Verification Timestamp */}
        <div className="metric-box">
          <div className="metric-box-header">
            <span>Last Health Check</span>
            <Clock size={14} />
          </div>
          <div className="metric-box-value" style={{ fontSize: '16px' }}>
            {formatLastChecked(lastChecked)}
          </div>
          <div className="metric-box-footer">
            Auto-refreshes every 15s
          </div>
        </div>
      </div>
    </div>
  );
};

export default BackendStatus;

