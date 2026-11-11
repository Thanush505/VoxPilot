import React from 'react';
import { ShieldAlert, CheckCircle2, XCircle, Code, Shield } from 'lucide-react';
import { PendingConfirmationInfo } from '../../api/types';

interface ConfirmationCardProps {
  confirmation: PendingConfirmationInfo;
  onApproveClick: (confirmation: PendingConfirmationInfo) => void;
  onRejectClick: (confirmation: PendingConfirmationInfo) => void;
  isProcessing?: boolean;
}

export const ConfirmationCard: React.FC<ConfirmationCardProps> = ({
  confirmation,
  onApproveClick,
  onRejectClick,
  isProcessing = false,
}) => {
  // Scrub sensitive fields for display
  const renderParameters = (params: Record<string, unknown>) => {
    try {
      const sanitized = JSON.stringify(
        params,
        (key, value) => {
          const lower = key.toLowerCase();
          if (
            lower.includes('token') ||
            lower.includes('password') ||
            lower.includes('secret') ||
            lower.includes('key')
          ) {
            return '[REDACTED]';
          }
          return value;
        },
        2
      );
      return sanitized;
    } catch {
      return '{ ... }';
    }
  };

  return (
    <div className="confirmation-card">
      <div className="confirmation-card-header">
        <div className="confirmation-title-group">
          <div className="confirmation-icon-circle">
            <ShieldAlert size={20} />
          </div>
          <div>
            <h3 className="confirmation-action-title">
              Action Approval Required: <code>{confirmation.action}</code>
            </h3>
            <span className="confirmation-execution-tag">
              Execution: <code>{confirmation.execution_id?.slice(0, 16)}...</code>
              {confirmation.step_index !== undefined && confirmation.step_index !== null && (
                <span> (Step {confirmation.step_index + 1})</span>
              )}
            </span>
          </div>
        </div>

        <div className="confirmation-badge-group">
          <span className="consequential-warning-pill">
            <Shield size={12} />
            <span>Destructive Action</span>
          </span>
        </div>
      </div>

      <div className="confirmation-card-body">
        {confirmation.description && (
          <p className="confirmation-desc-text">{confirmation.description}</p>
        )}

        {confirmation.parameters && Object.keys(confirmation.parameters).length > 0 && (
          <div className="parameters-preview-box">
            <div className="params-label-row">
              <Code size={12} />
              <span>Sanitized Tool Parameters:</span>
            </div>
            <pre className="params-code">{renderParameters(confirmation.parameters)}</pre>
          </div>
        )}
      </div>

      <div className="confirmation-card-actions">
        <button
          className="reject-action-btn"
          onClick={() => onRejectClick(confirmation)}
          disabled={isProcessing}
          type="button"
        >
          <XCircle size={15} />
          <span>Reject Action</span>
        </button>

        <button
          className="approve-action-btn"
          onClick={() => onApproveClick(confirmation)}
          disabled={isProcessing}
          type="button"
        >
          <CheckCircle2 size={15} />
          <span>Approve Action</span>
        </button>
      </div>
    </div>
  );
};

export default ConfirmationCard;

