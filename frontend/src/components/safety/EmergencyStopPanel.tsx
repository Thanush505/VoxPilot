import React, { useState } from 'react';
import {
  ShieldAlert,
  ShieldCheck,
  RefreshCw,
  AlertTriangle,
  Loader2,
  Keyboard,
  Info,
} from 'lucide-react';
import { EmergencyStopStatus } from '../../api/types';
import apiClient from '../../api/client';
import ResetConfirmationDialog from './ResetConfirmationDialog';

interface EmergencyStopPanelProps {
  status: EmergencyStopStatus | null;
  onStatusChange: () => void;
}

export const EmergencyStopPanel: React.FC<EmergencyStopPanelProps> = ({
  status,
  onStatusChange,
}) => {
  const [isTriggering, setIsTriggering] = useState(false);
  const [isResetDialogOpen, setIsResetDialogOpen] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const isActive = status?.is_active ?? false;
  const activeOpsCount = status?.active_operations_count ?? 0;

  const handleTriggerStop = async () => {
    setIsTriggering(true);
    setErrorMsg(null);

    try {
      const res = await apiClient.triggerEmergencyStop('Manual emergency stop triggered via Frontend Control Panel.');
      if (res.ok) {
        onStatusChange();
      } else {
        setErrorMsg(res.error || 'Failed to trigger emergency stop.');
      }
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Error triggering emergency stop.');
    } finally {
      setIsTriggering(false);
    }
  };

  const handleConfirmReset = async () => {
    setIsResetting(true);
    setErrorMsg(null);

    try {
      const res = await apiClient.resetEmergencyStop();
      if (res.ok) {
        setIsResetDialogOpen(false);
        onStatusChange();
      } else {
        // If HTTP 409 Conflict, show clear explanation
        if (res.status === 409) {
          setErrorMsg(`Reset Rejected (HTTP 409 Conflict): ${res.error}`);
        } else {
          setErrorMsg(res.error || 'Failed to reset emergency stop.');
        }
      }
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Error resetting emergency stop.');
    } finally {
      setIsResetting(false);
    }
  };

  const formatTimestamp = (ts?: number | null) => {
    if (!ts) return null;
    return new Date(ts * 1000).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  };

  return (
    <>
      <div className={`emergency-stop-panel ${isActive ? 'active-latch' : 'normal-latch'}`}>
        <div className="stop-panel-left">
          <div className="stop-icon-container">
            {isActive ? (
              <ShieldAlert size={28} className="stop-alert-icon" />
            ) : (
              <ShieldCheck size={28} className="stop-safe-icon" />
            )}
          </div>

          <div className="stop-info-text">
            <div className="stop-title-row">
              <span className="stop-heading">
                {isActive ? 'EMERGENCY STOP ACTIVE' : 'Global Safety Latch: Inactive (Normal)'}
              </span>
              <span className={`status-pill ${isActive ? 'danger' : 'success'}`}>
                {isActive ? 'LATCED STOP' : 'NORMAL'}
              </span>
            </div>

            <p className="stop-description">
              {isActive ? (
                <>
                  All automation controllers have been safely cancelled. In-flight keyboard, mouse, and
                  browser actions were terminated. New commands will be rejected until explicitly reset.
                  {status?.trigger_reason && (
                    <span className="stop-meta-line">
                      Reason: <b>{status.trigger_reason}</b> (Source: {status.trigger_source || 'manual'} at{' '}
                      {formatTimestamp(status.triggered_at)})
                    </span>
                  )}
                </>
              ) : (
                'System is ready to execute approved automation commands. Hardware input cleanups and safety checks active.'
              )}
            </p>

            <div className="hotkey-hint-row">
              <Keyboard size={13} />
              <span>Windows Hotkey: <code>Ctrl + Alt + Escape</code> triggers instant OS-level halt.</span>
            </div>
          </div>
        </div>

        <div className="stop-panel-right">
          {isActive ? (
            <button
              className="reset-stop-btn"
              onClick={() => {
                setErrorMsg(null);
                setIsResetDialogOpen(true);
              }}
              disabled={isResetting}
              type="button"
            >
              <RefreshCw size={16} />
              <span>Reset Emergency Stop</span>
            </button>
          ) : (
            <button
              className="trigger-stop-btn"
              onClick={handleTriggerStop}
              disabled={isTriggering}
              title="Immediately halts all active desktop and browser automation"
              type="button"
            >
              {isTriggering ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  <span>HALTING...</span>
                </>
              ) : (
                <>
                  <ShieldAlert size={18} />
                  <span>EMERGENCY STOP</span>
                </>
              )}
            </button>
          )}
        </div>
      </div>

      {errorMsg && (
        <div className="panel-error-alert">
          <AlertTriangle size={15} />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Active Operations info if any */}
      {activeOpsCount > 0 && (
        <div className="active-ops-callout">
          <Info size={14} />
          <span>
            {activeOpsCount} active operation(s) are currently tracked in the fail-closed registry.
          </span>
        </div>
      )}

      <ResetConfirmationDialog
        isOpen={isResetDialogOpen}
        onConfirmReset={handleConfirmReset}
        onCancel={() => setIsResetDialogOpen(false)}
        isLoading={isResetting}
        activeOperationsCount={activeOpsCount}
        errorMessage={errorMsg}
      />
    </>
  );
};

export default EmergencyStopPanel;

