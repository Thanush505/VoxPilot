import React from 'react';
import { AlertCircle, RefreshCw } from 'lucide-react';

interface ErrorStateProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
  isRetrying?: boolean;
}

export const ErrorState: React.FC<ErrorStateProps> = ({
  title = 'Service Connection Error',
  message = 'Failed to connect to the VoxPilot backend. Ensure the FastAPI service is running on http://127.0.0.1:8000.',
  onRetry,
  isRetrying = false,
}) => {
  return (
    <div className="state-container">
      <div className="state-icon-circle error">
        <AlertCircle size={24} />
      </div>
      <div className="state-title">{title}</div>
      <div className="state-description">{message}</div>
      {onRetry && (
        <button
          className="state-action-btn"
          onClick={onRetry}
          disabled={isRetrying}
          type="button"
        >
          <RefreshCw size={14} className={isRetrying ? 'animate-spin' : ''} />
          {isRetrying ? 'Retrying...' : 'Retry Connection'}
        </button>
      )}
    </div>
  );
};

export default ErrorState;

