import React from 'react';
import { Bot, User, AlertTriangle, ShieldCheck, ZapOff, CheckCircle2 } from 'lucide-react';
import { ChatMessage as ChatMessageType } from '../../api/types';

interface ChatMessageProps {
  message: ChatMessageType;
  onNavigateToConfirmations?: () => void;
}

export const ChatMessage: React.FC<ChatMessageProps> = ({
  message,
  onNavigateToConfirmations,
}) => {
  const isUser = message.sender === 'user';
  const isSystem = message.sender === 'system';

  const formatTime = (date: Date) => {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  if (isSystem) {
    return (
      <div className="chat-system-message">
        <span>{message.text}</span>
      </div>
    );
  }

  return (
    <div className={`chat-message-row ${isUser ? 'user-row' : 'agent-row'}`}>
      <div className="chat-avatar">
        {isUser ? <User size={16} /> : <Bot size={16} />}
      </div>

      <div className="chat-bubble-container">
        <div className="chat-bubble-header">
          <span className="chat-sender-name">{isUser ? 'You' : 'VoxPilot Agent'}</span>
          <span className="chat-timestamp">{formatTime(message.timestamp)}</span>
        </div>

        <div className={`chat-bubble ${isUser ? 'user-bubble' : 'agent-bubble'}`}>
          <div className="chat-text-content">{message.text}</div>

          {/* If confirmation required */}
          {message.status === 'confirmation_required' && message.pendingConfirmation && (
            <div className="chat-confirmation-notice">
              <div className="confirmation-notice-header">
                <AlertTriangle size={15} style={{ color: 'var(--status-warning)' }} />
                <span>Human Approval Required</span>
              </div>
              <p className="confirmation-notice-desc">
                {message.pendingConfirmation.description ||
                  `Action '${message.pendingConfirmation.action}' requires your confirmation before execution.`}
              </p>
              {onNavigateToConfirmations && (
                <button
                  className="state-action-btn"
                  onClick={onNavigateToConfirmations}
                  style={{ marginTop: '8px', padding: '6px 12px', fontSize: '12px' }}
                  type="button"
                >
                  <ShieldCheck size={14} />
                  <span>Review Pending Confirmation</span>
                </button>
              )}
            </div>
          )}

          {/* Completed steps summary if present */}
          {message.completedSteps && message.completedSteps.length > 0 && (
            <div className="chat-steps-summary">
              <div className="steps-summary-title">
                <CheckCircle2 size={13} style={{ color: 'var(--status-success)' }} />
                <span>Executed Steps ({message.completedSteps.length}):</span>
              </div>
              <div className="steps-badges-row">
                {message.completedSteps.map((step, idx) => (
                  <span key={idx} className="step-pill">
                    {step.action || `step_${idx + 1}`}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Cancellation Notice */}
          {message.status === 'cancelled' && (
            <div className="chat-cancellation-notice">
              <ZapOff size={14} />
              <span>Automation halted safely by Emergency Stop. Completed steps preserved.</span>
            </div>
          )}

          {/* Rate limited Notice */}
          {message.status === 'rate_limited' && (
            <div className="chat-error-notice">
              <AlertTriangle size={14} />
              <span>Execution budget or rate limit reached. Halting safely.</span>
            </div>
          )}

          {/* Error Notice */}
          {message.error && message.status !== 'rate_limited' && (
            <div className="chat-error-notice">
              <AlertTriangle size={14} />
              <span>{message.error}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ChatMessage;

