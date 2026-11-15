import React from 'react';

export interface PageSectionHeaderProps {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  badge?: React.ReactNode;
  actions?: React.ReactNode;
  testId?: string;
}

export const PageSectionHeader: React.FC<PageSectionHeaderProps> = ({
  icon,
  title,
  subtitle,
  badge,
  actions,
  testId,
}) => {
  return (
    <div className="vp-page-header" data-testid={testId}>
      <div className="vp-page-header-left">
        <div className="vp-page-icon-badge">{icon}</div>
        <div>
          <div className="vp-page-title-row">
            <h1 className="vp-page-title">{title}</h1>
            {badge}
          </div>
          {subtitle && <p className="vp-page-subtitle">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="vp-page-header-actions">{actions}</div>}
    </div>
  );
};

export interface GlassCardProps {
  children: React.ReactNode;
  elevated?: boolean;
  className?: string;
  testId?: string;
}

export const GlassCard: React.FC<GlassCardProps> = ({
  children,
  elevated = false,
  className = '',
  testId,
}) => {
  return (
    <div
      className={`vp-glass-card ${elevated ? 'vp-glass-card--elevated' : ''} ${className}`.trim()}
      data-testid={testId}
    >
      {children}
    </div>
  );
};

export type PillVariant = 'accent' | 'success' | 'warning' | 'danger' | 'info' | 'neutral';

export interface StatusPillProps {
  variant?: PillVariant;
  icon?: React.ReactNode;
  showDot?: boolean;
  children: React.ReactNode;
  testId?: string;
  className?: string;
}

export const StatusPill: React.FC<StatusPillProps> = ({
  variant = 'neutral',
  icon,
  showDot = false,
  children,
  testId,
  className = '',
}) => {
  return (
    <span
      className={`vp-pill vp-pill--${variant} ${className}`.trim()}
      data-testid={testId}
    >
      {showDot && <span className="vp-pill-dot" />}
      {icon}
      <span>{children}</span>
    </span>
  );
};

export interface SegmentedOption<T extends string> {
  id: T;
  label: string;
  icon?: React.ReactNode;
  disabled?: boolean;
}

export interface SegmentedControlProps<T extends string> {
  options: Array<SegmentedOption<T>>;
  value: T;
  onChange: (value: T) => void;
  ariaLabel?: string;
  testIdPrefix?: string;
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  testIdPrefix,
}: SegmentedControlProps<T>) {
  return (
    <div className="vp-segmented" role="tablist" aria-label={ariaLabel}>
      {options.map((opt) => {
        const active = opt.id === value;
        return (
          <button
            key={opt.id}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={opt.disabled}
            data-testid={testIdPrefix ? `${testIdPrefix}-${opt.id}` : undefined}
            className={`vp-segmented-btn ${active ? 'is-active' : ''}`}
            onClick={() => onChange(opt.id)}
          >
            {opt.icon}
            <span>{opt.label}</span>
          </button>
        );
      })}
    </div>
  );
}

export interface QuickActionChipProps {
  icon?: React.ReactNode;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  testId?: string;
}

export const QuickActionChip: React.FC<QuickActionChipProps> = ({
  icon,
  label,
  onClick,
  disabled = false,
  testId,
}) => {
  return (
    <button
      type="button"
      className="vp-chip-btn"
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
};

export const KeycapBadge: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <kbd className="vp-keycap">{children}</kbd>
);
