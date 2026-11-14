import { useState, useEffect, useCallback } from 'react';
import AppLayout from './components/layout/AppLayout';
import DashboardPage from './pages/DashboardPage';
import AgentPage from './pages/AgentPage';
import ApplicationsPage from './pages/ApplicationsPage';
import ExecutionsPage from './pages/ExecutionsPage';
import ConfirmationsPage from './pages/ConfirmationsPage';
import AutomationPage from './pages/AutomationPage';
import AuditLogsPage from './pages/AuditLogsPage';
import SettingsPage from './pages/SettingsPage';
import UpcomingSection from './components/common/UpcomingSection';
import useBackendHealth from './hooks/useBackendHealth';
import apiClient from './api/client';
import { ProfileItem } from './api/types';
import { useTheme } from './theme/ThemeProvider';
import './styles/globals.css';
import './styles/dashboard.css';

const UPCOMING_DESCRIPTIONS: Record<string, { title: string; description: string; phase: string }> = {};

export function App() {
  const [currentNav, setCurrentNav] = useState<string>('dashboard');
  const [profiles, setProfiles] = useState<ProfileItem[]>([]);
  const [activeProfile, setActiveProfile] = useState<ProfileItem | null>(null);
  const [isProfileSwitching, setIsProfileSwitching] = useState<boolean>(false);
  const { syncProfileTheme } = useTheme();

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

  const loadProfiles = useCallback(async () => {
    if (!isConnected) return;
    try {
      const res = await apiClient.getProfiles();
      if (res.ok && res.data) {
        setProfiles(res.data.profiles || []);
        setActiveProfile(res.data.active_profile || null);
        syncProfileTheme(res.data.active_profile || null);
      }
    } catch {
      // Non-fatal during startup/polling
    }
  }, [isConnected, syncProfileTheme]);

  useEffect(() => {
    loadProfiles();
  }, [loadProfiles]);

  const handleSwitchProfile = async (profileId: string) => {
    if (!profileId || profileId === activeProfile?.id) return;
    setIsProfileSwitching(true);
    try {
      const res = await apiClient.activateProfile(profileId);
      if (res.ok && res.data) {
        setActiveProfile(res.data);
        syncProfileTheme(res.data);
        await loadProfiles();
      }
    } finally {
      setIsProfileSwitching(false);
    }
  };

  const handleProfilesChanged = async () => {
    await loadProfiles();
  };

  const profileKey = activeProfile?.id || 'default';

  const renderContent = () => {
    switch (currentNav) {
      case 'dashboard':
        return (
          <DashboardPage
            key={`dash-${profileKey}`}
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
            key={`agent-${profileKey}`}
            onNavigateToConfirmations={() => setCurrentNav('confirmations')}
            isBackendConnected={isConnected}
          />
        );
      case 'applications':
        return (
          <ApplicationsPage
            isBackendConnected={isConnected}
          />
        );
      case 'executions':
        return (
          <ExecutionsPage
            key={`exec-${profileKey}`}
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
            key={`settings-${profileKey}`}
            isBackendConnected={isConnected}
            onProfileChanged={handleProfilesChanged}
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
      profiles={profiles}
      activeProfile={activeProfile}
      isProfileSwitching={isProfileSwitching}
      onSwitchProfile={handleSwitchProfile}
    >
      <div key={currentNav} className="page-transition-wrapper">
        {renderContent()}
      </div>
    </AppLayout>
  );
}

export default App;
