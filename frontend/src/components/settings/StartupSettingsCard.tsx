import React, { useEffect, useState } from 'react';
import { Power, CheckCircle2, AlertTriangle } from 'lucide-react';
import apiClient from '../../api/client';
import type { SystemStartupStatus } from '../../api/types';

interface StartupSettingsCardProps {
  isBackendConnected: boolean;
}

export const StartupSettingsCard: React.FC<StartupSettingsCardProps> = ({
  isBackendConnected,
}) => {
  const [startupStatus, setStartupStatus] = useState<SystemStartupStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const loadStatus = async () => {
    if (!isBackendConnected) return;
    setLoading(true);
    try {
      const res = await apiClient.getStartupStatus();
      if (res.ok && res.data) {
        setStartupStatus(res.data);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadStatus();
  }, [isBackendConnected]);

  const handleToggle = async (nextEnabled: boolean) => {
    setFeedback(null);
    setSaving(true);
    try {
      const res = await apiClient.updateStartupStatus(nextEnabled);
      if (res.ok && res.data) {
        setStartupStatus(res.data);
        setFeedback({
          type: 'success',
          text: res.data.enabled
            ? 'Wake-on-Startup enabled. VoxPilot will launch automatically when you sign in to Windows.'
            : 'Wake-on-Startup disabled. VoxPilot will no longer launch automatically at sign-in.',
        });
      } else {
        setFeedback({
          type: 'error',
          text: res.error || 'Could not update Windows startup setting.',
        });
      }
    } finally {
      setSaving(false);
    }
  };

  const canToggle =
    isBackendConnected &&
    !loading &&
    !saving &&
    Boolean(startupStatus?.supported) &&
    Boolean(startupStatus?.target_valid);

  return (
    <div className="settings-card" style={{ marginTop: '16px' }}>
      <div className="settings-card-header">
        <div className="settings-card-title">
          <Power size={18} />
          <h3>Wake-on-Startup (Windows Sign-In)</h3>
        </div>
        <span className="badge">
          {startupStatus?.enabled ? 'Enabled' : 'Disabled'}
        </span>
      </div>

      <p className="settings-description">
        Automatically launch VoxPilot when you sign in to your Windows user account (no administrator
        privileges required, profile-independent). You can disable automatic startup at any time by
        unchecking this option.
      </p>

      {startupStatus && (
        <div style={{ fontSize: '0.85rem', opacity: 0.85, marginBottom: '10px' }}>
          <span>
            Installation mode: <strong>{startupStatus.mode}</strong> | Startup support:{' '}
            <strong>
              {startupStatus.supported && startupStatus.target_valid ? 'Supported' : 'Unavailable'}
            </strong>
          </span>
        </div>
      )}

      {startupStatus?.reason && (
        <div className="feedback-banner error" style={{ marginBottom: '10px' }}>
          <AlertTriangle size={14} style={{ display: 'inline', marginRight: '6px' }} />
          <span>{startupStatus.reason}</span>
        </div>
      )}

      {feedback && (
        <div className={`feedback-banner ${feedback.type}`} style={{ marginBottom: '10px' }}>
          {feedback.type === 'success' && (
            <CheckCircle2 size={14} style={{ display: 'inline', marginRight: '6px' }} />
          )}
          <span>{feedback.text}</span>
        </div>
      )}

      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={Boolean(startupStatus?.enabled)}
          onChange={(e) => handleToggle(e.target.checked)}
          disabled={!canToggle}
        />
        <span>Launch VoxPilot automatically when I sign in to Windows</span>
      </label>
    </div>
  );
};

export default StartupSettingsCard;
