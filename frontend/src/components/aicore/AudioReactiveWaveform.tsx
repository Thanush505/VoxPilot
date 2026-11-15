import React, { useEffect, useRef, useState } from 'react';
import { Mic, Volume2, PauseCircle } from 'lucide-react';
import type { VoiceAudioTelemetry } from '../../api/types';
import { useTheme } from '../../theme/ThemeProvider';

export interface AudioReactiveWaveformProps {
  telemetry: VoiceAudioTelemetry;
  isWindowVisible?: boolean;
  compact?: boolean;
}

const BAND_COUNT = 16;
const CALM_BASELINE = Array.from({ length: BAND_COUNT }, () => 0.14);

export function computeTargetBarHeights(
  telemetry: VoiceAudioTelemetry,
  intensityScale: number
): number[] {
  if (!telemetry.active) {
    return CALM_BASELINE;
  }

  // Real microphone frequency bands (never random values)
  if (telemetry.mode === 'microphone' && Array.isArray(telemetry.bands) && telemetry.bands.length === BAND_COUNT) {
    return telemetry.bands.map((val) => {
      const clamped = Math.max(0, Math.min(1, Number.isFinite(val) ? val : 0));
      return Math.max(0.14, Math.min(1, 0.14 + clamped * 0.86 * intensityScale));
    });
  }

  // Deterministic envelope bar height for TTS speaking indicator when active
  const baseLevel = telemetry.mode === 'tts' ? 0.42 : Math.max(0.16, Math.min(0.95, telemetry.rms_level * 4));
  return Array.from({ length: BAND_COUNT }, (_, idx) => {
    const centerDist = Math.abs(idx - (BAND_COUNT - 1) / 2) / ((BAND_COUNT - 1) / 2);
    const arch = 1 - centerDist * 0.55;
    return Math.max(0.14, Math.min(1, baseLevel * arch * intensityScale));
  });
}

export const AudioReactiveWaveform: React.FC<AudioReactiveWaveformProps> = ({
  telemetry,
  isWindowVisible = true,
  compact = false,
}) => {
  const {
    animationsEnabled,
    waveformEnabled,
    effectiveReducedMotion,
    motionIntensity,
  } = useTheme();

  const [displayBands, setDisplayBands] = useState<number[]>(CALM_BASELINE);
  const currentBandsRef = useRef<number[]>([...CALM_BASELINE]);
  const targetBandsRef = useRef<number[]>([...CALM_BASELINE]);
  const rafRef = useRef<number | null>(null);

  const intensityScale =
    motionIntensity === 'subtle' ? 0.72 : motionIntensity === 'expressive' ? 1.15 : 1.0;

  const canAnimate =
    telemetry.active &&
    isWindowVisible &&
    animationsEnabled &&
    waveformEnabled &&
    !effectiveReducedMotion;

  useEffect(() => {
    targetBandsRef.current = computeTargetBarHeights(telemetry, intensityScale);

    // If not actively animating, snap cleanly to calm baseline (or static target if reduced-motion active)
    if (!canAnimate) {
      if (rafRef.current !== null) {
        window.cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      const next = telemetry.active ? targetBandsRef.current : CALM_BASELINE;
      currentBandsRef.current = [...next];
      setDisplayBands(next);
      return;
    }

    const step = () => {
      const curr = currentBandsRef.current;
      const tgt = targetBandsRef.current;
      let maxDelta = 0;
      const next = curr.map((val, i) => {
        const diff = (tgt[i] ?? 0.14) - val;
        if (Math.abs(diff) > maxDelta) maxDelta = Math.abs(diff);
        return val + diff * 0.35;
      });
      currentBandsRef.current = next;
      setDisplayBands(next);
      if (maxDelta > 0.008) {
        rafRef.current = window.requestAnimationFrame(step);
      } else {
        rafRef.current = null;
      }
    };

    if (rafRef.current === null) {
      rafRef.current = window.requestAnimationFrame(step);
    }

    return () => {
      if (rafRef.current !== null) {
        window.cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [telemetry, intensityScale, canAnimate]);

  const statusLabel = !waveformEnabled
    ? 'Waveform Disabled in Settings'
    : effectiveReducedMotion
      ? telemetry.active
        ? 'Audio Active (Reduced Motion)'
        : 'Reduced Motion Ready'
      : !isWindowVisible
        ? 'Paused (Window Inactive)'
        : telemetry.mode === 'microphone'
          ? telemetry.is_speech
            ? 'Speech Detected — Live Audio'
            : 'Microphone Listening'
          : telemetry.mode === 'tts'
            ? 'Speaking Response (Piper TTS)'
            : 'Microphone Idle';

  const cardModeClass =
    telemetry.mode === 'microphone'
      ? 'is-live'
      : telemetry.mode === 'tts'
        ? 'is-tts'
        : '';

  return (
    <div
      className={`vp-waveform-card ${cardModeClass}`.trim()}
      data-testid="audio-reactive-waveform"
      data-audio-mode={telemetry.mode}
      data-audio-active={String(telemetry.active)}
    >
      <div className="vp-waveform-header">
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontWeight: 600 }}>
          {telemetry.mode === 'tts' ? (
            <Volume2 size={13} />
          ) : !waveformEnabled || !isWindowVisible ? (
            <PauseCircle size={13} />
          ) : (
            <Mic size={13} />
          )}
          <span>{statusLabel}</span>
        </span>
        {!compact && (
          <span className="vp-waveform-meta">
            <span>RMS {(telemetry.rms_level * 100).toFixed(1)}%</span>
            <span>Peak {(telemetry.peak_level * 100).toFixed(1)}%</span>
          </span>
        )}
      </div>

      <div className="vp-waveform-bars" aria-hidden="true">
        {displayBands.map((level, idx) => (
          <div
            key={idx}
            className="vp-waveform-bar"
            style={{
              transform: `scaleY(${Math.max(0.14, Math.min(1, level)).toFixed(3)})`,
            }}
          />
        ))}
      </div>
    </div>
  );
};

export default AudioReactiveWaveform;
