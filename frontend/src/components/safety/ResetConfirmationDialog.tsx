import React from 'react';
import { AlertTriangle, RefreshCw, XCircle, Loader2 } from 'lucide-react';

interface ResetConfirmationDialogProps {
  isOpen: boolean;
  onConfirmReset: () => void;
  onCancel: () => void;
  isLoading: boolean;
  activeOperationsCount: number;
  errorMessage?: string | null;
}

export const ResetConfirmationDialog: React.FC<ResetConfirmationDialogProps> = ({
  isOpen,
  onConfirmReset,
  onCancel,
  isLoading,
  activeOperationsCount,
  errorMessage,
}) => {
  if (!isOpen) return null;

  return (
    <div className="confirmation-modal-overlay" onClick={onCancel}>
      <div className="confirmation-modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="confirmation-modal-header">
          <div className="modal-header-icon-box" style={{ backgroundColor: 'var(--status-warning-bg)' }}>
            <AlertTriangle size={22} style={{ color: 'var(--status-warning)' }} />
          </div>
          <div>
            <h2 className="confirmation-modal-title">Confirm Emergency Stop Reset</h2>
            <span className="confirmation-modal-subtitle">
              Restore desktop and browser automation subsystems
            </span>
          </div>
        </div>

        <div className="confirmation-modal-body">
          <p style={{ fontSize: '13.5px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            Resetting the emergency stop will restore automation dispatch capabilities.
            Any in-flight operations that were cancelled will <b>NOT</b> be automatically resumed.
          </p>

          <div className="safety-warning-callout">
            <span className="callout-title">Safety Invariant Check:</span>
            <p>
              The backend strictly verifies that all tracked active operations have cleanly completed
              before allowing a reset.
            </p>
            <div className="active-ops-count-row">
              <span>Tracked Active Operations:</span>
              <b style={{ color: activeOperationsCount === 0 ? 'var(--status-success)' : 'var(--status-danger)' }}>
                {activeOperationsCount}
              </b>
            </div>
          </div>

          {activeOperationsCount > 0 && (
            <div className="dialog-error-banner">
              <XCircle size={15} />
              <span>
                Warning: {activeOperationsCount} active operation(s) are still registered.
                Standard reset will be rejected with HTTP 409 Conflict until all operations terminate.
              </span>
            </div>
          )}

          {errorMessage && (
            <div className="dialog-error-banner">
              <AlertTriangle size={15} />
              <span>{errorMessage}</span>
            </div>
          )}
        </div>

        <div className="confirmation-modal-footer">
          <button
            className="cancel-btn"
            onClick={onCancel}
            disabled={isLoading}
            type="button"
          >
            Cancel
          </button>

          <button
            className="approve-btn"
            onClick={onConfirmReset}
            disabled={isLoading}
            type="button"
          >
            {isLoading ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                <span>Resetting Latch...</span>
              </>
            ) : (
              <>
                <RefreshCw size={14} />
                <span>Reset Emergency Stop</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};

export default ResetConfirmationDialog;

