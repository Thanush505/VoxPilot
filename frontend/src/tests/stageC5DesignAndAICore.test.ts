import { describe, expect, it } from 'vitest';
import {
  DEFAULT_AUDIO_TELEMETRY,
  deriveAICoreState,
} from '../context/AICoreContext';
import { computeTargetBarHeights } from '../components/aicore/AudioReactiveWaveform';
import {
  DEFAULT_MOTION_PREFERENCES,
  normalizeMotionPreferences,
  resolveEffectiveReducedMotion,
} from '../theme/ThemeProvider';
import type { VoiceStatusResponse } from '../api/types';

function makeVoiceStatus(overrides: Partial<VoiceStatusResponse> = {}): VoiceStatusResponse {
  return {
    is_active: false,
    phase: 'idle',
    is_recording: false,
    is_ptt_active: false,
    wake_word_enabled: false,
    conversation_id: null,
    last_transcript: null,
    last_response: null,
    last_error: null,
    last_agent_result: null,
    settings: {
      shortcut_enabled: true,
      shortcut: 'Ctrl+Shift+V',
      microphone_device: null,
      tts_enabled: true,
      piper_voice: 'en_US-lessac-medium.onnx',
      streaming_vad_enabled: true,
      wake_word_enabled: false,
    },
    shortcut_status: {
      shortcut: 'Ctrl+Shift+V',
      enabled: true,
      is_registered: true,
      registration_error: null,
      trigger_count: 0,
      reserved_emergency_shortcut: 'Ctrl+Alt+Escape',
    },
    components: {
      microphone: {
        available: true,
        device_count: 1,
        selected_device: {
          index: 0,
          name: 'Test USB Mic',
          host_api: 'Windows WASAPI',
          channels: 1,
          default_sample_rate: 16000,
        },
        error: null,
      },
      stt: {
        available: true,
        engine: 'faster-whisper',
        model_size: 'base',
        device: 'cpu',
        compute_type: 'int8',
        loaded: true,
        cached: true,
      },
      tts: {
        available: true,
        enabled: true,
        engine: 'piper',
        piper_voice: 'en_US-lessac-medium.onnx',
        model_path: 'test.onnx',
        model_exists: true,
        loaded: true,
        available_voices: [],
      },
      vad: {
        available: true,
        enabled: true,
        engine: 'webrtc+silero',
        aggressiveness: 2,
        frame_duration_ms: 30,
        end_silence_ms: 900,
      },
    },
    events: [],
    latest_event_id: 0,
    ...overrides,
  };
}

describe('Stage C.5 AI Core State Derivation', () => {
  it('derives all 8 AI Core states accurately with expected priority', () => {
    // 1. idle
    const idleMeta = deriveAICoreState({
      isConnected: true,
      emergencyActive: false,
      voiceStatus: makeVoiceStatus({ phase: 'idle' }),
      agentPhase: 'idle',
    });
    expect(idleMeta.state).toBe('idle');
    expect(idleMeta.pillVariant).toBe('neutral');

    // 2. listening (live voice takes priority over idle agent)
    const listeningMeta = deriveAICoreState({
      isConnected: true,
      emergencyActive: false,
      voiceStatus: makeVoiceStatus({ is_active: true, phase: 'listening', is_recording: true }),
      agentPhase: 'idle',
    });
    expect(listeningMeta.state).toBe('listening');
    expect(listeningMeta.detail).toContain('Test USB Mic');

    // 3. thinking (voice transcribing + interactive agent thinking)
    const thinkingVoice = deriveAICoreState({
      isConnected: true,
      emergencyActive: false,
      voiceStatus: makeVoiceStatus({ is_active: true, phase: 'transcribing' }),
      agentPhase: 'idle',
    });
    expect(thinkingVoice.state).toBe('thinking');

    const thinkingAgent = deriveAICoreState({
      isConnected: true,
      emergencyActive: false,
      voiceStatus: makeVoiceStatus({ phase: 'idle' }),
      agentPhase: 'thinking',
      agentDetail: 'Routing through Gemini BYOK',
    });
    expect(thinkingAgent.state).toBe('thinking');
    expect(thinkingAgent.detail).toBe('Routing through Gemini BYOK');

    // 4. planning
    const planningMeta = deriveAICoreState({
      isConnected: true,
      emergencyActive: false,
      voiceStatus: makeVoiceStatus({ phase: 'processing', last_transcript: 'Open VS Code' }),
      agentPhase: 'idle',
    });
    expect(planningMeta.state).toBe('planning');
    expect(planningMeta.detail).toContain('Open VS Code');

    // 5. executing
    const executingMeta = deriveAICoreState({
      isConnected: true,
      emergencyActive: false,
      voiceStatus: makeVoiceStatus({ phase: 'idle' }),
      agentPhase: 'executing',
      agentDetail: 'Running local tool chain',
    });
    expect(executingMeta.state).toBe('executing');
    expect(executingMeta.pillVariant).toBe('warning');

    // 6. speaking
    const speakingMeta = deriveAICoreState({
      isConnected: true,
      emergencyActive: false,
      voiceStatus: makeVoiceStatus({
        is_active: true,
        phase: 'speaking',
        last_response: 'Opening VS Code now.',
      }),
      agentPhase: 'idle',
    });
    expect(speakingMeta.state).toBe('speaking');
    expect(speakingMeta.detail).toBe('Opening VS Code now.');

    // 7. completed
    const completedMeta = deriveAICoreState({
      isConnected: true,
      emergencyActive: false,
      voiceStatus: makeVoiceStatus({ phase: 'idle' }),
      agentPhase: 'completed',
      agentDetail: 'Draft ready for review',
    });
    expect(completedMeta.state).toBe('completed');
    expect(completedMeta.pillVariant).toBe('success');

    // 8. error (emergency stop overrides everything)
    const emergencyMeta = deriveAICoreState({
      isConnected: true,
      emergencyActive: true,
      voiceStatus: makeVoiceStatus({ phase: 'listening', is_recording: true }),
      agentPhase: 'executing',
    });
    expect(emergencyMeta.state).toBe('error');
    expect(emergencyMeta.label).toBe('Safety Halt Active');

    const offlineMeta = deriveAICoreState({
      isConnected: false,
      emergencyActive: false,
      voiceStatus: null,
      agentPhase: 'idle',
    });
    expect(offlineMeta.state).toBe('error');
    expect(offlineMeta.label).toBe('Backend Offline');
  });
});

