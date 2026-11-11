import React, { useEffect, useState } from 'react';
import { ShieldAlert, Shield } from 'lucide-react';
import { EmergencyStopStatus } from '../../api/types';
import apiClient from '../../api/client';
import { NAV_ITEMS } from './navTypes';

interface SidebarProps {
  currentNav: string;
  onSelectNav: (id: string) => void;
  isOpen: boolean;
  onClose: () => void;
  emergencyStatus: EmergencyStopStatus | null;
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentNav,
  onSelectNav,
  isOpen,
  onClose,
  emergencyStatus,
}) => {
  const isEmergencyActive = emergencyStatus?.is_active ?? false;
  const [appVersion, setAppVersion] = useState<string>('0.5.0');

  useEffect(() => {
    let mounted = true;
    apiClient
      .getVersion()
      .then((res) => {
        if (mounted && res.ok && res.data?.installed_version) {
          setAppVersion(res.data.installed_version);
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

      <aside className={`sidebar ${isOpen ? 'open' : ''}`}>
        {/* Brand Header */}
        <div className="sidebar-header">
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
                <span>{item.label}</span>
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
            <span>VoxPilot v{appVersion}</span>
            <span>API v{appVersion}</span>
          </div>
        </div>
      </aside>
    </>
  );
};

export default Sidebar;
