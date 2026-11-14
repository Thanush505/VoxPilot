import React, { useEffect, useState, useCallback } from 'react';
import { Bell, BellOff, CheckCircle2, AlertTriangle, RotateCcw, Send } from 'lucide-react';
import apiClient from '../../api/client';
import type { NotificationStatusResponse } from '../../api/types';

interface NotificationSettingsCardProps {
  isBackendConnected: boolean;
}

export const NotificationSettingsCard: React.FC<NotificationSettingsCardProps> = ({
  isBackendConnected,
}) => {
  const [status, setStatus] = useState<NotificationStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const loadStatus = useCallback(async () => {
    if (!isBackendConnected) return;
    setLoading(true);
    try {
      const res = await apiClient.getNotificationStatus();
      if (res.ok && res.data) {
        setStatus(res.data);
      }
    } finally {
      setLoading(false);
    }
  }, [isBackendConnected]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const handleUpdate = async (updates: {
    enabled?: boolean;
    suppress_when_active?: boolean;
    categories?: Record<string, boolean>;
  }) => {
    setFeedback(null);
    setSaving(true);
    try {
      const res = await apiClient.updateNotificationPreferences(updates);
      if (res.ok && res.data) {
        setStatus(res.data);
        setFeedback({
          type: 'success',
          text: 'Notification preferences saved for the active profile.',
        });
      } else {
        setFeedback({
          type: 'error',
          text: res.error || 'Could not update notification preferences.',
        });
      }
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    setFeedback(null);
    setSaving(true);
    try {
      const res = await apiClient.resetNotificationPreferences();
      if (res.ok && res.data) {
        setStatus(res.data);
        setFeedback({
          type: 'success',
          text: 'Notification preferences restored to defaults.',
        });
      } else {
        setFeedback({
          type: 'error',
          text: res.error || 'Could not restore default notification settings.',
        });
      }
    } finally {
      setSaving(false);
    }
  };

  const handleSendTest = async () => {
    setFeedback(null);
    setTesting(true);
    try {
      const res = await apiClient.sendTestNotification();
      if (res.ok && res.data) {
        setStatus(res.data.status);
        if (res.data.delivered) {
          setFeedback({
            type: 'success',
            text: 'Test Windows toast notification sent.',
          });
        } else {
          setFeedback({
            type: 'error',
            text: res.data.reason || 'Test notification could not be delivered.',
          });
        }
      } else {
        setFeedback({
          type: 'error',
          text: res.error || 'Failed to send test notification.',
        });
      }
    } finally {
      setTesting(false);
    }
  };

  const prefs = status?.preferences;
  const supported = status?.supported ?? false;
  const enabled = prefs?.enabled ?? false;
  const suppressWhenActive = prefs?.suppress_when_active ?? true;

  let statusBadgeText = 'Unavailable';
  let statusBadgeClass = 'disconnected';
  if (loading) {
    statusBadgeText = 'Checking...';
  } else if (!supported) {
    statusBadgeText = 'Unsupported OS';
  } else if (status?.last_error) {
    statusBadgeText = 'Delivery Warning';
  } else if (enabled) {
    statusBadgeText = 'Enabled';
    statusBadgeClass = 'connected';
  } else {
    statusBadgeText = 'Disabled';
  }

  return (
    <section className="settings-card" data-testid="notification-settings-card">
      <div className="settings-card-header">
        <div className="settings-card-title">
          {enabled ? <Bell size={18} /> : <BellOff size={18} />}
          <div>
            <h2>Desktop Notifications</h2>
            <p>
              Receive native Windows notifications for task completions, confirmations, and errors without exposing private content.
            </p>
          </div>
        </div>
        <span className={`status-badge ${statusBadgeClass}`} data-testid="notification-status-badge">
          {statusBadgeText}
        </span>
      </div>

      {!isBackendConnected && (
        <div className="settings-alert warning">
          <AlertTriangle size={16} />
          <span>Connect to the VoxPilot backend to configure desktop notifications.</span>
        </div>
      )}

      {status && !status.supported && (
        <div className="settings-alert warning">
          <AlertTriangle size={16} />
          <span>{status.reason || 'Native desktop notifications are only supported on Windows.'}</span>
        </div>
      )}

      {status?.last_error && (
        <div className="settings-alert warning">
          <AlertTriangle size={16} />
          <span>Last delivery issue: {status.last_error}</span>
        </div>
      )}

      <div className="settings-control-row">
        <div>
          <strong>Enable Desktop Notifications</strong>
          <p className="settings-help-text">
            Profile: <code>{status?.active_profile_id || 'default'}</code> &bull; Mode: <code>{status?.mode || 'development'}</code>
          </p>
        </div>
        <label className="toggle-switch">
          <input
            type="checkbox"
            checked={enabled}
            disabled={!isBackendConnected || !supported || saving}
            onChange={(e) => handleUpdate({ enabled: e.target.checked })}
            data-testid="notifications-master-toggle"
            aria-label="Enable Desktop Notifications"
          />
          <span className="toggle-slider" />
        </label>
      </div>

      <div className="settings-control-row">
        <div>
          <strong>Suppress While VoxPilot Is Active</strong>
          <p className="settings-help-text">
            Avoid interrupting you with notifications when the VoxPilot window is actively in use.
          </p>
        </div>
        <label className="toggle-switch">
          <input
            type="checkbox"
            checked={suppressWhenActive}
            disabled={!isBackendConnected || !supported || !enabled || saving}
            onChange={(e) => handleUpdate({ suppress_when_active: e.target.checked })}
            data-testid="notifications-suppress-active-toggle"
            aria-label="Suppress While VoxPilot Is Active"
          />
          <span className="toggle-slider" />
        </label>
      </div>

      {status?.categories_meta && (
        <div className="settings-subsection" style={{ marginTop: '0.75rem' }}>
          <h3 style={{ fontSize: '0.9rem', marginBottom: '0.5rem' }}>Notification Categories</h3>
          <div style={{ display: 'grid', gap: '0.5rem' }}>
            {status.categories_meta.map((cat) => {
              const catEnabled = prefs?.categories?.[cat.id] ?? true;
              return (
                <div key={cat.id} className="settings-control-row" style={{ padding: '0.35rem 0' }}>
                  <div>
                    <div style={{ fontWeight: 500, fontSize: '0.88rem' }}>{cat.label}</div>
                    <div className="settings-help-text" style={{ fontSize: '0.8rem' }}>
                      {cat.description}
                    </div>
                  </div>
                  <label className="toggle-switch">
                    <input
                      type="checkbox"
                      checked={catEnabled}
                      disabled={!isBackendConnected || !supported || !enabled || saving}
                      onChange={(e) =>
                        handleUpdate({
                          categories: { [cat.id]: e.target.checked },
                        })
                      }
                      data-testid={`notification-cat-${cat.id}`}
                      aria-label={cat.label}
                    />
                    <span className="toggle-slider" />
                  </label>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1rem', flexWrap: 'wrap' }}>
        <button
          type="button"
          className="refresh-btn"
          onClick={handleSendTest}
          disabled={!isBackendConnected || !supported || testing}
          data-testid="send-test-notification-btn"
        >
          <Send size={14} />
          <span>{testing ? 'Sending...' : 'Send Test Notification'}</span>
        </button>

        <button
          type="button"
          className="refresh-btn"
          onClick={handleReset}
          disabled={!isBackendConnected || saving}
          data-testid="reset-notifications-btn"
        >
          <RotateCcw size={14} />
          <span>Restore Defaults</span>
        </button>
      </div>

      {feedback && (
        <div
          className={`settings-alert ${feedback.type === 'success' ? 'success' : 'error'}`}
          style={{ marginTop: '0.75rem' }}
          data-testid="notification-feedback"
        >
          {feedback.type === 'success' ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
          <span>{feedback.text}</span>
        </div>
      )}
    </section>
  );
};

export default NotificationSettingsCard;
