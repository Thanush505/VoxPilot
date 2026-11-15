import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import apiClient from '../api/client';
import type {
  ActiveProviderResponse,
  EmergencyStopStatus,
  VoiceAudioTelemetry,
  VoiceStatusResponse,
} from '../api/types';

export type AICoreState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'planning'
  | 'executing'
  | 'speaking'
  | 'completed'
  | 'error';

export type AgentExecutionPhase =
  | 'idle'
  | 'thinking'
  | 'planning'
  | 'executing'
  | 'completed'
  | 'error';

export const DEFAULT_AUDIO_TELEMETRY: VoiceAudioTelemetry = {
  active: false,
  mode: 'idle',
  rms_level: 0,
  peak_level: 0,
  is_speech: false,
  bands: Array.from({ length: 16 }, () => 0),
};

export interface AICoreStateMeta {
  state: AICoreState;
  label: string;
  detail: string;
  pillVariant: 'accent' | 'success' | 'warning' | 'danger' | 'info' | 'neutral';
}

export function deriveAICoreState(params: {
  isConnected: boolean;
  emergencyActive: boolean;
  voiceStatus: VoiceStatusResponse | null;
  agentPhase: AgentExecutionPhase;
  agentDetail?: string | null;
}): AICoreStateMeta {
  const { isConnected, emergencyActive, voiceStatus, agentPhase, agentDetail } = params;

  if (emergencyActive) {
    return {
      state: 'error',
      label: 'Safety Halt Active',
      detail: 'Global Emergency Stop is latched. Automation and voice are paused.',
      pillVariant: 'danger',
    };
  }

  if (!isConnected) {
    return {
      state: 'error',
      label: 'Backend Offline',
      detail: 'Reconnect the local VoxPilot FastAPI service to activate AI workflows.',
      pillVariant: 'danger',
    };
  }

  // Live voice phases take priority when voice interaction is active
  if (voiceStatus) {
    if (voiceStatus.phase === 'error') {
      return {
        state: 'error',
        label: 'Voice Error',
        detail: voiceStatus.last_error || 'Microphone or voice pipeline reported an error.',
        pillVariant: 'danger',
      };
    }
    if (voiceStatus.phase === 'listening' || voiceStatus.is_recording || voiceStatus.is_ptt_active) {
      return {
        state: 'listening',
        label: 'Listening',
        detail: voiceStatus.components?.microphone?.selected_device?.name
          ? `Capturing audio via ${voiceStatus.components.microphone.selected_device.name}`
          : 'Listening to microphone input via Streaming VAD...',
        pillVariant: 'success',
      };
    }
    if (voiceStatus.phase === 'transcribing') {
      return {
        state: 'thinking',
        label: 'Thinking',
        detail: 'Transcribing local speech with Faster-Whisper...',
        pillVariant: 'info',
      };
    }
    if (voiceStatus.phase === 'processing') {
      return {
        state: 'planning',
        label: 'Planning',
        detail: voiceStatus.last_transcript
          ? `Planning response for "${voiceStatus.last_transcript}"`
          : 'Analyzing intent and building execution plan...',
        pillVariant: 'info',
      };
    }
    if (voiceStatus.phase === 'speaking') {
      return {
        state: 'speaking',
        label: 'Speaking',
        detail: voiceStatus.last_response
          ? voiceStatus.last_response
          : 'Synthesizing voice response via Piper ONNX...',
        pillVariant: 'accent',
      };
    }
  }

  // Interactive text agent phases
  if (agentPhase === 'error') {
    return {
      state: 'error',
      label: 'Execution Error',
      detail: agentDetail || 'The last AI execution encountered an error.',
      pillVariant: 'danger',
    };
  }
  if (agentPhase === 'thinking') {
    return {
      state: 'thinking',
      label: 'Thinking',
      detail: agentDetail || 'Understanding request with active AI provider...',
      pillVariant: 'info',
    };
  }
  if (agentPhase === 'planning') {
    return {
      state: 'planning',
      label: 'Planning',
      detail: agentDetail || 'Composing structured multi-step action plan...',
      pillVariant: 'info',
    };
  }
  if (agentPhase === 'executing') {
    return {
      state: 'executing',
      label: 'Executing',
      detail: agentDetail || 'Running approved desktop, file, browser, or email tools...',
      pillVariant: 'warning',
    };
  }
  if (agentPhase === 'completed') {
    return {
      state: 'completed',
      label: 'Completed',
      detail: agentDetail || 'Task completed cleanly.',
      pillVariant: 'success',
    };
  }

  return {
    state: 'idle',
    label: 'Ready',
    detail: 'AI Core standing by for voice commands, agent tasks, and specialist workflows.',
    pillVariant: 'neutral',
  };
}

