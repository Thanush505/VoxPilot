import React from 'react';
import {
  X,
  Layers,
  Bot,
  Compass,
  MessageSquare,
  AlertTriangle,
  RotateCcw,
} from 'lucide-react';
import { ExecutionStatusResponse } from '../../api/types';
import ExecutionStep from './ExecutionStep';

interface ExecutionDetailsProps {
  execution: ExecutionStatusResponse | null;
  onClose: () => void;
}

export const ExecutionDetails: React.FC<ExecutionDetailsProps> = ({ execution, onClose }) => {
  if (!execution) return null;

  const results = execution.execution_results || [];

  return (
    <div className="execution-details-overlay" onClick={onClose}>
      <div className="execution-details-modal" onClick={(e) => e.stopPropagation()}>
        {/* Modal Header */}
        <div className="modal-header">
          <div className="modal-title-group">
            <Layers size={18} className="text-accent-blue" />
            <div>
              <h2 className="modal-title">Execution Details</h2>
              <span className="modal-subtitle">ID: {execution.execution_id}</span>
            </div>
          </div>
          <button className="modal-close-btn" onClick={onClose} type="button" aria-label="Close details">
            <X size={18} />
          </button>
        </div>

        {/* Modal Content */}
        <div className="modal-content-scroll">
          {/* User Request */}
          <div className="detail-section">
            <div className="section-title-row">
              <MessageSquare size={14} />
              <span>User Request</span>
            </div>
            <div className="user-request-box">{execution.user_input}</div>
          </div>

          {/* Status & Intent Meta */}
          <div className="detail-grid-row">
            <div className="detail-meta-box">
              <span className="meta-label">Status</span>
              <span className="meta-value" style={{ textTransform: 'capitalize' }}>
                {execution.execution_status}
              </span>
            </div>

            <div className="detail-meta-box">
              <span className="meta-label">Recognized Intent</span>
              <span className="meta-value">
                {execution.intent ? (
                  <span>
                    {execution.intent}{' '}
                    {execution.confidence !== undefined && execution.confidence !== null && (
                      <small style={{ color: 'var(--text-muted)' }}>
                        ({Math.round(execution.confidence * 100)}%)
                      </small>
                    )}
                  </span>
                ) : (
                  'General Conversation'
                )}
              </span>
            </div>

            {execution.conversation_id && (
              <div className="detail-meta-box">
                <span className="meta-label">Conversation ID</span>
                <span className="meta-value">
                  <code>{execution.conversation_id.slice(0, 12)}...</code>
                </span>
              </div>
            )}
          </div>

          {/* Generated Plan */}
          {execution.plan && execution.plan.length > 0 && (
            <div className="detail-section">
              <div className="section-title-row">
                <Compass size={14} />
                <span>Planned Action Sequence ({execution.plan.length} steps)</span>
              </div>
              <div className="plan-steps-table">
                {execution.plan.map((step, idx) => (
                  <div key={idx} className="plan-step-row">
                    <span className="plan-step-num">{step.step_index || idx + 1}</span>
                    <div className="plan-step-info">
                      <span className="plan-step-action">{step.action}</span>
                      {step.description && (
                        <span className="plan-step-desc">{step.description}</span>
                      )}
                    </div>
                    {step.is_consequential && (
                      <span className="consequential-tag">Consequential</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Execution Step Results */}
          <div className="detail-section">
            <div className="section-title-row">
              <Layers size={14} />
              <span>Step Results & Tool Outputs ({results.length})</span>
            </div>
            {results.length === 0 ? (
              <div className="no-steps-text">No tool execution steps recorded.</div>
            ) : (
              <div className="steps-container">
                {results.map((step, idx) => (
                  <ExecutionStep key={idx} step={step} index={idx} />
                ))}
              </div>
            )}
          </div>

          {/* Re-plan count if any */}
          {execution.replan_count !== undefined && execution.replan_count > 0 && (
            <div className="detail-section">
              <div className="replan-box">
                <RotateCcw size={15} />
                <span>
                  Dynamic re-planning loop triggered <b>{execution.replan_count}</b> time(s).
                </span>
              </div>
            </div>
          )}

          {/* Final Agent Response or Error */}
          {execution.response && (
            <div className="detail-section">
              <div className="section-title-row">
                <Bot size={14} />
                <span>Final Agent Spoken Response</span>
              </div>
              <div className="response-box">{execution.response}</div>
            </div>
          )}

          {execution.error && (
            <div className="detail-section">
              <div className="error-box">
                <AlertTriangle size={15} />
                <span>{execution.error}</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ExecutionDetails;

