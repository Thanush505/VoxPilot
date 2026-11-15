import React, { useEffect, useState } from 'react';
import { ShieldAlert, Shield, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { EmergencyStopStatus } from '../../api/types';
import apiClient from '../../api/client';
import { NAV_ITEMS, NAV_SECTIONS } from './navTypes';
import { useAICore } from '../../context/AICoreContext';
import { AIOrb } from '../aicore/AIOrb';

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
  const { coreState, coreLabel, activeProviderLabel, audioTelemetry, isWindowVisible } =
    useAICore();

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
              data-testid="sidebar-collapse-toggle"
              aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {isCollapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
            </button>
          )}
        </div>

        {/* Navigation List */}
        <nav className="sidebar-nav">
          {NAV_SECTIONS.map((sec) => (
            <React.Fragment key={sec.id}>
              <div className="vp-sidebar-section-label">{sec.label}</div>
              {NAV_ITEMS.filter((item) => item.section === sec.id).map((item) => {
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
                    data-testid={`nav-item-${item.id}`}
                    aria-current={isActive ? 'page' : undefined}
                    title={!item.isFunctional ? `${item.label} (Planned for upcoming phase)` : item.label}
                  >
                    {item.icon}
                    <span className="nav-item-label">{item.label}</span>
                    {item.badge && <span className="nav-badge">{item.badge}</span>}
                  </button>
                );
              })}
            </React.Fragment>
          ))}
        </nav>

        {/* Footer: AI Core Mini Dock, Emergency Stop Status & Version */}
        <div className="sidebar-footer">
          <button
            type="button"
            className="vp-sidebar-core-dock"
            data-testid="sidebar-ai-core-dock"
            onClick={() => {
              onSelectNav('agent');
              onClose();
            }}
            title={`AI Core: ${coreLabel} (${activeProviderLabel}) — Open AI Workspace`}
          >
            <AIOrb
              state={coreState}
              size="sm"
              rmsLevel={audioTelemetry.rms_level}
              isWindowVisible={isWindowVisible}
            />
            <div className="vp-sidebar-core-text" style={{ textAlign: 'left', minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                AI Core: {coreLabel}
              </div>
              <div
                style={{
                  fontSize: 11,
                  color: 'var(--text-muted)',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {activeProviderLabel}
              </div>
            </div>
          </button>

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
