import React from 'react';
import { Mic, MicOff, Square, Cpu, Sparkles, ArrowRight } from 'lucide-react';
import { useAICore } from '../../context/AICoreContext';
import { AIOrb } from './AIOrb';
import { AudioReactiveWaveform } from './AudioReactiveWaveform';
import { StatusPill, KeycapBadge } from '../ui/DesignSystem';

export interface AICoreHeroCardProps {
  onOpenAgent: () => void;
  onOpenSettings: () => void;
}

export const AICoreHeroCard: React.FC<AICoreHeroCardProps> = ({
  onOpenAgent,
  onOpenSettings,
}) => {
  const {
    coreState,
    coreLabel,
    coreDetail,
    pillVariant,
    voiceStatus,
    audioTelemetry,
    activeProviderLabel,
    activeModelLabel,
    isWindowVisible,
    toggleVoiceMode,
    interruptVoice,
  } = useAICore();

  const isVoiceActive = Boolean(voiceStatus?.is_active);
  const shortcut = voiceStatus?.settings?.shortcut || 'Ctrl+Shift+V';

  return (
    <section
      className="vp-ai-core-hero"
      data-testid="ai-core-hero-card"
      data-core-state={coreState}
    >
      <div className="vp-ai-core-hero-top">
        <AIOrb
          state={coreState}
          size="lg"
          rmsLevel={audioTelemetry.rms_level}
          isWindowVisible={isWindowVisible}
          onClick={() => toggleVoiceMode()}
          ariaLabel={`AI Core (${coreLabel}) — Click to toggle Voice Mode`}
        />

        <div className="vp-ai-core-summary">
          <div className="vp-ai-core-title-row">
            <h2 className="vp-ai-core-title">VoxPilot AI Core</h2>
            <StatusPill
              variant={pillVariant}
              showDot
              testId="ai-core-state-badge"
            >
              {coreLabel}
            </StatusPill>
          </div>

          <p className="vp-ai-core-detail">{coreDetail}</p>

          <div className="vp-ai-core-telemetry-row">
            <StatusPill variant="accent" icon={<Cpu size={12} />}>
              {activeProviderLabel} • {activeModelLabel}
            </StatusPill>
            <StatusPill variant="neutral" icon={<Sparkles size={12} />}>
              Voice Shortcut: <KeycapBadge>{shortcut}</KeycapBadge>
            </StatusPill>
          </div>
        </div>
      </div>

      <AudioReactiveWaveform
        telemetry={audioTelemetry}
        isWindowVisible={isWindowVisible}
      />

      <div className="vp-ai-core-actions">
        <button
          type="button"
          className={isVoiceActive ? 'vp-btn-danger' : 'vp-btn-primary'}
          onClick={() => toggleVoiceMode()}
          data-testid="ai-core-voice-toggle"
        >
          {isVoiceActive ? <MicOff size={14} /> : <Mic size={14} />}
          <span>{isVoiceActive ? 'Stop Voice Mode' : 'Start Voice Mode'}</span>
        </button>

        {(coreState === 'listening' || coreState === 'speaking') && (
          <button
            type="button"
            className="vp-btn-secondary"
            onClick={() => interruptVoice()}
            data-testid="ai-core-voice-interrupt"
          >
            <Square size={13} />
            <span>Interrupt</span>
          </button>
        )}

        <button
          type="button"
          className="vp-btn-secondary"
          onClick={onOpenAgent}
        >
          <span>Open AI Workspace</span>
          <ArrowRight size={13} />
        </button>

        <button
          type="button"
          className="vp-btn-secondary"
          onClick={onOpenSettings}
        >
          <span>Configure Provider</span>
        </button>
      </div>
    </section>
  );
};

export default AICoreHeroCard;
