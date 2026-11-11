import React from 'react';

export type StatusVariant = 'success' | 'warning' | 'danger' | 'neutral' | 'info';

interface StatusCardProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  badgeText?: string;
  badgeVariant?: StatusVariant;
  detail?: string;
  onClick?: () => void;
}

export const StatusCard: React.FC<StatusCardProps> = ({
  icon,
  label,
  value,
  badgeText,
  badgeVariant = 'neutral',
  detail,
  onClick,
}) => {
  return (
    <div
      className={`status-card ${onClick ? 'clickable' : ''}`}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
    >
      <div className="status-card-header">
        <div className="status-card-title-group">
          <div className="status-card-icon">{icon}</div>
          <span className="status-card-label">{label}</span>
        </div>
        {badgeText && (
          <span className={`health-pill ${badgeVariant === 'success' ? 'connected' : badgeVariant === 'danger' ? 'disconnected' : 'checking'}`}>
            <span className="status-dot" />
            <span>{badgeText}</span>
          </span>
        )}
      </div>

      <div className="status-card-value">{value}</div>

      {detail && <div className="status-card-detail">{detail}</div>}
    </div>
  );
};

export default StatusCard;

