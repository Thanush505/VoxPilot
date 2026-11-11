import React from 'react';
import {
  Clock,
  Loader2,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ShieldAlert,
  ZapOff,
  Layers,
} from 'lucide-react';
import { ExecutionStatusResponse } from '../../api/types';

interface ExecutionProgressProps {
  execution: Partial<ExecutionStatusResponse>;
  showDetails?: boolean;
}

export const ExecutionProgress: React.FC<ExecutionProgressProps> = ({
  execution,
  showDetails = true,
}) => {
  const status = execution.execution_status || 'pending';
  const plan = execution.plan || [];
  const completed = execution.completed_steps || [];
  const failed = execution.failed_steps || [];

  const totalSteps = plan.length;
  const completedCount = completed.length;
  const currentStepIndex = Math.min(completedCount + 1, totalSteps);
  const currentAction = plan[currentStepIndex - 1]?.action || 'Finalizing';

  const getStatusBadge = () => {
    switch (status) {
      case 'completed':
        return (
          <span className="health-pill connected">
            <CheckCircle2 size={13} />
            <span>Completed</span>
          </span>
        );
      case 'executing':
      case 'planning':
      case 'pending':
        return (
          <span className="health-pill checking">
            <Loader2 size={13} className="animate-spin" />
            <span style={{ textTransform: 'capitalize' }}>{status}...</span>
          </span>
        );
      case 'confirmation_required':
        return (
          <span className="health-pill checking" style={{ backgroundColor: 'var(--status-warning-bg)', color: 'var(--status-warning)' }}>
            <AlertTriangle size={13} />
            <span>Confirmation Required</span>
          </span>
        );
      case 'cancelled':
        return (
          <span className="health-pill disconnected" style={{ backgroundColor: 'var(--status-inactive-bg)', color: 'var(--status-inactive)' }}>
            <ZapOff size={13} />
            <span>Cancelled</span>
          </span>
        );
      case 'rate_limited':
        return (
          <span className="health-pill disconnected">
            <ShieldAlert size={13} />
            <span>Rate Limited</span>
          </span>
        );
      case 'failed':
      default:
        return (
          <span className="health-pill disconnected">
            <XCircle size={13} />
            <span>Failed</span>
          </span>
        );
    }
  };

  return (
    <div className="execution-progress-box">
      <div className="execution-progress-header">
        <div className="execution-id-group">
          <Layers size={14} className="text-muted" />
          <span className="execution-id-text">
            Execution: <code>{execution.execution_id?.slice(0, 16)}...</code>
          </span>
        </div>
        {getStatusBadge()}
      </div>

      {totalSteps > 0 && (
        <div className="execution-steps-summary">
          <div className="step-counter-text">
            <span>
              Step {completedCount} of {totalSteps} completed
            </span>
            {status === 'executing' && (
              <span className="current-action-text">
                Current action: <code>{currentAction}</code>
              </span>
            )}
          </div>
        </div>
      )}

      {execution.error && (
        <div className="execution-error-banner">
          <AlertTriangle size={14} />
          <span>{execution.error}</span>
        </div>
      )}

      {showDetails && completed.length > 0 && (
        <div className="completed-steps-list">
          {completed.map((step, idx) => (
            <div key={idx} className="step-item completed">
              <CheckCircle2 size={13} style={{ color: 'var(--status-success)' }} />
              <span>Step {step.step_index || idx + 1}:</span>
              <code>{step.action || 'tool'}</code>
              <span className="step-status-tag">Success</span>
            </div>
          ))}
          {failed.map((step, idx) => (
            <div key={`failed-${idx}`} className="step-item failed">
              <XCircle size={13} style={{ color: 'var(--status-danger)' }} />
              <span>Step {step.step_index || idx + 1}:</span>
              <code>{step.action || 'tool'}</code>
              <span className="step-status-tag danger">Failed</span>
            </div>
          ))}
        </div>
      )}

      {execution.replan_count !== undefined && execution.replan_count > 0 && (
        <div className="replan-notice-text">
          <Clock size={12} />
          <span>Dynamic re-planning triggered ({execution.replan_count} time(s))</span>
        </div>
      )}
    </div>
  );
};

export default ExecutionProgress;

