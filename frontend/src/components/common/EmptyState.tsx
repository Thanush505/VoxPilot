import React from 'react';
import { Inbox } from 'lucide-react';

interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description: string;
  actionText?: string;
  onAction?: () => void;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  icon,
  title,
  description,
  actionText,
  onAction,
}) => {
  return (
    <div className="state-container">
      <div className="state-icon-circle">
        {icon || <Inbox size={24} />}
      </div>
      <div className="state-title">{title}</div>
      <div className="state-description">{description}</div>
      {actionText && onAction && (
        <button className="state-action-btn" onClick={onAction} type="button">
          {actionText}
        </button>
      )}
    </div>
  );
};

export default EmptyState;

