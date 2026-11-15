import React from 'react';
import {
  Sparkles,
  Mic,
  Brain,
  Compass,
  Zap,
  Volume2,
  Check,
  AlertTriangle,
} from 'lucide-react';
import type { AICoreState } from '../../context/AICoreContext';
import { useTheme } from '../../theme/ThemeProvider';

export interface AIOrbProps {
  state: AICoreState;
  size?: 'sm' | 'md' | 'lg';
  rmsLevel?: number;
  isWindowVisible?: boolean;
  onClick?: () => void;
  ariaLabel?: string;
}

const STATE_ICONS: Record<AICoreState, React.ReactNode> = {
  idle: <Sparkles size={16} />,
  listening: <Mic size={18} />,
  thinking: <Brain size={17} />,
  planning: <Compass size={17} />,
  executing: <Zap size={17} />,
  speaking: <Volume2 size={18} />,
  completed: <Check size={18} />,
  error: <AlertTriangle size={17} />,
};

export const AIOrb: React.FC<AIOrbProps> = ({
  state,
  size = 'md',
  rmsLevel = 0,
  isWindowVisible = true,
  onClick,
  ariaLabel = 'VoxPilot AI Core',
}) => {
  const {
    animationsEnabled,
    aiCoreAnimationEnabled,
    voiceOrbEnabled,
    effectiveReducedMotion,
    motionIntensity,
  } = useTheme();

  const shouldAnimate =
    animationsEnabled &&
    aiCoreAnimationEnabled &&
    voiceOrbEnabled &&
    !effectiveReducedMotion &&
    isWindowVisible;

  const intensityMultiplier =
    motionIntensity === 'subtle' ? 0.35 : motionIntensity === 'expressive' ? 0.85 : 0.6;
  const clampedRms = Math.max(0, Math.min(1, rmsLevel * 4.5));
  const audioScale = shouldAnimate ? 1 + clampedRms * 0.22 * intensityMultiplier : 1;

  const iconSizeClass = size === 'sm' ? 13 : size === 'lg' ? 24 : 18;
  const iconNode = React.isValidElement(STATE_ICONS[state])
    ? React.cloneElement(STATE_ICONS[state] as React.ReactElement<{ size?: number }>, {
        size: iconSizeClass,
      })
    : STATE_ICONS[state];

  const Tag = onClick ? 'button' : 'div';

  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      aria-label={ariaLabel}
      data-testid={`ai-orb-${size}`}
      data-core-state={state}
      data-animated={String(shouldAnimate)}
      className={`vp-orb-wrapper vp-orb-wrapper--${size} vp-orb-wrapper--${state} ${
        shouldAnimate ? 'is-animated' : 'is-static'
      }`}
    >
      <span className="vp-orb-aura" />
      <span className="vp-orb-ring vp-orb-ring--outer" />
      <span className="vp-orb-ring vp-orb-ring--inner" />
      <span
        className="vp-orb-core"
        style={{
          transform: `scale(${audioScale.toFixed(3)})`,
        }}
      >
        {iconNode}
      </span>
    </Tag>
  );
};

export default AIOrb;
