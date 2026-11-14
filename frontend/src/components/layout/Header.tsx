import React from 'react';
import { RefreshCw, Menu, Settings } from 'lucide-react';
import { BackendConnectionState } from '../../hooks/useBackendHealth';
import { ProfileItem } from '../../api/types';
import { renderProfileAvatarIcon } from '../settings/ProfileManagementCard';

interface HeaderProps {
  pageTitle: string;
  connectionState: BackendConnectionState;
  lastChecked: Date | null;
  isLoading: boolean;
  onRefresh: () => void;
  onToggleSidebar: () => void;
  onSelectNav: (item: string) => void;
  profiles?: ProfileItem[];
  activeProfile?: ProfileItem | null;
  isProfileSwitching?: boolean;
  onSwitchProfile?: (profileId: string) => void;
}

export const Header: React.FC<HeaderProps> = ({
  pageTitle,
  connectionState,
  lastChecked,
  isLoading,
  onRefresh,
  onToggleSidebar,
  onSelectNav,
  profiles = [],
  activeProfile = null,
  isProfileSwitching = false,
  onSwitchProfile,
}) => {
  const formatTime = (date: Date | null): string => {
    if (!date) return 'Never';
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  return (
    <header className="header">
      <div className="header-left">
        <button
          className="menu-toggle"
          onClick={onToggleSidebar}
          aria-label="Toggle Navigation Sidebar"
          type="button"
        >
          <Menu size={20} />
        </button>
        <div className="header-title-container">
          <img
            src="/favicon-32x32.png"
            alt="VoxPilot"
            className="header-brand-icon"
          />
          <h1 className="header-page-title">{pageTitle}</h1>
        </div>
      </div>

      <div className="header-right">
        {/* Local Profile Switcher */}
        {connectionState === 'connected' && profiles.length > 0 && onSwitchProfile && (
          <div
            className="header-profile-switcher"
            data-testid="profile-switcher"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <span title={activeProfile?.description || activeProfile?.name || 'Active Profile'}>
              {renderProfileAvatarIcon(activeProfile?.avatar, 14)}
            </span>
            <select
              className="status-filter-select"
              value={activeProfile?.id || 'default'}
              onChange={(e) => onSwitchProfile(e.target.value)}
              disabled={isProfileSwitching}
              aria-label="Switch Active Profile"
              title="Switch active local profile"
              style={{ minWidth: 120, padding: '4px 8px', fontSize: 12 }}
            >
              {profiles.map((prof) => (
                <option key={prof.id} value={prof.id}>
                  {prof.name}{prof.is_default ? ' (Default)' : ''}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Backend Connectivity Status Pill */}
        <div className={`health-pill ${connectionState}`}>
          <span className={`status-dot ${connectionState === 'connected' ? 'pulse' : ''}`} />
          <span>
            {connectionState === 'connected' && 'Backend Connected'}
            {connectionState === 'disconnected' && 'Backend Disconnected'}
            {connectionState === 'checking' && 'Checking Backend...'}
          </span>
        </div>

        {/* Refresh Action */}
        <button
          className="refresh-btn"
          onClick={onRefresh}
          disabled={isLoading}
          title={`Last verified: ${formatTime(lastChecked)}`}
          type="button"
        >
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          <span>Refresh</span>
        </button>

        {/* Settings Action */}
        <button
          className="refresh-btn"
          onClick={() => onSelectNav('settings')}
          title="Open Settings"
          type="button"
        >
          <Settings size={14} />
          <span>Settings</span>
        </button>
      </div>
    </header>
  );
};

export default Header;

