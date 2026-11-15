import React, { useEffect, useRef, useState } from 'react';
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
  const mainContentRef = useRef<HTMLDivElement | null>(null);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem('voxpilot.sidebar.collapsed') === 'true';
    } catch {
      return false;
    }
  });

  useEffect(() => {
    const container = mainContentRef.current;
    if (!container) return;
    if (typeof container.scrollTo === 'function') {
      container.scrollTo({ top: 0, behavior: 'auto' });
    } else {
      container.scrollTop = 0;
    }
  }, [currentNav]);

  useEffect(() => {
    if (!isSidebarOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsSidebarOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isSidebarOpen]);

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

      <div className="main-content" ref={mainContentRef}>
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
