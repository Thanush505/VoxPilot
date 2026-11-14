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

      {execution.user_input && (
        <div className="execution-current-task" data-testid="execution-current-task">
          <span className="text-muted">Task:</span>{' '}
          <span>{execution.user_input}</span>
        </div>
      )}

      {totalSteps > 0 && (
        <div className="execution-steps-summary">
          <div className="step-counter-text">
            <span>
              Step {completedCount} of {totalSteps} completed
            </span>
            {(status === 'executing' || status === 'running' || status === 'planning') && (
              <span className="current-action-text">
                Current action: <code>{currentAction}</code>
              </span>
            )}
          </div>
        </div>
      )}

      {showDetails && plan.length > 0 && (
        <div className="completed-steps-list" data-testid="execution-plan-steps">
          {plan.map((step, idx) => {
            const isCompleted = idx < completedCount;
            const isFailed = failed.some((f) => (f.step_index ?? idx) === idx) || (status === 'failed' && idx === completedCount);
            const isRunning = !isCompleted && !isFailed && idx === completedCount && (status === 'executing' || status === 'running');
            const isCancelled = status === 'cancelled' && !isCompleted;
            return (
              <div
                key={idx}
                className={`step-item ${isCompleted ? 'completed' : isFailed ? 'failed' : isRunning ? 'running' : ''}`}
              >
                {isCompleted ? (
                  <CheckCircle2 size={13} style={{ color: 'var(--status-success)' }} />
                ) : isFailed ? (
                  <XCircle size={13} style={{ color: 'var(--status-danger)' }} />
                ) : isRunning ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : isCancelled ? (
                  <ZapOff size={13} style={{ color: 'var(--status-inactive)' }} />
                ) : (
                  <Clock size={13} className="text-muted" />
                )}
                <span>Step {idx + 1}:</span>
                <code>{step.action || 'tool'}</code>
                {step.description && <span className="text-muted">— {step.description}</span>}
              </div>
            );
          })}
        </div>
      )}

      {status === 'confirmation_required' && execution.pending_confirmation && (
        <div className="execution-error-banner" style={{ borderColor: 'var(--status-warning)', color: 'var(--status-warning)' }}>
          <AlertTriangle size={14} />
          <span>
            Confirmation required for <code>{execution.pending_confirmation.action}</code>. Review in Confirmations.
          </span>
        </div>
      )}

      {status === 'cancelled' && (
        <div className="execution-error-banner">
          <ZapOff size={14} />
          <span>Execution cancelled safely.</span>
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

