import React from 'react';
import { ArrowLeft, Clock } from 'lucide-react';

interface UpcomingSectionProps {
  title: string;
  description: string;
  plannedPhase: string;
  onBackToDashboard: () => void;
}

export const UpcomingSection: React.FC<UpcomingSectionProps> = ({
  title,
  description,
  plannedPhase,
  onBackToDashboard,
}) => {
  return (
    <div className="upcoming-page-banner">
      <div className="upcoming-badge">
        <Clock size={12} />
        <span>{plannedPhase}</span>
      </div>
      <h2 style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-primary)' }}>
        {title}
      </h2>
      <p style={{ fontSize: '14px', color: 'var(--text-muted)', maxWidth: '520px', lineHeight: 1.6 }}>
        {description}
      </p>
      <button
        className="state-action-btn"
        onClick={onBackToDashboard}
        style={{ marginTop: '12px' }}
        type="button"
      >
        <ArrowLeft size={14} />
        <span>Return to Dashboard</span>
      </button>
    </div>
  );
};

export default UpcomingSection;

