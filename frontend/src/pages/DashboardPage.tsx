import React from 'react';
import { Server, Bot, ShieldCheck, ShieldAlert } from 'lucide-react';
import { BackendConnectionState } from '../hooks/useBackendHealth';
import { HealthResponse, EmergencyStopStatus } from '../api/types';
import StatusCard from '../components/dashboard/StatusCard';
import BackendStatus from '../components/dashboard/BackendStatus';
import SystemOverview from '../components/dashboard/SystemOverview';
import RecentActivity from '../components/dashboard/RecentActivity';
import EmergencyStopPanel from '../components/safety/EmergencyStopPanel';
import ErrorState from '../components/common/ErrorState';

interface DashboardPageProps {
  connectionState: BackendConnectionState;
  health: HealthResponse | null;
  emergencyStatus: EmergencyStopStatus | null;
  lastChecked: Date | null;
  error: string | null;
  isLoading: boolean;
  onRefresh: () => void;
}

export const DashboardPage: React.FC<DashboardPageProps> = ({
  connectionState,
  health,
  emergencyStatus,
  lastChecked,
  error,
  isLoading,
  onRefresh,
}) => {
  const isEmergencyActive = emergencyStatus?.is_active ?? false;
  const isConnected = connectionState === 'connected';

  return (
    <>
      {/* Page Title & Context */}
      <div className="page-header">
        <div>
          <h1 className="page-title">Agent Control Dashboard</h1>
          <p className="page-description">
            Real-time status overview, backend telemetry, safety latches, and automation state.
          </p>
        </div>
      </div>

      {/* Prominent Global Emergency Stop Safety Control */}
      <EmergencyStopPanel
        status={emergencyStatus}
        onStatusChange={onRefresh}
      />

      {/* Disconnection Banner if backend is down */}
      {connectionState === 'disconnected' && (
        <ErrorState
          title="Backend Service Disconnected"
          message={`Could not connect to VoxPilot backend at ${health ? health.service : 'http://127.0.0.1:8000'}. Error: ${error || 'Connection refused.'}`}
          onRetry={onRefresh}
          isRetrying={isLoading}
        />
      )}

      {/* Primary Overview Cards Grid */}
      <div className="status-cards-grid">
        {/* Card 1: Backend Connection */}
        <StatusCard
          icon={<Server size={18} />}
          label="Backend Service"
          value={
            connectionState === 'connected'
              ? 'Connected'
              : connectionState === 'disconnected'
                ? 'Offline'
                : 'Checking...'
          }
          badgeText={health ? `v${health.version}` : undefined}
          badgeVariant={connectionState === 'connected' ? 'success' : 'danger'}
          detail="FastAPI Localhost Daemon (127.0.0.1:8000)"
        />

        {/* Card 2: AI Agent State */}
        <StatusCard
          icon={<Bot size={18} />}
          label="LangGraph Agent"
          value={isConnected ? 'Available' : 'Unavailable'}
          badgeText={isConnected ? 'Ollama' : 'Offline'}
          badgeVariant={isConnected ? 'success' : 'neutral'}
          detail={isConnected ? '37 Registered Tools allowlisted' : 'Awaiting backend connection'}
        />

        {/* Card 3: Emergency Stop Latch */}
        <StatusCard
          icon={isEmergencyActive ? <ShieldAlert size={18} /> : <ShieldCheck size={18} />}
          label="Emergency Stop"
          value={isEmergencyActive ? 'TRIGGERED' : 'Inactive (Safe)'}
          badgeText={isEmergencyActive ? 'STOPPED' : 'Normal'}
          badgeVariant={isEmergencyActive ? 'danger' : 'success'}
          detail={
            isEmergencyActive
              ? `Reason: ${emergencyStatus?.trigger_reason || 'Manual stop'}`
              : 'Global Hotkey: Ctrl + Alt + Escape'
          }
        />
      </div>

      {/* Detailed Backend Connectivity Section */}
      <BackendStatus
        connectionState={connectionState}
        health={health}
        emergencyStatus={emergencyStatus}
        lastChecked={lastChecked}
        error={error}
        isLoading={isLoading}
        onRefresh={onRefresh}
      />

      {/* System Overview (Clear Unavailable State) */}
      <SystemOverview />

      {/* Recent Activity Section (Empty or Real Data) */}
      <RecentActivity isBackendConnected={isConnected} />
    </>
  );
};

export default DashboardPage;

