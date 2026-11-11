import React from 'react';
import { Loader2 } from 'lucide-react';

interface LoadingStateProps {
  title?: string;
  message?: string;
}

export const LoadingState: React.FC<LoadingStateProps> = ({
  title = 'Loading data...',
  message = 'Connecting to backend service and fetching updates.',
}) => {
  return (
    <div className="state-container">
      <div className="state-icon-circle loading">
        <Loader2 size={24} />
      </div>
      <div className="state-title">{title}</div>
      <div className="state-description">{message}</div>
    </div>
  );
};

export default LoadingState;

