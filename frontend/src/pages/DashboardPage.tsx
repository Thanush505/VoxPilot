import React, { useState } from 'react';
import {
  Server,
  Bot,
  ShieldCheck,
  ShieldAlert,
  Send,
  Mic,
  FolderOpen,
  Globe,
  Mail,
  AppWindow,
  Cpu,
} from 'lucide-react';
import { BackendConnectionState } from '../hooks/useBackendHealth';
import { HealthResponse, EmergencyStopStatus } from '../api/types';
import apiClient from '../api/client';
import StatusCard from '../components/dashboard/StatusCard';
import BackendStatus from '../components/dashboard/BackendStatus';
import SystemOverview from '../components/dashboard/SystemOverview';
import RecentActivity from '../components/dashboard/RecentActivity';
import EmergencyStopPanel from '../components/safety/EmergencyStopPanel';
import ErrorState from '../components/common/ErrorState';
import { AICoreHeroCard } from '../components/aicore/AICoreHeroCard';
import { useAICore } from '../context/AICoreContext';
import type { AgentSpecialistMode } from './AgentPage';

interface DashboardPageProps {
  connectionState: BackendConnectionState;
  health: HealthResponse | null;
  emergencyStatus: EmergencyStopStatus | null;
  lastChecked: Date | null;
  error: string | null;
  isLoading: boolean;
  onRefresh: () => void;
  onNavigate?: (nav: string) => void;
  onOpenAgentWorkspace?: (mode?: AgentSpecialistMode, initialPrompt?: string) => void;
}

export const DashboardPage: React.FC<DashboardPageProps> = ({
  connectionState,
  health,
  emergencyStatus,
  lastChecked,
  error,
  isLoading,
  onRefresh,
  onNavigate,
  onOpenAgentWorkspace,
}) => {
  const [quickPrompt, setQuickPrompt] = useState('');
  const { activeProviderLabel, activeModelLabel } = useAICore();
  const isEmergencyActive = emergencyStatus?.is_active ?? false;
  const isConnected = connectionState === 'connected';
  const baseUrl = apiClient.getBaseUrl();
  const hostLabel = baseUrl.replace(/^https?:\/\//i, '');

  const handleQuickSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const cleaned = quickPrompt.trim();
    if (!cleaned) return;
    setQuickPrompt('');
    if (onOpenAgentWorkspace) {
      onOpenAgentWorkspace('all', cleaned);
    } else {
      onNavigate?.('agent');
    }
  };

  return (
    <>
      {/* Page Title & Context */}
      <div className="page-header">
        <div>
          <h1 className="page-title">Agent Control Dashboard</h1>
          <p className="page-description">
            VoxPilot desktop command center: AI Core telemetry, voice status, hardware metrics, and safety latches.
          </p>
        </div>
        <div className="page-header-actions">
          <span className={`health-pill ${isConnected ? 'connected' : 'disconnected'}`}>
            <span className="status-dot" />
            <span>{isConnected ? 'System Operational' : 'Backend Offline'}</span>
          </span>
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
          message={`Could not connect to VoxPilot backend at ${baseUrl}. Error: ${error || 'Connection refused.'}`}
          onRetry={onRefresh}
          isRetrying={isLoading}
        />
      )}

      {/* Command Center Top Grid: Prominent AI Core + Quick Command & Specialist Launcher */}
      <div className="vp-command-grid" data-testid="dashboard-command-center">
        <AICoreHeroCard
          onOpenAgent={() =>
            onOpenAgentWorkspace ? onOpenAgentWorkspace('all') : onNavigate?.('agent')
          }
          onOpenSettings={() => onNavigate?.('settings')}
        />

        <section className="vp-quick-command-card" data-testid="dashboard-quick-command">
          <div>
            <h2 className="dashboard-card-title" style={{ marginBottom: 4 }}>
              Quick Command & Specialist Hub
            </h2>
            <p className="dashboard-card-description">
              Send a natural language instruction or jump straight into a specialist assistant mode.
            </p>
          </div>

          <form className="vp-quick-command-form" onSubmit={handleQuickSubmit}>
            <input
              type="text"
              className="vp-quick-command-input"
              placeholder="Ask VoxPilot to run a task, summarize emails, research, or organize files..."
              value={quickPrompt}
              onChange={(e) => setQuickPrompt(e.target.value)}
              disabled={!isConnected}
              data-testid="dashboard-quick-command-input"
            />
            <button
              type="submit"
              className="vp-btn-primary"
              disabled={!isConnected || !quickPrompt.trim()}
              data-testid="dashboard-quick-command-submit"
            >
              <Send size={14} />
              <span>Run</span>
            </button>
          </form>

          <div className="vp-specialist-launchers">
            <button
              type="button"
              className="vp-specialist-tile"
              onClick={() =>
                onOpenAgentWorkspace ? onOpenAgentWorkspace('voice') : onNavigate?.('agent')
              }
              data-testid="specialist-launch-voice"
            >
              <Mic size={15} style={{ color: 'var(--accent-blue)' }} />
              <span>Voice Assistant</span>
            </button>

            <button
              type="button"
              className="vp-specialist-tile"
              onClick={() =>
                onOpenAgentWorkspace ? onOpenAgentWorkspace('files') : onNavigate?.('agent')
              }
              data-testid="specialist-launch-files"
            >
              <FolderOpen size={15} style={{ color: 'var(--accent-blue)' }} />
              <span>File Assistant</span>
            </button>

            <button
              type="button"
              className="vp-specialist-tile"
              onClick={() =>
                onOpenAgentWorkspace ? onOpenAgentWorkspace('research') : onNavigate?.('agent')
              }
              data-testid="specialist-launch-research"
            >
              <Globe size={15} style={{ color: 'var(--accent-blue)' }} />
              <span>Browser Research</span>
            </button>

            <button
              type="button"
              className="vp-specialist-tile"
              onClick={() =>
                onOpenAgentWorkspace ? onOpenAgentWorkspace('email') : onNavigate?.('agent')
              }
              data-testid="specialist-launch-email"
            >
              <Mail size={15} style={{ color: 'var(--accent-blue)' }} />
              <span>Email Assistant</span>
            </button>

            <button
              type="button"
              className="vp-specialist-tile"
              onClick={() => onNavigate?.('applications')}
              data-testid="specialist-launch-apps"
            >
              <AppWindow size={15} style={{ color: 'var(--accent-blue)' }} />
              <span>App Launcher</span>
            </button>

            <button
              type="button"
              className="vp-specialist-tile"
              onClick={() => onNavigate?.('automation')}
              data-testid="specialist-launch-automation"
            >
              <Cpu size={15} style={{ color: 'var(--accent-blue)' }} />
              <span>Automations</span>
            </button>
          </div>
        </section>
      </div>

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
          detail={`FastAPI Localhost Daemon (${hostLabel})`}
        />

        {/* Card 2: AI Agent State */}
        <StatusCard
          icon={<Bot size={18} />}
          label="AI Runtime & Agent"
          value={isConnected ? activeProviderLabel : 'Unavailable'}
          badgeText={isConnected ? activeModelLabel : 'Offline'}
          badgeVariant={isConnected ? 'success' : 'neutral'}
          detail={isConnected ? 'LangGraph orchestrator & allowlisted tools ready' : 'Awaiting backend connection'}
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

      {/* System Overview (Live Host Hardware & Ollama Runtime) */}
      <SystemOverview isBackendConnected={isConnected} />

      {/* Recent Activity Section (Empty or Real Data) */}
      <RecentActivity isBackendConnected={isConnected} />
    </>
  );
};

export default DashboardPage;

