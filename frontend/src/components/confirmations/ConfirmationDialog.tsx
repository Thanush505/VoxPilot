import React, { useState } from 'react';
import { ShieldAlert, CheckCircle2, XCircle, AlertTriangle, Loader2 } from 'lucide-react';
import { PendingConfirmationInfo } from '../../api/types';

interface ConfirmationDialogProps {
  isOpen: boolean;
  confirmation: PendingConfirmationInfo | null;
  actionType: 'approve' | 'reject';
  onConfirm: (reason?: string) => void;
  onCancel: () => void;
  isLoading: boolean;
  errorMessage?: string | null;
}

export const ConfirmationDialog: React.FC<ConfirmationDialogProps> = ({
  isOpen,
  confirmation,
  actionType,
  onConfirm,
  onCancel,
  isLoading,
  errorMessage,
}) => {
  const [rejectReason, setRejectReason] = useState('');

  if (!isOpen || !confirmation) return null;

  const isApprove = actionType === 'approve';

  return (
    <div className="confirmation-modal-overlay" onClick={onCancel}>
      <div className="confirmation-modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="confirmation-modal-header">
          <div className="modal-header-icon-box">
            {isApprove ? (
              <ShieldAlert size={22} style={{ color: 'var(--status-warning)' }} />
            ) : (
              <XCircle size={22} style={{ color: 'var(--status-danger)' }} />
            )}
          </div>
          <div>
            <h2 className="confirmation-modal-title">
              {isApprove ? 'Confirm Consequential Action' : 'Reject Consequential Action'}
            </h2>
            <span className="confirmation-modal-subtitle">
              Execution ID: <code>{confirmation.execution_id?.slice(0, 16)}...</code>
            </span>
          </div>
        </div>

        <div className="confirmation-modal-body">
          <div className="action-summary-box">
            <span className="action-label">Target Action:</span>
            <code className="action-name">{confirmation.action}</code>
            {confirmation.description && (
              <p className="action-description">{confirmation.description}</p>
            )}
          </div>

          {/* Development Trust Boundary Security Notice */}
          <div className="dev-trust-notice">
            <AlertTriangle size={15} style={{ flexShrink: 0 }} />
            <p>
              <b>Development Security Notice:</b> Approving this action submits authorization via
              localhost Origin verification (127.0.0.1). Full cryptographic user credential authentication
              is scheduled for production hardening. Only approve actions you explicitly initiated.
            </p>
          </div>

          {!isApprove && (
            <div className="rejection-reason-group">
              <label htmlFor="rejectionReason" className="rejection-label">
                Optional Reason for Rejection:
              </label>
              <input
                id="rejectionReason"
                type="text"
                className="rejection-input"
                placeholder="e.g., Unintended target directory or duplicate action"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                disabled={isLoading}
              />
            </div>
          )}

          {errorMessage && (
            <div className="dialog-error-banner">
              <AlertTriangle size={14} />
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

          {isApprove ? (
            <button
              className="approve-btn"
              onClick={() => onConfirm()}
              disabled={isLoading}
              type="button"
            >
              {isLoading ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  <span>Approving & Resuming...</span>
                </>
              ) : (
                <>
                  <CheckCircle2 size={14} />
                  <span>Authorize & Execute</span>
                </>
              )}
            </button>
          ) : (
            <button
              className="reject-btn"
              onClick={() => onConfirm(rejectReason)}
              disabled={isLoading}
              type="button"
            >
              {isLoading ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  <span>Rejecting...</span>
                </>
              ) : (
                <>
                  <XCircle size={14} />
                  <span>Confirm Rejection</span>
                </>
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default ConfirmationDialog;

