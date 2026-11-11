import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import apiClient from '../api/client';
import {
  formatKeyboardEventToShortcut,
  validateShortcutClientSide,
} from '../utils/shortcutUtils';
import { VoiceStatusResponse } from '../api/types';

describe('Voice Mode & Customizable Global Shortcut Frontend Tests', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const makeMockVoiceStatus = (overrides?: Partial<VoiceStatusResponse>): VoiceStatusResponse => ({
    is_active: false,
    phase: 'idle',
    is_recording: false,
    conversation_id: 'conv_voice_123',
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
          name: 'Microphone Array (WASAPI)',
          host_api: 'Windows WASAPI',
          channels: 1,
          default_sample_rate: 16000,
        },
        error: null,
      },
      stt: {
        available: true,
        engine: 'Faster-Whisper',
        model_size: 'base',
        device: 'cpu',
        compute_type: 'int8',
        loaded: true,
        cached: true,
      },
      tts: {
        available: true,
        enabled: true,
        engine: 'Piper ONNX',
        piper_voice: 'en_US-lessac-medium.onnx',
        model_path: 'models/piper/en_US-lessac-medium.onnx',
        model_exists: true,
        loaded: true,
        available_voices: [
          {
            id: 'en_US-lessac-medium.onnx',
            name: 'en_US-lessac-medium',
            path: 'models/piper/en_US-lessac-medium.onnx',
            has_config: true,
          },
        ],
      },
      vad: {
        available: true,
        enabled: true,
        engine: 'WebRTC + Silero VAD',
        aggressiveness: 2,
        frame_duration_ms: 30,
        end_silence_ms: 900,
      },
    },
    events: [],
    latest_event_id: 0,
    ...overrides,
  });

  it('starts and stops voice mode via microphone button API endpoints', async () => {
    const activeStatus = makeMockVoiceStatus({
      is_active: true,
      phase: 'listening',
      is_recording: true,
    });
    const idleStatus = makeMockVoiceStatus({
      is_active: false,
      phase: 'idle',
      is_recording: false,
    });

    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify(activeStatus), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(idleStatus), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      );

    const startRes = await apiClient.startVoiceMode('conv_voice_123');
    expect(startRes.ok).toBe(true);
    expect(startRes.data?.is_active).toBe(true);
    expect(startRes.data?.phase).toBe('listening');
    expect(startRes.data?.is_recording).toBe(true);

    const stopRes = await apiClient.stopVoiceMode('Cancelled by user');
    expect(stopRes.ok).toBe(true);
    expect(stopRes.data?.is_active).toBe(false);
    expect(stopRes.data?.phase).toBe('idle');
    expect(stopRes.data?.is_recording).toBe(false);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it('accurately tracks voice mode states and recording indicator across phases', async () => {
    const phases: Array<VoiceStatusResponse['phase']> = [
      'idle',
      'listening',
      'transcribing',
      'processing',
      'speaking',
      'error',
    ];

    for (const phase of phases) {
      const mockState = makeMockVoiceStatus({
        is_active: phase !== 'idle' && phase !== 'error',
        phase,
        is_recording: phase === 'listening',
        last_transcript: phase !== 'idle' && phase !== 'listening' ? 'Open Notepad' : null,
        last_response: phase === 'speaking' ? 'Opened Notepad.' : null,
        last_error: phase === 'error' ? 'Microphone unavailable.' : null,
      });

      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
        new Response(JSON.stringify(mockState), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      );

      const res = await apiClient.getVoiceStatus(0);
      expect(res.ok).toBe(true);
      expect(res.data?.phase).toBe(phase);
      expect(res.data?.is_recording).toBe(phase === 'listening');
    }
  });

  it('captures keyboard combinations and validates custom shortcuts accurately', () => {
    // Incomplete modifier-only press
    const incomplete = formatKeyboardEventToShortcut({
      key: 'Control',
      ctrlKey: true,
      altKey: false,
      shiftKey: true,
      metaKey: false,
    });
    expect(incomplete.isComplete).toBe(false);
    expect(incomplete.preview).toBe('Ctrl+Shift+...');

    // Complete default Ctrl+Shift+V press
    const completeDefault = formatKeyboardEventToShortcut({
      key: 'v',
      ctrlKey: true,
      altKey: false,
      shiftKey: true,
      metaKey: false,
    });
    expect(completeDefault.isComplete).toBe(true);
    expect(completeDefault.preview).toBe('Ctrl+Shift+V');
    expect(validateShortcutClientSide(completeDefault.preview)).toEqual({
      valid: true,
      error: null,
    });

    // Custom Ctrl+Alt+M shortcut
    const customCombo = formatKeyboardEventToShortcut({
      key: 'm',
      ctrlKey: true,
      altKey: true,
      shiftKey: false,
      metaKey: false,
    });
    expect(customCombo.preview).toBe('Ctrl+Alt+M');
    expect(validateShortcutClientSide(customCombo.preview).valid).toBe(true);
  });

  it('strictly blocks assigning Ctrl+Alt+Escape (Emergency Stop) to voice mode', () => {
    const emergencyCombo = formatKeyboardEventToShortcut({
      key: 'Escape',
      ctrlKey: true,
      altKey: true,
      shiftKey: false,
      metaKey: false,
    });
    expect(emergencyCombo.preview).toBe('Ctrl+Alt+Escape');

    const validation = validateShortcutClientSide(emergencyCombo.preview);
    expect(validation.valid).toBe(false);
    expect(validation.error).toContain('permanently reserved for the Global Emergency Stop');

    const escShortValidation = validateShortcutClientSide('Ctrl+Alt+Esc');
    expect(escShortValidation.valid).toBe(false);
    expect(escShortValidation.error).toContain('permanently reserved');
  });

  it('persists custom shortcut and voice settings via API client', async () => {
    const updatedStatus = makeMockVoiceStatus({
      settings: {
        shortcut_enabled: true,
        shortcut: 'Ctrl+Alt+M',
        microphone_device: 1,
        tts_enabled: false,
        piper_voice: 'en_US-lessac-medium.onnx',
        streaming_vad_enabled: true,
      },
      shortcut_status: {
        shortcut: 'Ctrl+Alt+M',
        enabled: true,
        is_registered: true,
        registration_error: null,
        trigger_count: 0,
        reserved_emergency_shortcut: 'Ctrl+Alt+Escape',
      },
    });

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify(updatedStatus), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    const res = await apiClient.updateVoiceConfig({
      shortcut: 'Ctrl+Alt+M',
      microphone_device: 1,
      tts_enabled: false,
    });

    expect(res.ok).toBe(true);
    expect(res.data?.settings.shortcut).toBe('Ctrl+Alt+M');
    expect(res.data?.settings.microphone_device).toBe(1);
    expect(res.data?.settings.tts_enabled).toBe(false);
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringContaining('/agent/voice/config'),
      expect.objectContaining({
        method: 'POST',
      })
    );
  });

  it('handles shortcut conflict (HTTP 409) and microphone test errors gracefully', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail:
            "Could not register shortcut 'Ctrl+Shift+F12': Hotkey already registered. Previous shortcut 'Ctrl+Shift+V' remains active.",
        }),
        {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        }
      )
    );

    const conflictRes = await apiClient.updateVoiceShortcut('Ctrl+Shift+F12', true);
    expect(conflictRes.ok).toBe(false);
    expect(conflictRes.status).toBe(409);
    expect(conflictRes.error).toContain("Previous shortcut 'Ctrl+Shift+V' remains active");

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          success: true,
          device: {
            index: 0,
            name: 'Test Mic',
            host_api: 'WASAPI',
            channels: 1,
            default_sample_rate: 16000,
          },
          sample_rate: 16000,
          channels: 1,
          rms_level: 0.045,
          peak_level: 0.21,
          signal_detected: true,
          message: "Microphone [0] 'Test Mic' tested successfully.",
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }
      )
    );

    const testRes = await apiClient.testMicrophone(0, 0.5);
    expect(testRes.ok).toBe(true);
    expect(testRes.data?.success).toBe(true);
    expect(testRes.data?.signal_detected).toBe(true);
  });
});
