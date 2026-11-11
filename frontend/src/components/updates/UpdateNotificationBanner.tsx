import React, { useState, useEffect, useCallback } from 'react';
import {
  Download,
  CheckCircle2,
  AlertTriangle,
  ExternalLink,
  Clock,
  ShieldCheck,
  X,
  RefreshCw,
  FileText,
  Sparkles,
} from 'lucide-react';
import apiClient from '../../api/client';
import { UpdateStatusResponse } from '../../api/types';
import { formatBytes, formatPublishedDate } from '../../utils/updateUtils';

interface UpdateNotificationBannerProps {
  isBackendConnected: boolean;
  onOpenSettings?: () => void;
}

export const UpdateNotificationBanner: React.FC<UpdateNotificationBannerProps> = ({
  isBackendConnected,
  onOpenSettings,
}) => {
  const [updateStatus, setUpdateStatus] = useState<UpdateStatusResponse | null>(null);
  const [showReleaseNotes, setShowReleaseNotes] = useState<boolean>(false);
  const [showConfirmInstallModal, setShowConfirmInstallModal] = useState<boolean>(false);
  const [relaunchAfterUpdate, setRelaunchAfterUpdate] = useState<boolean>(true);
  const [isActionPending, setIsActionPending] = useState<boolean>(false);
  const [localDismissed, setLocalDismissed] = useState<boolean>(false);

  const fetchUpdateStatus = useCallback(async () => {
    if (!isBackendConnected) return;
    const res = await apiClient.getUpdateStatus();
    if (res.ok && res.data) {
      setUpdateStatus(res.data);
      // Perform non-blocking automatic startup check if auto_check_enabled and state is idle
      if (
        res.data.state === 'idle' &&
        res.data.auto_check_enabled &&
        !res.data.last_checked_at
      ) {
        const checkRes = await apiClient.checkForUpdates(false);
        if (checkRes.ok && checkRes.data) {
          setUpdateStatus(checkRes.data);
        }
      }
    }
  }, [isBackendConnected]);

  useEffect(() => {
    fetchUpdateStatus();
  }, [fetchUpdateStatus]);

  // Poll rapidly while downloading or verifying
  useEffect(() => {
    if (!isBackendConnected || !updateStatus) return;
    if (updateStatus.state !== 'downloading' && updateStatus.state !== 'verifying') return;

    const timer = setInterval(async () => {
      const res = await apiClient.getUpdateStatus();
      if (res.ok && res.data) {
        setUpdateStatus(res.data);
      }
    }, 600);

    return () => clearInterval(timer);
  }, [isBackendConnected, updateStatus]);

  const handleUpdateNow = async () => {
    setIsActionPending(true);
    try {
      const res = await apiClient.startUpdateDownload(true);
      if (res.ok && res.data) {
        setUpdateStatus(res.data);
      }
    } finally {
      setIsActionPending(false);
    }
  };

  const handleCancelDownload = async () => {
    setIsActionPending(true);
    try {
      const res = await apiClient.cancelUpdateDownload();
      if (res.ok && res.data) {
        setUpdateStatus(res.data);
      }
    } finally {
      setIsActionPending(false);
    }
  };

  const handleRemindMeLater = async () => {
    setLocalDismissed(true);
    const res = await apiClient.dismissUpdateNotification(updateStatus?.latest_version || undefined);
    if (res.ok && res.data) {
      setUpdateStatus(res.data);
    }
  };

  const handleConfirmInstall = async () => {
    setIsActionPending(true);
    try {
      const res = await apiClient.installUpdate(true, relaunchAfterUpdate, true);
      if (res.ok && res.data) {
        setUpdateStatus(res.data);
        setShowConfirmInstallModal(false);
      }
    } finally {
      setIsActionPending(false);
    }
  };

  if (!updateStatus || localDismissed || updateStatus.notification_dismissed) {
    return null;
  }

  const shouldRenderBanner =
    updateStatus.update_available ||
    updateStatus.state === 'downloading' ||
    updateStatus.state === 'verifying' ||
    updateStatus.state === 'ready_to_install' ||
    updateStatus.state === 'installing';

  if (!shouldRenderBanner) {
    return null;
  }

  const release = updateStatus.release;
  const dl = updateStatus.download;

  return (
    <>
      <div
        className={`update-notification-banner state-${updateStatus.state}`}
        role="region"
        aria-label="Software Update Notification"
      >
        <div className="update-banner-main">
          <div className="update-banner-info">
            <div className="update-banner-icon">
              {updateStatus.state === 'ready_to_install' ? (
                <ShieldCheck size={18} />
              ) : updateStatus.state === 'downloading' || updateStatus.state === 'verifying' ? (
                <RefreshCw size={18} className="animate-spin" />
              ) : (
                <Sparkles size={18} />
              )}
            </div>

            <div className="update-banner-text">
              <div className="update-banner-headline">
                <strong>
                  {release?.title || `VoxPilot v${updateStatus.latest_version} Available`}
                </strong>
                <span className="update-version-pill">
                  v{updateStatus.installed_version} &rarr; v{updateStatus.latest_version}
                </span>
                {release?.published_at && (
                  <span className="update-date-pill">
                    <Clock size={12} />
                    <span>{formatPublishedDate(release.published_at)}</span>
                  </span>
                )}
              </div>

              <div className="update-banner-subtext">
                {updateStatus.state === 'downloading' && (
                  <span>
                    Downloading official installer ({formatBytes(dl.downloaded_bytes)} /{' '}
                    {formatBytes(dl.total_bytes || release?.installer_size_bytes)} &bull;{' '}
                    {dl.progress_pct}%)
                  </span>
                )}
                {updateStatus.state === 'verifying' && (
                  <span>Verifying installer SHA-256 checksum and package integrity...</span>
                )}
                {updateStatus.state === 'ready_to_install' && (
                  <span>
                    SHA-256 integrity verified ({dl.computed_sha256?.slice(0, 12)}...). Ready to
                    install after your confirmation.
                  </span>
                )}
                {updateStatus.state === 'installing' && (
                  <span>
                    Launching external Windows updater. VoxPilot will close and upgrade...
                  </span>
                )}
                {updateStatus.state === 'update_available' && (
                  <span>
                    A newer official release of VoxPilot is available for download (
                    {formatBytes(release?.installer_size_bytes)}).
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="update-banner-actions">
            {updateStatus.state === 'update_available' && (
              <button
                type="button"
                className="state-action-btn update-primary-btn"
                onClick={handleUpdateNow}
                disabled={isActionPending}
              >
                <Download size={14} />
                <span>Update Now</span>
              </button>
            )}

            {updateStatus.state === 'downloading' && (
              <button
                type="button"
                className="refresh-btn"
                onClick={handleCancelDownload}
                disabled={isActionPending}
              >
                <X size={14} />
                <span>Cancel Download</span>
              </button>
            )}

            {updateStatus.state === 'ready_to_install' && (
              <button
                type="button"
                className="state-action-btn update-install-btn"
                onClick={() => setShowConfirmInstallModal(true)}
                disabled={isActionPending}
              >
                <CheckCircle2 size={14} />
                <span>Install Update</span>
              </button>
            )}

            <button
              type="button"
              className="refresh-btn"
              onClick={() => setShowReleaseNotes((prev) => !prev)}
            >
              <FileText size={14} />
              <span>{showReleaseNotes ? 'Hide Release Notes' : 'View Release Notes'}</span>
            </button>

            {release?.release_url && (
              <a
                href={release.release_url}
                target="_blank"
                rel="noopener noreferrer"
                className="refresh-btn"
                style={{ textDecoration: 'none' }}
              >
                <ExternalLink size={14} />
                <span>GitHub Release</span>
              </a>
            )}

            {onOpenSettings && (
              <button type="button" className="refresh-btn" onClick={onOpenSettings}>
                <span>Update Settings</span>
              </button>
            )}

            {updateStatus.state !== 'installing' && (
              <button
                type="button"
                className="refresh-btn"
                onClick={handleRemindMeLater}
                title="Dismiss this update notification for now"
              >
                <span>Remind Me Later</span>
              </button>
            )}
          </div>
        </div>

        {(updateStatus.state === 'downloading' || updateStatus.state === 'verifying') && (
          <div className="update-progress-track" role="progressbar" aria-valuenow={dl.progress_pct}>
            <div
              className="update-progress-fill"
              style={{ width: `${Math.max(2, Math.min(100, dl.progress_pct))}%` }}
            />
          </div>
        )}

        {showReleaseNotes && (
          <div className="update-release-notes-box">
            <div className="update-release-notes-header">
              <span>Release Notes — {release?.title || `v${updateStatus.latest_version}`}</span>
              {release?.expected_sha256 && (
                <code className="update-checksum-tag">
                  SHA-256: {release.expected_sha256}
                </code>
              )}
            </div>
            <pre className="update-release-notes-body">
              {release?.notes || 'No release notes provided for this version.'}
            </pre>
          </div>
        )}
      </div>

      {/* Explicit Installation Confirmation Modal */}
      {showConfirmInstallModal && (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-update-install-title"
        >
          <div className="modal-card update-confirm-modal">
            <div className="modal-header">
              <div className="dashboard-card-title-group">
                <ShieldCheck size={20} className="text-accent-blue" />
                <h2 id="confirm-update-install-title" className="dashboard-card-title">
                  Confirm VoxPilot Update Installation
                </h2>
              </div>
              <button
                type="button"
                className="refresh-btn"
                onClick={() => setShowConfirmInstallModal(false)}
                disabled={isActionPending}
                aria-label="Close confirmation dialog"
              >
                <X size={16} />
              </button>
            </div>

            <div className="modal-body">
              <div className="panel-error-alert" style={{ marginBottom: '12px' }}>
                <AlertTriangle size={16} />
                <span>
                  Please save any active work before proceeding. VoxPilot must close so the
                  verified Windows installer can upgrade the application files safely.
                </span>
              </div>

              <div className="update-confirm-details">
                <div>
                  <strong>Current Version:</strong> v{updateStatus.installed_version}
                </div>
                <div>
                  <strong>New Version:</strong> v{updateStatus.latest_version}
                </div>
                <div>
                  <strong>Verified SHA-256:</strong>{' '}
                  <code>{dl.computed_sha256 || release?.expected_sha256}</code>
                </div>
                {dl.signature && (
                  <div>
                    <strong>Verification Status:</strong> {dl.signature.publisher_verification_note}
                  </div>
                )}
                <div>
                  <strong>Data Preservation:</strong> Your SQLite database, conversations, audit
                  logs, voice settings, and Ollama models in{' '}
                  <code>%LOCALAPPDATA%\VoxPilot</code> will be preserved intact.
                </div>
              </div>

              <label className="checkbox-label" style={{ marginTop: '14px' }}>
                <input
                  type="checkbox"
                  checked={relaunchAfterUpdate}
                  onChange={(e) => setRelaunchAfterUpdate(e.target.checked)}
                />
                <span>Automatically relaunch VoxPilot after the update completes</span>
              </label>
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="refresh-btn"
                onClick={() => setShowConfirmInstallModal(false)}
                disabled={isActionPending}
              >
                <span>Cancel</span>
              </button>
              <button
                type="button"
                className="state-action-btn update-install-btn"
                style={{ marginTop: 0 }}
                onClick={handleConfirmInstall}
                disabled={isActionPending}
              >
                <CheckCircle2 size={14} />
                <span>{isActionPending ? 'Starting Installer...' : 'Close VoxPilot & Install Update'}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default UpdateNotificationBanner;
