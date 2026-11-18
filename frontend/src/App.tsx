import { useState, useEffect, useCallback, useRef } from 'react';
import AppLayout from './components/layout/AppLayout';
import DashboardPage from './pages/DashboardPage';
import AgentPage, { type AgentSpecialistMode } from './pages/AgentPage';
import ApplicationsPage from './pages/ApplicationsPage';
import ExecutionsPage from './pages/ExecutionsPage';
import ConfirmationsPage from './pages/ConfirmationsPage';
import AutomationPage from './pages/AutomationPage';
import AuditLogsPage from './pages/AuditLogsPage';
import SettingsPage from './pages/SettingsPage';
import UpcomingSection from './components/common/UpcomingSection';
import FloatingVoiceOrb from './components/voice/FloatingVoiceOrb';
import useBackendHealth from './hooks/useBackendHealth';
import apiClient, { resolveDesktopSessionToken } from './api/client';
import { ProfileItem } from './api/types';
import { useTheme } from './theme/ThemeProvider';
import { AICoreProvider } from './context/AICoreContext';
import './styles/globals.css';
import './styles/dashboard.css';
import './styles/redesign.css';

const UPCOMING_DESCRIPTIONS: Record<string, { title: string; description: string; phase: string }> = {};

function MainShellApp() {
  const [currentNav, setCurrentNav] = useState<string>('dashboard');
  const [agentSpecialistMode, setAgentSpecialistMode] = useState<AgentSpecialistMode>('all');
  const [pendingAgentPrompt, setPendingAgentPrompt] = useState<string | null>(null);
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
    resolveDesktopSessionToken();
  }, []);

  useEffect(() => {
    loadProfiles();
  }, [loadProfiles]);

  const lastActivitySentRef = useRef<boolean | null>(null);

  useEffect(() => {
    if (!isConnected) {
      lastActivitySentRef.current = null;
      return;
    }

    const syncWindowActivity = () => {
      const isFocused = typeof document.hasFocus === 'function' ? document.hasFocus() : true;
      const isVisible = document.visibilityState !== 'hidden';
      const isActive = isFocused && isVisible;
      if (lastActivitySentRef.current === isActive) {
        return;
      }
      lastActivitySentRef.current = isActive;
      void apiClient.recordNotificationActivity(isActive);
    };

    syncWindowActivity();

    const handleFocus = () => syncWindowActivity();
    const handleBlur = () => {
      if (lastActivitySentRef.current === false) return;
      lastActivitySentRef.current = false;
      void apiClient.recordNotificationActivity(false);
    };

    window.addEventListener('focus', handleFocus);
    window.addEventListener('blur', handleBlur);
    document.addEventListener('visibilitychange', syncWindowActivity);

    return () => {
      window.removeEventListener('focus', handleFocus);
      window.removeEventListener('blur', handleBlur);
      document.removeEventListener('visibilitychange', syncWindowActivity);
    };
  }, [isConnected]);

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

  const handleOpenAgentWorkspace = useCallback(
    (mode: AgentSpecialistMode = 'all', initialPrompt?: string) => {
      setAgentSpecialistMode(mode);
      setPendingAgentPrompt(initialPrompt || null);
      setCurrentNav('agent');
    },
    []
  );

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
            onNavigate={(nav) => setCurrentNav(nav)}
            onOpenAgentWorkspace={handleOpenAgentWorkspace}
          />
        );
      case 'agent':
        return (
          <AgentPage
            key={`agent-${profileKey}`}
            onNavigateToConfirmations={() => setCurrentNav('confirmations')}
            isBackendConnected={isConnected}
            initialSpecialistMode={agentSpecialistMode}
            initialPrompt={pendingAgentPrompt}
            onConsumeInitialPrompt={() => setPendingAgentPrompt(null)}
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
    <AICoreProvider
      isConnected={isConnected}
      emergencyStatus={emergencyStatus}
      activeProfileId={profileKey}
    >
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
    </AICoreProvider>
  );
}

export function App() {
  const isOrbMode =
    typeof window !== 'undefined' &&
    new URLSearchParams(window.location.search).get('orb') === '1';
  if (isOrbMode) {
    return <FloatingVoiceOrb />;
  }
  return <MainShellApp />;
}

export default App;