export interface AICoreContextValue {
  coreState: AICoreState;
  coreLabel: string;
  coreDetail: string;
  pillVariant: AICoreStateMeta['pillVariant'];
  voiceStatus: VoiceStatusResponse | null;
  audioTelemetry: VoiceAudioTelemetry;
  activeProvider: ActiveProviderResponse | null;
  activeProviderLabel: string;
  activeModelLabel: string;
  isWindowVisible: boolean;
  isWindowFocused: boolean;
  agentPhase: AgentExecutionPhase;
  setAgentExecutionPhase: (phase: AgentExecutionPhase, detail?: string | null) => void;
  toggleVoiceMode: (conversationId?: string | null) => Promise<void>;
  interruptVoice: () => Promise<void>;
  refreshVoiceStatus: () => Promise<void>;
  refreshActiveProvider: () => Promise<void>;
}

const AICoreContext = createContext<AICoreContextValue | null>(null);

export const AICoreProvider: React.FC<{
  children: React.ReactNode;
  isConnected: boolean;
  emergencyStatus: EmergencyStopStatus | null;
  activeProfileId: string;
}> = ({ children, isConnected, emergencyStatus, activeProfileId }) => {
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatusResponse | null>(null);
  const [activeProvider, setActiveProvider] = useState<ActiveProviderResponse | null>(null);
  const [agentPhase, setAgentPhaseState] = useState<AgentExecutionPhase>('idle');
  const [agentDetail, setAgentDetail] = useState<string | null>(null);
  const [isWindowVisible, setIsWindowVisible] = useState<boolean>(() =>
    typeof document !== 'undefined' ? document.visibilityState !== 'hidden' : true
  );
  const [isWindowFocused, setIsWindowFocused] = useState<boolean>(() =>
    typeof document !== 'undefined' && typeof document.hasFocus === 'function'
      ? document.hasFocus()
      : true
  );

  const completedResetTimerRef = useRef<number | null>(null);
  const isMountedRef = useRef<boolean>(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      if (completedResetTimerRef.current !== null) {
        window.clearTimeout(completedResetTimerRef.current);
      }
    };
  }, []);

  // Track window visibility and focus to cleanly pause animations & rapid polling when inactive
  useEffect(() => {
    if (typeof document === 'undefined' || typeof window === 'undefined') return;

    const handleVisibility = () => {
      setIsWindowVisible(document.visibilityState !== 'hidden');
    };
    const handleFocus = () => setIsWindowFocused(true);
    const handleBlur = () => setIsWindowFocused(false);

    document.addEventListener('visibilitychange', handleVisibility);
    window.addEventListener('focus', handleFocus);
    window.addEventListener('blur', handleBlur);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('focus', handleFocus);
      window.removeEventListener('blur', handleBlur);
    };
  }, []);

  const refreshVoiceStatus = useCallback(async () => {
    if (!isConnected) return;
    try {
      const res = await apiClient.getVoiceStatus(0);
      if (isMountedRef.current && res.ok && res.data) {
        setVoiceStatus(res.data);
      }
    } catch {
      // Non-fatal
    }
  }, [isConnected]);

  const refreshActiveProvider = useCallback(async () => {
    if (!isConnected) return;
    try {
      const res = await apiClient.getActiveProvider();
      if (isMountedRef.current && res.ok && res.data) {
        setActiveProvider(res.data);
      }
    } catch {
      // Non-fatal
    }
  }, [isConnected, activeProfileId]);

  useEffect(() => {
    refreshVoiceStatus();
    refreshActiveProvider();
  }, [refreshVoiceStatus, refreshActiveProvider]);

  // Adaptive polling: fast (320ms) only while actively recording/listening/speaking and visible;
  // gentle (4000ms) while idle and visible; paused while hidden!
  const isVoiceBusy = Boolean(
    voiceStatus &&
      (voiceStatus.is_active ||
        voiceStatus.is_recording ||
        voiceStatus.is_ptt_active ||
        (voiceStatus.phase !== 'idle' && voiceStatus.phase !== 'error'))
  );

  useEffect(() => {
    if (!isConnected || !isWindowVisible) return;
    const intervalMs = isVoiceBusy ? 320 : 4000;
    const timer = window.setInterval(() => {
      refreshVoiceStatus();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [isConnected, isWindowVisible, isVoiceBusy, refreshVoiceStatus]);

  const setAgentExecutionPhase = useCallback(
    (phase: AgentExecutionPhase, detail?: string | null) => {
      if (completedResetTimerRef.current !== null) {
        window.clearTimeout(completedResetTimerRef.current);
        completedResetTimerRef.current = null;
      }
      setAgentPhaseState(phase);
      setAgentDetail(detail ?? null);
      if (phase === 'completed') {
        completedResetTimerRef.current = window.setTimeout(() => {
          if (isMountedRef.current) {
            setAgentPhaseState('idle');
            setAgentDetail(null);
          }
        }, 4200);
      }
    },
    []
  );

  const toggleVoiceMode = useCallback(
    async (conversationId?: string | null) => {
      if (!isConnected) return;
      const res = await apiClient.toggleVoiceMode(conversationId ?? undefined);
      if (isMountedRef.current && res.ok && res.data) {
        setVoiceStatus(res.data);
      }
    },
    [isConnected]
  );

  const interruptVoice = useCallback(async () => {
    if (!isConnected) return;
    const res = await apiClient.interruptVoice('Interrupted from AI Core');
    if (isMountedRef.current && res.ok && res.data) {
      setVoiceStatus(res.data);
    } else {
      await refreshVoiceStatus();
    }
  }, [isConnected, refreshVoiceStatus]);

  const emergencyActive = Boolean(emergencyStatus?.is_active);

  const meta = useMemo(
    () =>
      deriveAICoreState({
        isConnected,
        emergencyActive,
        voiceStatus,
        agentPhase,
        agentDetail,
      }),
    [isConnected, emergencyActive, voiceStatus, agentPhase, agentDetail]
  );

  const audioTelemetry = useMemo<VoiceAudioTelemetry>(() => {
    if (!voiceStatus?.audio_telemetry) {
      return DEFAULT_AUDIO_TELEMETRY;
    }
    const raw = voiceStatus.audio_telemetry;
    return {
      active: Boolean(raw.active),
      mode: raw.mode || 'idle',
      rms_level: Number.isFinite(raw.rms_level) ? raw.rms_level : 0,
      peak_level: Number.isFinite(raw.peak_level) ? raw.peak_level : 0,
      is_speech: Boolean(raw.is_speech),
      bands:
        Array.isArray(raw.bands) && raw.bands.length === 16
          ? raw.bands
          : DEFAULT_AUDIO_TELEMETRY.bands,
    };
  }, [voiceStatus]);

  const { activeProviderLabel, activeModelLabel } = useMemo(() => {
    const cfg = activeProvider?.profile_config;
    if (!cfg) {
      return { activeProviderLabel: 'Local AI (Ollama)', activeModelLabel: 'qwen3:4b' };
    }
    if (cfg.active_provider_id === 'byok') {
      const vendorMap: Record<string, string> = {
        openai: 'OpenAI BYOK',
        anthropic: 'Anthropic Claude',
        google: 'Google Gemini',
      };
      return {
        activeProviderLabel: vendorMap[cfg.byok_vendor || 'openai'] || 'BYOK Provider',
        activeModelLabel: cfg.byok_model || activeProvider?.health?.active_model || 'configured',
      };
    }
    if (cfg.active_provider_id === 'cloud') {
      return {
        activeProviderLabel: 'VoxPilot Cloud',
        activeModelLabel: activeProvider?.health?.active_model || 'managed',
      };
    }
    return {
      activeProviderLabel: 'Local AI (Ollama)',
      activeModelLabel: cfg.local_model || activeProvider?.health?.active_model || 'qwen3:4b',
    };
  }, [activeProvider]);

  const value = useMemo<AICoreContextValue>(
    () => ({
      coreState: meta.state,
      coreLabel: meta.label,
      coreDetail: meta.detail,
      pillVariant: meta.pillVariant,
      voiceStatus,
      audioTelemetry,
      activeProvider,
      activeProviderLabel,
      activeModelLabel,
      isWindowVisible,
      isWindowFocused,
      agentPhase,
      setAgentExecutionPhase,
      toggleVoiceMode,
      interruptVoice,
      refreshVoiceStatus,
      refreshActiveProvider,
    }),
    [
      meta,
      voiceStatus,
      audioTelemetry,
      activeProvider,
      activeProviderLabel,
      activeModelLabel,
      isWindowVisible,
      isWindowFocused,
      agentPhase,
      setAgentExecutionPhase,
      toggleVoiceMode,
      interruptVoice,
      refreshVoiceStatus,
      refreshActiveProvider,
    ]
  );

  return <AICoreContext.Provider value={value}>{children}</AICoreContext.Provider>;
};

export function useOptionalAICore(): AICoreContextValue | null {
  return useContext(AICoreContext);
}

export function useAICore(): AICoreContextValue {
  const ctx = useContext(AICoreContext);
  if (!ctx) {
    throw new Error('useAICore must be used within an AICoreProvider.');
  }
  return ctx;
}
