import { useState } from 'react';
import AppLayout from './components/layout/AppLayout';
import DashboardPage from './pages/DashboardPage';
import AgentPage from './pages/AgentPage';
import ExecutionsPage from './pages/ExecutionsPage';
import ConfirmationsPage from './pages/ConfirmationsPage';
import AutomationPage from './pages/AutomationPage';
import AuditLogsPage from './pages/AuditLogsPage';
import SettingsPage from './pages/SettingsPage';
import UpcomingSection from './components/common/UpcomingSection';
import useBackendHealth from './hooks/useBackendHealth';
import './styles/globals.css';
import './styles/dashboard.css';

const UPCOMING_DESCRIPTIONS: Record<string, { title: string; description: string; phase: string }> = {};

export function App() {
  const [currentNav, setCurrentNav] = useState<string>('dashboard');

  const {
    connectionState,
    health,
    emergencyStatus,
    lastChecked,
    error,
    isLoading,
    refetch,
  } = useBackendHealth(15000);

  const isConnected = connectionState !== 'disconnected';

  const renderContent = () => {
    switch (currentNav) {
      case 'dashboard':
        return (
          <DashboardPage
            connectionState={connectionState}
            health={health}
            emergencyStatus={emergencyStatus}
            lastChecked={lastChecked}
            error={error}
            isLoading={isLoading}
            onRefresh={refetch}
          />
        );
      case 'agent':
        return (
          <AgentPage
            onNavigateToConfirmations={() => setCurrentNav('confirmations')}
            isBackendConnected={isConnected}
          />
        );
      case 'executions':
        return (
          <ExecutionsPage
            isBackendConnected={isConnected}
          />
        );
      case 'confirmations':
        return (
          <ConfirmationsPage
            isBackendConnected={isConnected}
          />
        );
      case 'automation':
        return (
          <AutomationPage
            isBackendConnected={isConnected}
            emergencyStatus={emergencyStatus}
            onRefreshEmergencyStatus={refetch}
          />
        );
      case 'audit':
        return (
          <AuditLogsPage
            isBackendConnected={isConnected}
          />
        );
      case 'settings':
        return (
          <SettingsPage
            isBackendConnected={isConnected}
          />
        );
      default:
        return (
          <UpcomingSection
            title={UPCOMING_DESCRIPTIONS[currentNav]?.title || 'Upcoming Section'}
            description={UPCOMING_DESCRIPTIONS[currentNav]?.description || 'This feature is planned for an upcoming prompt.'}
            plannedPhase={UPCOMING_DESCRIPTIONS[currentNav]?.phase || 'Phase 6'}
            onBackToDashboard={() => setCurrentNav('dashboard')}
          />
        );
    }
  };

  return (
    <AppLayout
      currentNav={currentNav}
      onSelectNav={(id) => setCurrentNav(id)}
      connectionState={connectionState}
      lastChecked={lastChecked}
      isLoading={isLoading}
      onRefresh={refetch}
      emergencyStatus={emergencyStatus}
    >
      {renderContent()}
    </AppLayout>
  );
}

export default App;
