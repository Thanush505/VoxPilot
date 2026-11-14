import React, { useEffect, useState } from 'react';
import { ShieldAlert, Shield, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { EmergencyStopStatus } from '../../api/types';
import apiClient from '../../api/client';
import { NAV_ITEMS } from './navTypes';

interface SidebarProps {
  currentNav: string;
  onSelectNav: (id: string) => void;
  isOpen: boolean;
  onClose: () => void;
  emergencyStatus: EmergencyStopStatus | null;
  isCollapsed?: boolean;
  onToggleCollapse?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentNav,
  onSelectNav,
  isOpen,
  onClose,
  emergencyStatus,
  isCollapsed = false,
  onToggleCollapse,
}) => {
  const isEmergencyActive = emergencyStatus?.is_active ?? false;
  const [appVersion, setAppVersion] = useState<string>('');

  useEffect(() => {
    let mounted = true;
    apiClient
      .getVersion()
      .then(async (res) => {
        if (mounted && res.ok && res.data?.installed_version) {
          setAppVersion(res.data.installed_version);
          return;
        }
        const health = await apiClient.getHealth();
        if (mounted && health.ok && health.data?.version) {
          setAppVersion(health.data.version);
        }
      })
      .catch(() => { });
    return () => {
      mounted = false;
    };
  }, []);

  return (
    <>
      {isOpen && (
        <div
          className="sidebar-backdrop"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      <aside className={`sidebar ${isOpen ? 'open' : ''} ${isCollapsed ? 'collapsed' : ''}`}>
        {/* Brand Header */}
        <div className="sidebar-header">
          <div className="sidebar-brand-group">
            <div className="brand-logo">
              <img
                src="/voxpilot-logo.png"
                alt="VoxPilot Logo"
                className="brand-logo-img"
              />
            </div>
            <div className="brand-text">
              <span className="brand-title">VoxPilot</span>
              <span className="brand-subtitle">AI Desktop Agent</span>
            </div>
          </div>

          {onToggleCollapse && (
            <button
              type="button"
              className="sidebar-collapse-btn"
              onClick={onToggleCollapse}
              aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {isCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            </button>
          )}
        </div>

        {/* Navigation List */}
        <nav className="sidebar-nav">
          <div className="nav-section-title">Navigation</div>
          {NAV_ITEMS.map((item) => {
            const isActive = currentNav === item.id;
            return (
              <button
                key={item.id}
                className={`nav-item ${isActive ? 'active' : ''} ${!item.isFunctional ? 'upcoming' : ''}`}
                onClick={() => {
                  onSelectNav(item.id);
                  onClose();
                }}
                type="button"
                aria-current={isActive ? 'page' : undefined}
                title={!item.isFunctional ? `${item.label} (Planned for upcoming phase)` : item.label}
              >
                {item.icon}
                <span className="nav-item-label">{item.label}</span>
                {item.badge && <span className="nav-badge">{item.badge}</span>}
              </button>
            );
          })}
        </nav>

        {/* Footer: Emergency Stop Status & Version */}
        <div className="sidebar-footer">
          <div
            className={`emergency-status-badge ${isEmergencyActive ? 'active' : 'inactive'}`}
            title={
              isEmergencyActive
                ? `Emergency Stop Active: ${emergencyStatus?.trigger_reason || 'Manual trigger'}`
                : 'Emergency Stop Inactive (Hotkey: Ctrl+Alt+Escape)'
            }
          >
            {isEmergencyActive ? <ShieldAlert size={16} /> : <Shield size={16} />}
            <span>
              {isEmergencyActive ? 'Emergency Active' : 'Safety Latched OK'}
            </span>
          </div>

          <div className="version-info">
            <span>VoxPilot v{appVersion || '...'}</span>
            <span>API v{appVersion || '...'}</span>
          </div>
        </div>
      </aside>
    </>
  );
};

export default Sidebar;
