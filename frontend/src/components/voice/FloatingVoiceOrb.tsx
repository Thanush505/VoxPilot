import React, { useEffect, useMemo, useState } from 'react';
import { Mic, MicOff, Square, EyeOff, Sparkles, AlertTriangle, Volume2, BrainCircuit, Play } from 'lucide-react';
import apiClient from '../../api/client';
import type { VoiceStatusResponse } from '../../api/types';

type OrbVisualState =
  | 'idle'
  | 'starting'
  | 'listening'
  | 'processing'
  | 'planning'
  | 'executing'
  | 'speaking'
  | 'error';

function normalizeOrbState(phase?: string): OrbVisualState {
  switch ((phase || '').toLowerCase()) {
    case 'starting':
      return 'starting';
    case 'listening':
      return 'listening';
    case 'transcribing':
    case 'processing':
      return 'processing';
    case 'planning':
      return 'planning';
    case 'executing':
      return 'executing';
    case 'speaking':
      return 'speaking';
    case 'error':
      return 'error';
    default:
      return 'idle';
  }
}

const STATE_META: Record<OrbVisualState, { label: string; detail: string }> = {
  idle: { label: 'Idle', detail: 'Voice assistant is inactive.' },
  starting: { label: 'Starting', detail: 'Initializing microphone stream...' },
  listening: { label: 'Listening', detail: 'Microphone active and capturing speech.' },
  processing: { label: 'Processing', detail: 'Speech is being interpreted by AI.' },
  planning: { label: 'Planning', detail: 'Agent is planning the next action.' },
  executing: { label: 'Executing', detail: 'Executing approved desktop action.' },
  speaking: { label: 'Speaking', detail: 'VoxPilot is responding through speech.' },
  error: { label: 'Error', detail: 'Voice or microphone subsystem error.' },
};

