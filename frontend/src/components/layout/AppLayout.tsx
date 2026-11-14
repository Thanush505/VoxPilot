import React, { useState } from 'react';
import Sidebar from './Sidebar';
import Header from './Header';
import UpdateNotificationBanner from '../updates/UpdateNotificationBanner';
import { NAV_ITEMS } from './navTypes';
import { BackendConnectionState } from '../../hooks/useBackendHealth';
import { EmergencyStopStatus, ProfileItem } from '../../api/types';

interface AppLayoutProps {
  currentNav: string;
  onSelectNav: (id: string) => void;
  connectionState: BackendConnectionState;
  lastChecked: Date | null;
  isLoading: boolean;
  onRefresh: () => void;
  emergencyStatus: EmergencyStopStatus | null;
  profiles?: ProfileItem[];
  activeProfile?: ProfileItem | null;
  isProfileSwitching?: boolean;
  onSwitchProfile?: (profileId: string) => void;
  children: React.ReactNode;
}

export const AppLayout: React.FC<AppLayoutProps> = ({
  currentNav,
  onSelectNav,
  connectionState,
  lastChecked,
  isLoading,
  onRefresh,
  emergencyStatus,
  profiles = [],
  activeProfile = null,
  isProfileSwitching = false,
  onSwitchProfile,
  children,
}) => {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem('voxpilot.sidebar.collapsed') === 'true';
    } catch {
      return false;
    }
  });

  const handleToggleSidebarCollapse = () => {
    setIsSidebarCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem('voxpilot.sidebar.collapsed', String(next));
      } catch {
        // Ignore storage errors
      }
      return next;
    });
  };

  const activeItem = NAV_ITEMS.find((item) => item.id === currentNav);
  const pageTitle = activeItem ? activeItem.label : 'Dashboard';

  return (
    <div className="app-container">
      <Sidebar
        currentNav={currentNav}
        onSelectNav={onSelectNav}
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        emergencyStatus={emergencyStatus}
        isCollapsed={isSidebarCollapsed}
        onToggleCollapse={handleToggleSidebarCollapse}
      />

      <div className="main-content">
        <Header
          pageTitle={pageTitle}
          connectionState={connectionState}
          lastChecked={lastChecked}
          isLoading={isLoading}
          onRefresh={onRefresh}
          onToggleSidebar={() => setIsSidebarOpen(!isSidebarOpen)}
          onSelectNav={onSelectNav}
          profiles={profiles}
          activeProfile={activeProfile}
          isProfileSwitching={isProfileSwitching}
          onSwitchProfile={onSwitchProfile}
        />

        <UpdateNotificationBanner
          isBackendConnected={connectionState === 'connected'}
          onOpenSettings={() => onSelectNav('settings')}
        />

        <main className="page-container">
          {children}
        </main>
      </div>
    </div>
  );
};

export default AppLayout;
