import React from 'react';
import { RefreshCw, Menu, Settings, Sun, Moon, Cpu } from 'lucide-react';
import { BackendConnectionState } from '../../hooks/useBackendHealth';
import { ProfileItem } from '../../api/types';
import { renderProfileAvatarIcon } from '../settings/ProfileManagementCard';
import { useAICore } from '../../context/AICoreContext';
import { useTheme } from '../../theme/ThemeProvider';
import { AIOrb } from '../aicore/AIOrb';

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
  const {
    coreState,
    coreLabel,
    activeProviderLabel,
    activeModelLabel,
    audioTelemetry,
    isWindowVisible,
  } = useAICore();
  const { resolvedTheme, setThemeMode } = useTheme();

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

      <div className="header-right" style={{ gap: 10 }}>
        {/* Live AI Core Header Pill */}
        <button
          type="button"
          className="vp-header-core-pill"
          onClick={() => onSelectNav('agent')}
          data-testid="header-ai-core-pill"
          title={`AI Core: ${coreLabel} — Open AI Workspace`}
        >
          <AIOrb
            state={coreState}
            size="sm"
            rmsLevel={audioTelemetry.rms_level}
            isWindowVisible={isWindowVisible}
          />
          <span style={{ fontWeight: 600 }}>{coreLabel}</span>
        </button>

        {/* Active AI Provider Badge */}
        <button
          type="button"
          className="vp-header-chip vp-hide-compact"
          onClick={() => onSelectNav('settings')}
          data-testid="header-provider-chip"
          title={`Active Provider: ${activeProviderLabel} (${activeModelLabel})`}
        >
          <Cpu size={13} />
          <span>{activeProviderLabel}</span>
        </button>

        {/* Quick Light / Dark Theme Toggle */}
        <button
          type="button"
          className="vp-header-icon-btn"
          onClick={() => setThemeMode(resolvedTheme === 'dark' ? 'light' : 'dark')}
          aria-label={`Switch to ${resolvedTheme === 'dark' ? 'light' : 'dark'} theme`}
          title={`Switch to ${resolvedTheme === 'dark' ? 'light' : 'dark'} theme`}
          data-testid="header-theme-toggle"
        >
          {resolvedTheme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
        </button>
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