describe('Stage C.5 Audio-Reactive Waveform Math', () => {
  it('returns calm baseline when telemetry is inactive', () => {
    const idleBars = computeTargetBarHeights(DEFAULT_AUDIO_TELEMETRY, 1.0);
    expect(idleBars).toHaveLength(16);
    expect(idleBars.every((v) => v === 0.14)).toBe(true);
  });

  it('produces deterministic FFT band heights bounded to [0.14, 1.0] for real microphone data', () => {
    const bands = Array.from({ length: 16 }, (_, idx) => (idx % 2 === 0 ? 0.65 : 0.25));
    const telemetry = {
      active: true,
      mode: 'microphone' as const,
      rms_level: 0.35,
      peak_level: 0.78,
      is_speech: true,
      bands,
    };

    const runA = computeTargetBarHeights(telemetry, 1.0);
    const runB = computeTargetBarHeights(telemetry, 1.0);
    expect(runA).toEqual(runB); // Purely deterministic — zero randomness
    expect(runA).toHaveLength(16);
    expect(runA[0]).toBeGreaterThan(runA[1]);
    expect(runA.every((v) => v >= 0.14 && v <= 1.0)).toBe(true);

    const expressive = computeTargetBarHeights(telemetry, 1.15);
    expect(expressive[0]).toBeGreaterThanOrEqual(runA[0]);
  });

  it('clamps malformed telemetry values and handles active TTS fallback cleanly', () => {
    const malformed = {
      active: true,
      mode: 'microphone' as const,
      rms_level: 0.5,
      peak_level: 1.0,
      is_speech: true,
      bands: Array.from({ length: 16 }, (_, i) => (i === 0 ? Number.NaN : 99.0)),
    };
    const safeBars = computeTargetBarHeights(malformed, 1.0);
    expect(safeBars[0]).toBeCloseTo(0.14, 4);
    expect(safeBars[1]).toBe(1.0);

    const ttsBars = computeTargetBarHeights(
      { active: true, mode: 'tts', rms_level: 0, peak_level: 0, is_speech: true, bands: new Array(16).fill(0) },
      1.0
    );
    expect(ttsBars).toHaveLength(16);
    expect(ttsBars[7]).toBeGreaterThan(ttsBars[0]);
  });
});

describe('Stage C.5 Motion Preferences & Reduced Motion Engine', () => {
  it('resolves reduced motion across system, on, off, and animationsEnabled overrides', () => {
    expect(resolveEffectiveReducedMotion(DEFAULT_MOTION_PREFERENCES, false)).toBe(false);
    expect(resolveEffectiveReducedMotion(DEFAULT_MOTION_PREFERENCES, true)).toBe(true);

    expect(
      resolveEffectiveReducedMotion(
        { ...DEFAULT_MOTION_PREFERENCES, reducedMotionMode: 'on' },
        false
      )
    ).toBe(true);

    expect(
      resolveEffectiveReducedMotion(
        { ...DEFAULT_MOTION_PREFERENCES, reducedMotionMode: 'off' },
        true
      )
    ).toBe(false);

    expect(
      resolveEffectiveReducedMotion(
        { ...DEFAULT_MOTION_PREFERENCES, animationsEnabled: false, reducedMotionMode: 'off' },
        false
      )
    ).toBe(true);
  });

  it('normalizes malformed stored motion preference objects back to valid defaults', () => {
    expect(normalizeMotionPreferences(null)).toEqual(DEFAULT_MOTION_PREFERENCES);
    expect(
      normalizeMotionPreferences({
        animationsEnabled: false,
        motionIntensity: 'invalid-level',
        reducedMotionMode: 'on',
        aiCoreAnimationEnabled: 'not-a-bool',
        voiceOrbEnabled: false,
        waveformEnabled: false,
      })
    ).toEqual({
      animationsEnabled: false,
      motionIntensity: 'balanced',
      reducedMotionMode: 'on',
      aiCoreAnimationEnabled: true,
      voiceOrbEnabled: false,
      waveformEnabled: false,
    });
  });
});
