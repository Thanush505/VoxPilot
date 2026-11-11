import React, { useState } from 'react';
import Sidebar from './Sidebar';
import Header from './Header';
import UpdateNotificationBanner from '../updates/UpdateNotificationBanner';
import { NAV_ITEMS } from './navTypes';
import { BackendConnectionState } from '../../hooks/useBackendHealth';
import { EmergencyStopStatus } from '../../api/types';

interface AppLayoutProps {
  currentNav: string;
  onSelectNav: (id: string) => void;
  connectionState: BackendConnectionState;
  lastChecked: Date | null;
  isLoading: boolean;
  onRefresh: () => void;
  emergencyStatus: EmergencyStopStatus | null;
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
  children,
}) => {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

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