export const FloatingVoiceOrb: React.FC = () => {
  const [status, setStatus] = useState<VoiceStatusResponse | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    document.documentElement.classList.add('vp-orb-window');
    document.body.classList.add('vp-orb-window');
    return () => {
      document.documentElement.classList.remove('vp-orb-window');
      document.body.classList.remove('vp-orb-window');
    };
  }, []);

  useEffect(() => {
    let mounted = true;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const res = await apiClient.getVoiceStatus();
        if (mounted && res.ok && res.data) {
          setStatus(res.data);
        }
      } catch {
        // Ignore transient startup polling failures
      } finally {
        if (mounted) {
          const active = Boolean(status?.is_active);
          timer = window.setTimeout(poll, active ? 350 : 1000);
        }
      }
    };

    poll();
    const onToken = () => {
      poll();
    };
    window.addEventListener('voxpilot:token-ready', onToken);
    return () => {
      mounted = false;
      if (timer) window.clearTimeout(timer);
      window.removeEventListener('voxpilot:token-ready', onToken);
    };
  }, [status?.is_active]);

  const visualState = normalizeOrbState(status?.phase);
  const isActive = Boolean(status?.is_active);
  const micActive = Boolean(status?.is_recording || visualState === 'listening');
  const rms = Math.min(1, Math.max(0, Number(status?.audio_telemetry?.rms_level ?? 0)));
  const animationsEnabled = status?.settings?.floating_orb_animations !== false;
  const activeCorner = status?.settings?.floating_orb_corner || 'bottom_right';
  const meta = STATE_META[visualState];

  const orbScale = useMemo(() => {
    if (!animationsEnabled || visualState !== 'listening') return 1;
    return 1 + Math.min(0.16, rms * 0.45);
  }, [animationsEnabled, rms, visualState]);

  const togglePanel = async () => {
    const next = !panelOpen;
    setPanelOpen(next);
    try {
      await apiClient.setFloatingOrbPanel(next);
    } catch {
      // Ignore outside desktop shell
    }
  };

  const handleStopVoice = async () => {
    setBusy(true);
    try {
      const res = await apiClient.stopVoiceMode('Stopped from floating orb');
      if (res.ok && res.data) {
        setStatus(res.data);
      }
      setPanelOpen(false);
      await apiClient.setFloatingOrbPanel(false).catch(() => {});
    } finally {
      setBusy(false);
    }
  };

  const handleHideOrb = async () => {
    setBusy(true);
    try {
      setPanelOpen(false);
      await apiClient.setFloatingOrbPanel(false).catch(() => {});
      await apiClient.hideFloatingOrb();
    } finally {
      setBusy(false);
    }
  };

  const handleCornerChange = async (corner: string) => {
    await apiClient.updateFloatingOrbPosition({ corner });
    const res = await apiClient.getVoiceStatus();
    if (res.ok && res.data) setStatus(res.data);
  };

  const renderIcon = () => {
    switch (visualState) {
      case 'listening':
        return <Mic size={20} />;
      case 'speaking':
        return <Volume2 size={20} />;
      case 'processing':
      case 'planning':
        return <BrainCircuit size={20} />;
      case 'executing':
        return <Play size={18} />;
      case 'error':
        return <AlertTriangle size={20} />;
      default:
        return <Sparkles size={20} />;
    }
  };

  return (
    <div
      className={`vp-floating-orb-shell ${animationsEnabled ? '' : 'vp-floating-orb-shell--static'}`}
      data-orb-state={visualState}
      data-testid="floating-voice-orb"
    >
      <button
        type="button"
        className={`vp-floating-orb-btn vp-floating-orb-btn--${visualState}`}
        style={{ transform: `scale(${orbScale.toFixed(3)})` }}
        onClick={togglePanel}
        title={`VoxPilot Voice: ${meta.label} (click for status)`}
        aria-label={`VoxPilot Voice Orb — ${meta.label}`}
        data-testid="floating-voice-orb-button"
      >
        <span className="vp-floating-orb-ring" aria-hidden="true" />
        <span className="vp-floating-orb-core" aria-hidden="true">
          {renderIcon()}
        </span>
      </button>

      {panelOpen && (
        <div className="vp-floating-orb-panel" data-testid="floating-voice-orb-panel">
          <div className="vp-floating-orb-panel-header">
            <span className={`vp-floating-orb-badge vp-floating-orb-badge--${visualState}`}>
              {meta.label}
            </span>
            <span className={`vp-floating-orb-mic ${micActive ? 'is-live' : ''}`}>
              {micActive ? <Mic size={12} /> : <MicOff size={12} />}
              <span>{micActive ? 'Mic Active' : 'Mic Inactive'}</span>
            </span>
          </div>

          <p className="vp-floating-orb-panel-detail">
            {visualState === 'error' && status?.last_error ? status.last_error : meta.detail}
          </p>

          <div className="vp-floating-orb-corners" aria-label="Orb screen corner">
            {([
              ['top_left', 'TL'],
              ['top_right', 'TR'],
              ['bottom_left', 'BL'],
              ['bottom_right', 'BR'],
            ] as const).map(([key, short]) => (
              <button
                key={key}
                type="button"
                className={`vp-floating-orb-corner-btn ${activeCorner === key ? 'is-selected' : ''}`}
                onClick={() => handleCornerChange(key)}
              >
                {short}
              </button>
            ))}
          </div>

          <div className="vp-floating-orb-panel-actions">
            {isActive && (
              <button
                type="button"
                className="vp-floating-orb-action-stop"
                onClick={handleStopVoice}
                disabled={busy}
                data-testid="floating-orb-stop-voice"
              >
                <Square size={12} />
                <span>Stop Voice</span>
              </button>
            )}
            <button
              type="button"
              className="vp-floating-orb-action-hide"
              onClick={handleHideOrb}
              disabled={busy}
              data-testid="floating-orb-hide"
            >
              <EyeOff size={12} />
              <span>Hide Orb</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default FloatingVoiceOrb;
