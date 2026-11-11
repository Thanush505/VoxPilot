import React, { useState } from 'react';
import { CheckCircle2, XCircle, AlertTriangle, ChevronDown, ChevronRight, Terminal } from 'lucide-react';
import { ExecutionStepResult } from '../../api/types';

interface ExecutionStepProps {
  step: ExecutionStepResult;
  index: number;
}

export const ExecutionStep: React.FC<ExecutionStepProps> = ({ step, index }) => {
  const [expanded, setExpanded] = useState(false);

  const isSuccess = step.status === 'completed' || step.status === 'success';
  const isFailed = step.status === 'failed';

  // Scrub sensitive values for presentation
  const sanitizeDisplay = (val: unknown): string => {
    if (!val) return '';
    try {
      const json = JSON.stringify(val, (key, value) => {
        const lowerKey = key.toLowerCase();
        if (
          lowerKey.includes('token') ||
          lowerKey.includes('password') ||
          lowerKey.includes('secret') ||
          lowerKey.includes('key') ||
          lowerKey.includes('credential')
        ) {
          return '[REDACTED]';
        }
        return value;
      }, 2);
      return json;
    } catch {
      return String(val);
    }
  };

  return (
    <div className={`step-card ${isSuccess ? 'success' : isFailed ? 'failed' : 'neutral'}`}>
      <div className="step-card-header" onClick={() => setExpanded(!expanded)}>
        <div className="step-header-left">
          {isSuccess ? (
            <CheckCircle2 size={16} style={{ color: 'var(--status-success)' }} />
          ) : isFailed ? (
            <XCircle size={16} style={{ color: 'var(--status-danger)' }} />
          ) : (
            <AlertTriangle size={16} style={{ color: 'var(--status-warning)' }} />
          )}
          <span className="step-index-badge">Step {step.step_index || index + 1}</span>
          <span className="step-action-name">{step.action || 'Unknown Action'}</span>
        </div>

        <div className="step-header-right">
          <span className={`step-status-tag ${isSuccess ? 'success' : isFailed ? 'danger' : 'warning'}`}>
            {step.status}
          </span>
          <button className="expand-button" type="button" aria-label="Toggle details">
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="step-card-body">
          {step.error && (
            <div className="step-error-banner">
              <AlertTriangle size={14} />
              <span>{step.error}</span>
            </div>
          )}

          {step.result !== undefined && step.result !== null && (
            <div className="step-result-block">
              <div className="step-result-label">
                <Terminal size={12} />
                <span>Result Output:</span>
              </div>
              <pre className="step-code-preview">{sanitizeDisplay(step.result)}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default ExecutionStep;

