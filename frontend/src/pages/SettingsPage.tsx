import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Settings,
  Mic,
  Volume2,
  Keyboard,
  Activity,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  ShieldAlert,
  Radio,
  Save,
  X,
  RotateCcw,
  Download,
  ShieldCheck,
  ExternalLink,
  Clock,
  Sparkles,
} from 'lucide-react';
import apiClient from '../api/client';
import {
  VoiceStatusResponse,
  MicrophoneDevice,
  MicrophoneTestResponse,
  PiperVoiceModel,
  UpdateStatusResponse,
  UpdateChannel,
} from '../api/types';
import {
  formatBytes,
  formatPublishedDate,
} from '../utils/updateUtils';
import {
  formatKeyboardEventToShortcut,
  validateShortcutClientSide,
} from '../utils/shortcutUtils';

interface SettingsPageProps {
  isBackendConnected: boolean;
}

export const SettingsPage: React.FC<SettingsPageProps> = ({ isBackendConnected }) => {
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatusResponse | null>(null);
  const [microphones, setMicrophones] = useState<MicrophoneDevice[]>([]);
  const [voices, setVoices] = useState<PiperVoiceModel[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [feedbackMsg, setFeedbackMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Shortcut Capture State
  const [isCapturingShortcut, setIsCapturingShortcut] = useState<boolean>(false);
  const [capturedShortcut, setCapturedShortcut] = useState<string>('');
  const [shortcutValidationError, setShortcutValidationError] = useState<string | null>(null);
  const captureBoxRef = useRef<HTMLDivElement>(null);

  // Microphone Test State
  const [isTestingMic, setIsTestingMic] = useState<boolean>(false);
  const [micTestResult, setMicTestResult] = useState<MicrophoneTestResponse | null>(null);

  // Software Update State
  const [updateStatus, setUpdateStatus] = useState<UpdateStatusResponse | null>(null);
  const [isCheckingUpdate, setIsCheckingUpdate] = useState<boolean>(false);
  const [isUpdateActionPending, setIsUpdateActionPending] = useState<boolean>(false);
  const [showSettingsConfirmInstall, setShowSettingsConfirmInstall] = useState<boolean>(false);
  const [relaunchOnInstall, setRelaunchOnInstall] = useState<boolean>(true);

  const loadVoiceSettingsAndStatus = useCallback(async () => {
    if (!isBackendConnected) return;
    setIsLoading(true);
    try {
      const [statusRes, micsRes, voicesRes, updRes] = await Promise.all([
        apiClient.getVoiceStatus(),
        apiClient.getMicrophones(),
        apiClient.getPiperVoices(),
        apiClient.getUpdateStatus(),
      ]);

      if (statusRes.ok && statusRes.data) {
        setVoiceStatus(statusRes.data);
      }
      if (micsRes.ok && micsRes.data) {
        setMicrophones(micsRes.data.devices || []);
      }
      if (voicesRes.ok && voicesRes.data) {
        setVoices(voicesRes.data.voices || []);
      }
      if (updRes.ok && updRes.data) {
        setUpdateStatus(updRes.data);
      }
    } catch (err: unknown) {
      console.error('Failed to load voice settings:', err);
    } finally {
      setIsLoading(false);
    }
  }, [isBackendConnected]);

  useEffect(() => {
    if (!isBackendConnected || !updateStatus) return;
    if (updateStatus.state !== 'downloading' && updateStatus.state !== 'verifying') return;

    const timer = setInterval(async () => {
      const res = await apiClient.getUpdateStatus();
      if (res.ok && res.data) {
        setUpdateStatus(res.data);
      }
    }, 600);

    return () => clearInterval(timer);
  }, [isBackendConnected, updateStatus]);

  const handleManualCheckForUpdates = async () => {
    setFeedbackMsg(null);
    setIsCheckingUpdate(true);
    try {
      const res = await apiClient.checkForUpdates(true);
      if (res.ok && res.data) {
        setUpdateStatus(res.data);
        if (res.data.update_available) {
          setFeedbackMsg({
            type: 'success',
            text: `New update found: VoxPilot v${res.data.latest_version} is available!`,
          });
        } else if (res.data.state === 'up_to_date') {
          setFeedbackMsg({
            type: 'success',
            text: res.data.status_message || `VoxPilot v${res.data.installed_version} is up to date.`,
          });
        } else if (res.data.error_message) {
          setFeedbackMsg({
            type: 'error',
            text: res.data.error_message,
          });
        }
      } else {
        setFeedbackMsg({
          type: 'error',
          text: res.error || 'Failed to check for updates.',
        });
      }
    } finally {
      setIsCheckingUpdate(false);
    }
  };

  const handleToggleAutoUpdateCheck = async (enabled: boolean) => {
    setFeedbackMsg(null);
    setIsUpdateActionPending(true);
    try {
      const res = await apiClient.updateUpdateSettings({ auto_check_enabled: enabled });
      if (res.ok && res.data) {
        const statusRes = await apiClient.getUpdateStatus();
        if (statusRes.ok && statusRes.data) {
          setUpdateStatus(statusRes.data);
        }
        setFeedbackMsg({
          type: 'success',
          text: enabled
            ? 'Automatic update checks enabled.'
            : 'Automatic update checks disabled.',
        });
      } else {
        setFeedbackMsg({
          type: 'error',
          text: res.error || 'Failed to save automatic update check preference.',
        });
      }
    } finally {
      setIsUpdateActionPending(false);
    }
  };

  const handleChangeUpdateChannel = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const channel = e.target.value as UpdateChannel;
    setFeedbackMsg(null);
    setIsUpdateActionPending(true);
    try {
      const res = await apiClient.updateUpdateSettings({ channel });
      if (res.ok && res.data) {
        const statusRes = await apiClient.getUpdateStatus();
        if (statusRes.ok && statusRes.data) {
          setUpdateStatus(statusRes.data);
        }
        setFeedbackMsg({
          type: 'success',
          text: `Update channel switched to '${channel}'.`,
        });
      } else {
        setFeedbackMsg({
          type: 'error',
          text: res.error || 'Failed to change update channel.',
        });
      }
    } finally {
      setIsUpdateActionPending(false);
    }
  };

  const handleStartDownloadFromSettings = async () => {
    setFeedbackMsg(null);
    setIsUpdateActionPending(true);
    try {
      const res = await apiClient.startUpdateDownload(true);
      if (res.ok && res.data) {
        setUpdateStatus(res.data);
      } else {
        setFeedbackMsg({
          type: 'error',
          text: res.error || 'Failed to start installer download.',
        });
      }
    } finally {
      setIsUpdateActionPending(false);
    }
  };

  const handleCancelDownloadFromSettings = async () => {
    setFeedbackMsg(null);
    setIsUpdateActionPending(true);
    try {
      const res = await apiClient.cancelUpdateDownload();
      if (res.ok && res.data) {
        setUpdateStatus(res.data);
        setFeedbackMsg({
          type: 'success',
          text: 'Installer download cancelled.',
        });
      }
    } finally {
      setIsUpdateActionPending(false);
    }
  };

  const handleConfirmInstallFromSettings = async () => {
    setFeedbackMsg(null);
    setIsUpdateActionPending(true);
    try {
      const res = await apiClient.installUpdate(true, relaunchOnInstall, true);
      if (res.ok && res.data) {
        setUpdateStatus(res.data);
        setShowSettingsConfirmInstall(false);
        setFeedbackMsg({
          type: 'success',
          text: res.data.status_message,
        });
      } else {
        setFeedbackMsg({
          type: 'error',
          text: res.error || 'Failed to launch external updater.',
        });
      }
    } finally {
      setIsUpdateActionPending(false);
    }
  };

  useEffect(() => {
    loadVoiceSettingsAndStatus();
  }, [loadVoiceSettingsAndStatus]);

  useEffect(() => {
    if (isCapturingShortcut && captureBoxRef.current) {
      captureBoxRef.current.focus();
    }
  }, [isCapturingShortcut]);

  const handleToggleShortcutEnabled = async (enabled: boolean) => {
    setFeedbackMsg(null);
    setIsSaving(true);
    try {
      const res = await apiClient.updateVoiceConfig({ shortcut_enabled: enabled });
      if (res.ok && res.data) {
        setVoiceStatus(res.data);
        setFeedbackMsg({
          type: 'success',
          text: enabled
            ? `Global voice shortcut (${res.data.shortcut_status.shortcut}) enabled.`
            : 'Global voice shortcut disabled.',
        });
      } else {
        setFeedbackMsg({ type: 'error', text: res.error || 'Failed to update shortcut setting.' });
      }
    } finally {
      setIsSaving(false);
    }
  };

  const handleStartCaptureShortcut = () => {
    setFeedbackMsg(null);
    setShortcutValidationError(null);
    setCapturedShortcut(voiceStatus?.settings?.shortcut || 'Ctrl+Shift+V');
    setIsCapturingShortcut(true);
  };

  const handleCancelCaptureShortcut = () => {
    setIsCapturingShortcut(false);
    setCapturedShortcut('');
    setShortcutValidationError(null);
  };

  const handleCaptureKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();

    const { preview, isComplete } = formatKeyboardEventToShortcut(e);
    setCapturedShortcut(preview);

    if (isComplete) {
      const check = validateShortcutClientSide(preview);
      setShortcutValidationError(check.error);
    } else {
      setShortcutValidationError(null);
    }
  };

  const handleSaveShortcut = async (shortcutToSave?: string) => {
    const candidate = shortcutToSave ?? capturedShortcut;
    const validation = validateShortcutClientSide(candidate);
    if (!validation.valid) {
      setShortcutValidationError(validation.error);
      return;
    }

    setIsSaving(true);
    setShortcutValidationError(null);
    setFeedbackMsg(null);

    try {
      const res = await apiClient.updateVoiceShortcut(candidate, voiceStatus?.settings?.shortcut_enabled ?? true);
      if (res.ok && res.data) {
        setVoiceStatus(res.data);
        setIsCapturingShortcut(false);
        setFeedbackMsg({
          type: 'success',
          text: `Global voice shortcut updated and saved as '${res.data.shortcut_status.shortcut}'.`,
        });
      } else {
        setShortcutValidationError(res.error || 'Failed to register shortcut on Windows.');
        // Refresh status to confirm rollback to previous shortcut
        const statusRes = await apiClient.getVoiceStatus();
        if (statusRes.ok && statusRes.data) {
          setVoiceStatus(statusRes.data);
        }
      }
    } finally {
      setIsSaving(false);
    }
  };

  const handleSelectMicrophone = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const rawVal = e.target.value;
    const deviceVal: number | string | null =
      rawVal === '' || rawVal === 'default'
        ? null
        : /^\d+$/.test(rawVal)
          ? parseInt(rawVal, 10)
          : rawVal;

    setFeedbackMsg(null);
    setMicTestResult(null);
    setIsSaving(true);
    try {
      const res = await apiClient.updateVoiceConfig({ microphone_device: deviceVal });
      if (res.ok && res.data) {
        setVoiceStatus(res.data);
        setFeedbackMsg({
          type: 'success',
          text: 'Microphone preference saved.',
        });
      } else {
        setFeedbackMsg({
          type: 'error',
          text: res.error || 'Failed to update microphone selection.',
        });
      }
    } finally {
      setIsSaving(false);
    }
  };

  const handleTestMicrophone = async () => {
    setIsTestingMic(true);
    setMicTestResult(null);
    setFeedbackMsg(null);
    try {
      const configuredDevice = voiceStatus?.settings?.microphone_device ?? null;
      const res = await apiClient.testMicrophone(configuredDevice, 0.5);
      if (res.ok && res.data) {
        setMicTestResult(res.data);
      } else {
        setMicTestResult({
          success: false,
          device: null,
          sample_rate: 0,
          channels: 0,
          rms_level: 0,
          peak_level: 0,
          signal_detected: false,
          message: res.error || 'Microphone test failed.',
        });
      }
    } finally {
      setIsTestingMic(false);
    }
  };

  const handleToggleTtsEnabled = async (enabled: boolean) => {
    setFeedbackMsg(null);
    setIsSaving(true);
    try {
      const res = await apiClient.updateVoiceConfig({ tts_enabled: enabled });
      if (res.ok && res.data) {
        setVoiceStatus(res.data);
        setFeedbackMsg({
          type: 'success',
          text: enabled
            ? 'Voice output (Piper TTS) enabled.'
            : 'Voice output (Piper TTS) muted.',
        });
      } else {
        setFeedbackMsg({ type: 'error', text: res.error || 'Failed to update TTS setting.' });
      }
    } finally {
      setIsSaving(false);
    }
  };

  const handleSelectPiperVoice = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const voiceId = e.target.value;
    setFeedbackMsg(null);
    setIsSaving(true);
    try {
      const res = await apiClient.updateVoiceConfig({ piper_voice: voiceId });
      if (res.ok && res.data) {
        setVoiceStatus(res.data);
        setFeedbackMsg({
          type: 'success',
          text: `Piper voice updated to '${voiceId}'.`,
        });
      } else {
        setFeedbackMsg({ type: 'error', text: res.error || 'Failed to update Piper voice.' });
      }
    } finally {
      setIsSaving(false);
    }
  };

  const handleToggleVadEnabled = async (enabled: boolean) => {
    setFeedbackMsg(null);
    setIsSaving(true);
    try {
      const res = await apiClient.updateVoiceConfig({ streaming_vad_enabled: enabled });
      if (res.ok && res.data) {
        setVoiceStatus(res.data);
        setFeedbackMsg({
          type: 'success',
          text: enabled
            ? 'Dynamic Streaming Voice Activity Detection enabled.'
            : 'Switched to fixed-duration recording mode.',
        });
      } else {
        setFeedbackMsg({ type: 'error', text: res.error || 'Failed to update VAD setting.' });
      }
    } finally {
      setIsSaving(false);
    }
  };

  const settings = voiceStatus?.settings;
  const shortcutStatus = voiceStatus?.shortcut_status;
  const components = voiceStatus?.components;
  const currentShortcut = shortcutStatus?.shortcut || settings?.shortcut || 'Ctrl+Shift+V';
  const isShortcutEnabled = settings?.shortcut_enabled ?? true;
  const isShortcutRegistered = shortcutStatus?.is_registered ?? false;

  return (
    <div className="page-container">
      {/* Page Header */}
      <div className="page-header">
        <div>
          <h1 className="page-title">Voice &amp; Shortcut Settings</h1>
          <p className="page-description">
            Configure your global Windows voice activation shortcut, microphone hardware, Faster-Whisper speech recognition, and Piper TTS output.
          </p>
        </div>

        <button
          className="refresh-btn"
          onClick={loadVoiceSettingsAndStatus}
          disabled={isLoading || !isBackendConnected}
          type="button"
        >
          <RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />
          <span>Refresh Hardware Status</span>
        </button>
      </div>

      {!isBackendConnected && (
        <div className="panel-error-alert">
          <AlertTriangle size={16} />
          <span>Backend service is currently unreachable. Connect to FastAPI to modify voice settings.</span>
        </div>
      )}

      {feedbackMsg && (
        <div
          className={
            feedbackMsg.type === 'success' ? 'settings-feedback-banner success' : 'panel-error-alert'
          }
          role="alert"
        >
          {feedbackMsg.type === 'success' ? (
            <CheckCircle2 size={16} />
          ) : (
            <AlertTriangle size={16} />
          )}
          <span>{feedbackMsg.text}</span>
        </div>
      )}

      {/* 1. Global Keyboard Shortcut Section */}
      <div className="dashboard-card">
        <div className="dashboard-card-header">
          <div className="dashboard-card-title-group">
            <Keyboard size={18} className="text-accent-blue" />
            <div>
              <h2 className="dashboard-card-title">Global Voice Mode Keyboard Shortcut</h2>
              <p className="dashboard-card-description">
                Toggle Voice Mode on or off from anywhere in Windows, even when another application is in the foreground.
              </p>
            </div>
          </div>

          <span
            className={`status-pill ${!isShortcutEnabled
              ? 'inactive'
              : isShortcutRegistered
                ? 'success'
                : 'danger'
              }`}
          >
            {!isShortcutEnabled
              ? 'Shortcut Disabled'
              : isShortcutRegistered
                ? 'Registered (Active Globally)'
                : 'Not Registered'}
          </span>
        </div>

        <div className="settings-section-body">
          <div className="settings-row">
            <div className="settings-row-info">
              <span className="settings-label">Enable Global Voice Shortcut</span>
              <span className="settings-hint">
                Registers a system-wide Windows hotkey via Win32 RegisterHotKey.
              </span>
            </div>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={isShortcutEnabled}
                onChange={(e) => handleToggleShortcutEnabled(e.target.checked)}
                disabled={!isBackendConnected || isSaving}
              />
              <span>{isShortcutEnabled ? 'Enabled' : 'Disabled'}</span>
            </label>
          </div>

          <div className="settings-row">
            <div className="settings-row-info">
              <span className="settings-label">Current Keyboard Shortcut</span>
              <span className="settings-hint">
                Press once to start listening; press again to stop Voice Mode immediately.
              </span>
            </div>

            <div className="shortcut-controls-group">
              <code className="shortcut-key-display">{currentShortcut}</code>
              {!isCapturingShortcut && (
                <>
                  <button
                    type="button"
                    className="state-action-btn"
                    style={{ marginTop: 0, padding: '7px 14px' }}
                    onClick={handleStartCaptureShortcut}
                    disabled={!isBackendConnected || isSaving}
                  >
                    <Keyboard size={14} />
                    <span>Change Shortcut</span>
                  </button>
                  {currentShortcut !== 'Ctrl+Shift+V' && (
                    <button
                      type="button"
                      className="refresh-btn"
                      onClick={() => handleSaveShortcut('Ctrl+Shift+V')}
                      disabled={!isBackendConnected || isSaving}
                      title="Restore default Ctrl+Shift+V shortcut"
                    >
                      <RotateCcw size={14} />
                      <span>Reset Default</span>
                    </button>
                  )}
                </>
              )}
            </div>
          </div>

          {isCapturingShortcut && (
            <div className="shortcut-capture-panel">
              <div className="shortcut-capture-header">
                <span className="settings-label">Press your new key combination:</span>
                <span className="settings-hint">
                  Hold modifier keys (Ctrl, Alt, Shift, Win) and press a letter, number, or function key.
                </span>
              </div>

              <div
                ref={captureBoxRef}
                tabIndex={0}
                onKeyDown={handleCaptureKeyDown}
                className="shortcut-capture-box"
                role="textbox"
                aria-label="Press new shortcut combination"
              >
                <Keyboard size={18} className="text-accent-blue" />
                <span className="captured-combo-text">
                  {capturedShortcut || 'Press keys now...'}
                </span>
              </div>

              {shortcutValidationError && (
                <div className="panel-error-alert" style={{ marginTop: '8px' }}>
                  <AlertTriangle size={15} />
                  <span>{shortcutValidationError}</span>
                </div>
              )}

              <div className="shortcut-capture-actions">
                <button
                  type="button"
                  className="state-action-btn"
                  style={{ marginTop: 0 }}
                  onClick={() => handleSaveShortcut()}
                  disabled={isSaving || !capturedShortcut || capturedShortcut.endsWith('+...')}
                >
                  <Save size={14} />
                  <span>{isSaving ? 'Saving...' : 'Save Shortcut'}</span>
                </button>
                <button
                  type="button"
                  className="refresh-btn"
                  onClick={handleCancelCaptureShortcut}
                  disabled={isSaving}
                >
                  <X size={14} />
                  <span>Cancel</span>
                </button>
              </div>
            </div>
          )}

          {shortcutStatus?.registration_error && (
            <div className="panel-error-alert">
              <AlertTriangle size={15} />
              <span>{shortcutStatus.registration_error}</span>
            </div>
          )}

          <div className="hotkey-hint-row">
            <ShieldAlert size={14} className="text-accent-blue" />
            <span>
              Protected Safety Shortcut: <code>Ctrl+Alt+Escape</code> is permanently reserved for the Global Emergency Stop and cannot be reassigned.
            </span>
          </div>
        </div>
      </div>

      {/* 2. Microphone & Audio Capture Section */}
      <div className="dashboard-card">
        <div className="dashboard-card-header">
          <div className="dashboard-card-title-group">
            <Mic size={18} className="text-accent-blue" />
            <div>
              <h2 className="dashboard-card-title">Microphone &amp; Audio Capture</h2>
              <p className="dashboard-card-description">
                Select your preferred input device and verify hardware signal capture. Microphone is only active while Voice Mode is listening.
              </p>
            </div>
          </div>

          <span
            className={`status-pill ${components?.microphone.available ? 'success' : 'danger'
              }`}
          >
            {components?.microphone.available
              ? `${components.microphone.device_count} Input Device(s)`
              : 'No Microphone Found'}
          </span>
        </div>

        <div className="settings-section-body">
          <div className="settings-row">
            <div className="settings-row-info">
              <span className="settings-label">Input Microphone Device</span>
              <span className="settings-hint">
                {components?.microphone.selected_device
                  ? `Active Hardware: [${components.microphone.selected_device.index}] ${components.microphone.selected_device.name} (${components.microphone.selected_device.host_api})`
                  : 'Using system default audio input device.'}
              </span>
            </div>

            <div className="shortcut-controls-group">
              <select
                className="status-filter-select"
                value={
                  settings?.microphone_device === null || settings?.microphone_device === undefined
                    ? 'default'
                    : String(settings.microphone_device)
                }
                onChange={handleSelectMicrophone}
                disabled={!isBackendConnected || isSaving}
                aria-label="Select Microphone"
              >
                <option value="default">System Default Microphone</option>
                {microphones.map((mic) => (
                  <option key={mic.index} value={String(mic.index)}>
                    [{mic.index}] {mic.name} ({mic.host_api} - {Math.round(mic.default_sample_rate)} Hz)
                  </option>
                ))}
              </select>

              <button
                type="button"
                className="refresh-btn"
                onClick={handleTestMicrophone}
                disabled={!isBackendConnected || isTestingMic}
              >
                <Radio size={14} className={isTestingMic ? 'animate-spin' : ''} />
                <span>{isTestingMic ? 'Testing Mic...' : 'Test Microphone'}</span>
              </button>
            </div>
          </div>

          {micTestResult && (
            <div
              className={
                micTestResult.success ? 'settings-feedback-banner success' : 'panel-error-alert'
              }
            >
              {micTestResult.success ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
              <div>
                <div>{micTestResult.message}</div>
                {micTestResult.success && (
                  <div style={{ fontSize: '11.5px', opacity: 0.85, marginTop: '2px' }}>
                    RMS Level: {micTestResult.rms_level} • Peak Level: {micTestResult.peak_level} • Privacy verified: test buffer discarded in-memory.
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 3. Voice Output (Piper TTS) Section */}
      <div className="dashboard-card">
        <div className="dashboard-card-header">
          <div className="dashboard-card-title-group">
            <Volume2 size={18} className="text-accent-blue" />
            <div>
              <h2 className="dashboard-card-title">Voice Output (Piper Neural TTS)</h2>
              <p className="dashboard-card-description">
                Configure offline neural text-to-speech synthesis for spoken AI responses.
              </p>
            </div>
          </div>

          <span
            className={`status-pill ${!settings?.tts_enabled
              ? 'inactive'
              : components?.tts.model_exists
                ? 'success'
                : 'danger'
              }`}
          >
            {!settings?.tts_enabled
              ? 'Voice Output Muted'
              : components?.tts.model_exists
                ? 'Piper ONNX Ready'
                : 'Voice Model Missing'}
          </span>
        </div>

        <div className="settings-section-body">
          <div className="settings-row">
            <div className="settings-row-info">
              <span className="settings-label">Speak AI Agent Responses (Piper TTS)</span>
              <span className="settings-hint">
                Automatically synthesize and play the assistant&apos;s response during Voice Mode.
              </span>
            </div>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={settings?.tts_enabled ?? true}
                onChange={(e) => handleToggleTtsEnabled(e.target.checked)}
                disabled={!isBackendConnected || isSaving}
              />
              <span>{settings?.tts_enabled ?? true ? 'Enabled' : 'Disabled'}</span>
            </label>
          </div>

          <div className="settings-row">
            <div className="settings-row-info">
              <span className="settings-label">Installed Piper Voice Model</span>
              <span className="settings-hint">
                Local ONNX neural voice model used for speech synthesis.
              </span>
            </div>

            <select
              className="status-filter-select"
              value={settings?.piper_voice || 'en_US-lessac-medium.onnx'}
              onChange={handleSelectPiperVoice}
              disabled={!isBackendConnected || isSaving}
              aria-label="Select Piper Voice Model"
            >
              {(voices.length > 0
                ? voices
                : [{ id: 'en_US-lessac-medium.onnx', name: 'en_US-lessac-medium', path: '', has_config: true }]
              ).map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} ({v.id})
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* 4. Speech Recognition & Voice Activity Diagnostics */}
      <div className="dashboard-card">
        <div className="dashboard-card-header">
          <div className="dashboard-card-title-group">
            <Activity size={18} className="text-accent-blue" />
            <div>
              <h2 className="dashboard-card-title">Speech Recognition &amp; Voice Activity Status</h2>
              <p className="dashboard-card-description">
                Telemetry for Faster-Whisper speech-to-text and WebRTC + Silero Voice Activity Detection.
              </p>
            </div>
          </div>

          <span className="status-pill success">
            Phase: {(voiceStatus?.phase || 'idle').toUpperCase()}
          </span>
        </div>

        <div className="settings-section-body">
          <div className="settings-row">
            <div className="settings-row-info">
              <span className="settings-label">Dynamic Streaming Voice Activity Detection (VAD)</span>
              <span className="settings-hint">
                Automatically detects speech onset and stops recording when you finish speaking.
              </span>
            </div>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={settings?.streaming_vad_enabled ?? true}
                onChange={(e) => handleToggleVadEnabled(e.target.checked)}
                disabled={!isBackendConnected || isSaving}
              />
              <span>{settings?.streaming_vad_enabled ?? true ? 'Streaming VAD Active' : 'Fixed Duration'}</span>
            </label>
          </div>

          <div className="metrics-grid">
            <div className="metric-box">
              <div className="metric-box-header">
                <span>Speech Recognition</span>
                <Settings size={14} />
              </div>
              <div className="metric-box-value" style={{ fontSize: '16px' }}>
                Faster-Whisper ({components?.stt.model_size || 'base'})
              </div>
              <div className="metric-box-footer">
                Compute: {components?.stt.device || 'cpu'} ({components?.stt.compute_type || 'int8'}) •{' '}
                {components?.stt.loaded
                  ? 'Loaded in RAM'
                  : components?.stt.cached
                    ? 'Cached on Disk'
                    : 'Auto-downloads on first use'}
              </div>
            </div>

            <div className="metric-box">
              <div className="metric-box-header">
                <span>Voice Activity Detection</span>
                <Activity size={14} />
              </div>
              <div className="metric-box-value" style={{ fontSize: '16px' }}>
                {components?.vad.enabled ?? true ? 'WebRTC + Silero VAD' : 'Fixed 5s Window'}
              </div>
              <div className="metric-box-footer">
                Frame: {components?.vad.frame_duration_ms || 30}ms • Silence Endpoint:{' '}
                {components?.vad.end_silence_ms || 900}ms
              </div>
            </div>

            <div className="metric-box">
              <div className="metric-box-header">
                <span>Microphone Stream</span>
                <Mic size={14} />
              </div>
              <div className="metric-box-value" style={{ fontSize: '16px' }}>
                {voiceStatus?.is_recording ? (
                  <span style={{ color: 'var(--status-danger)' }}>RECORDING</span>
                ) : (
                  <span style={{ color: 'var(--status-success)' }}>Released (Idle)</span>
                )}
              </div>
              <div className="metric-box-footer">
                Zero background recording when Voice Mode is off
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 5. Application Updates & Version Management Section */}
      <div className="dashboard-card">
        <div className="dashboard-card-header">
          <div className="dashboard-card-title-group">
            <Sparkles size={18} className="text-accent-blue" />
            <div>
              <h2 className="dashboard-card-title">Software Updates &amp; Version Management</h2>
              <p className="dashboard-card-description">
                Check GitHub Releases for official VoxPilot updates, verify SHA-256 package integrity, and install updates after your approval.
              </p>
            </div>
          </div>

          <span
            className={`status-pill ${updateStatus?.state === 'ready_to_install' || updateStatus?.state === 'up_to_date'
                ? 'success'
                : updateStatus?.update_available
                  ? 'warning'
                  : updateStatus?.state === 'failed' || updateStatus?.state === 'offline'
                    ? 'danger'
                    : 'inactive'
              }`}
          >
            {updateStatus?.state === 'checking'
              ? 'Checking...'
              : updateStatus?.state === 'downloading'
                ? `Downloading (${updateStatus.download.progress_pct}%)`
                : updateStatus?.state === 'verifying'
                  ? 'Verifying SHA-256...'
                  : updateStatus?.state === 'ready_to_install'
                    ? 'Verified & Ready to Install'
                    : updateStatus?.update_available
                      ? `Update Available (v${updateStatus.latest_version})`
                      : updateStatus?.state === 'up_to_date'
                        ? 'Up to Date'
                        : updateStatus?.state === 'offline'
                          ? 'Offline'
                          : updateStatus?.state === 'failed'
                            ? 'Update Error'
                            : `Installed v${updateStatus?.installed_version || '0.5.0'}`}
          </span>
        </div>

        <div className="settings-section-body">
          <div className="metrics-grid" style={{ marginBottom: '16px' }}>
            <div className="metric-box">
              <div className="metric-box-header">
                <span>Installed Version</span>
                <ShieldCheck size={14} />
              </div>
              <div className="metric-box-value" style={{ fontSize: '18px' }}>
                v{updateStatus?.installed_version || '0.5.0'}
              </div>
              <div className="metric-box-footer">
                Repository: {updateStatus?.release_repo || 'Thanush505/VoxPilot'}
              </div>
            </div>

            <div className="metric-box">
              <div className="metric-box-header">
                <span>Latest Official Version</span>
                <Sparkles size={14} />
              </div>
              <div className="metric-box-value" style={{ fontSize: '18px' }}>
                {updateStatus?.latest_version ? `v${updateStatus.latest_version}` : 'Not checked yet'}
              </div>
              <div className="metric-box-footer">
                {updateStatus?.update_available
                  ? 'Newer version available on GitHub'
                  : 'Matches or older than installed build'}
              </div>
            </div>

            <div className="metric-box">
              <div className="metric-box-header">
                <span>Last Update Check</span>
                <Clock size={14} />
              </div>
              <div className="metric-box-value" style={{ fontSize: '15px' }}>
                {updateStatus?.last_checked_at
                  ? formatPublishedDate(updateStatus.last_checked_at)
                  : 'Never'}
              </div>
              <div className="metric-box-footer">
                Channel: {(updateStatus?.channel || 'stable').toUpperCase()}
              </div>
            </div>
          </div>

          <div className="settings-row">
            <div className="settings-row-info">
              <span className="settings-label">Check for Official Releases</span>
              <span className="settings-hint">
                {updateStatus?.status_message || 'Query GitHub Releases for newer VoxPilot installers.'}
              </span>
            </div>

            <div className="shortcut-controls-group">
              <button
                type="button"
                className="state-action-btn"
                style={{ marginTop: 0, padding: '7px 14px' }}
                onClick={handleManualCheckForUpdates}
                disabled={
                  !isBackendConnected ||
                  isCheckingUpdate ||
                  updateStatus?.state === 'downloading' ||
                  updateStatus?.state === 'verifying'
                }
              >
                <RefreshCw size={14} className={isCheckingUpdate ? 'animate-spin' : ''} />
                <span>{isCheckingUpdate ? 'Checking GitHub...' : 'Check for Updates'}</span>
              </button>

              {updateStatus?.update_available &&
                updateStatus.state !== 'downloading' &&
                updateStatus.state !== 'verifying' &&
                updateStatus.state !== 'ready_to_install' && (
                  <button
                    type="button"
                    className="state-action-btn update-primary-btn"
                    style={{ marginTop: 0, padding: '7px 14px' }}
                    onClick={handleStartDownloadFromSettings}
                    disabled={!isBackendConnected || isUpdateActionPending}
                  >
                    <Download size={14} />
                    <span>Download Update (v{updateStatus.latest_version})</span>
                  </button>
                )}

              {updateStatus?.state === 'downloading' && (
                <button
                  type="button"
                  className="refresh-btn"
                  onClick={handleCancelDownloadFromSettings}
                  disabled={isUpdateActionPending}
                >
                  <X size={14} />
                  <span>Cancel Download</span>
                </button>
              )}

              {updateStatus?.state === 'ready_to_install' && (
                <button
                  type="button"
                  className="state-action-btn update-install-btn"
                  style={{ marginTop: 0, padding: '7px 14px' }}
                  onClick={() => setShowSettingsConfirmInstall(true)}
                  disabled={isUpdateActionPending}
                >
                  <CheckCircle2 size={14} />
                  <span>Install Update</span>
                </button>
              )}
            </div>
          </div>

          {(updateStatus?.state === 'downloading' || updateStatus?.state === 'verifying') && (
            <div className="settings-update-progress-panel">
              <div className="settings-update-progress-meta">
                <span>
                  {updateStatus.state === 'verifying'
                    ? 'Verifying SHA-256 package checksum...'
                    : `Downloading ${updateStatus.release?.installer_name || 'VoxPilot-Setup.exe'}`}
                </span>
                <span>
                  {formatBytes(updateStatus.download.downloaded_bytes)} /{' '}
                  {formatBytes(
                    updateStatus.download.total_bytes ||
                    updateStatus.release?.installer_size_bytes
                  )}{' '}
                  ({updateStatus.download.progress_pct}%)
                </span>
              </div>
              <div
                className="update-progress-track"
                role="progressbar"
                aria-valuenow={updateStatus.download.progress_pct}
              >
                <div
                  className="update-progress-fill"
                  style={{
                    width: `${Math.max(2, Math.min(100, updateStatus.download.progress_pct))}%`,
                  }}
                />
              </div>
            </div>
          )}

          {updateStatus?.download?.sha256_verified && (
            <div className="settings-feedback-banner success">
              <ShieldCheck size={16} />
              <div>
                <div>
                  <strong>SHA-256 Checksum Verified:</strong>{' '}
                  <code>{updateStatus.download.computed_sha256}</code>
                </div>
                {updateStatus.download.signature && (
                  <div style={{ fontSize: '12px', opacity: 0.9, marginTop: '2px' }}>
                    {updateStatus.download.signature.publisher_verification_note}
                  </div>
                )}
              </div>
            </div>
          )}

          {updateStatus?.error_message && (
            <div className="panel-error-alert">
              <AlertTriangle size={16} />
              <span>{updateStatus.error_message}</span>
            </div>
          )}

          {updateStatus?.release && updateStatus.update_available && (
            <div className="update-release-notes-box" style={{ marginTop: '10px' }}>
              <div className="update-release-notes-header">
                <span>
                  <strong>
                    {updateStatus.release.title || `VoxPilot v${updateStatus.release.version}`}
                  </strong>{' '}
                  &bull; Published {formatPublishedDate(updateStatus.release.published_at)}
                </span>
                {updateStatus.release.release_url && (
                  <a
                    href={updateStatus.release.release_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="refresh-btn"
                    style={{ textDecoration: 'none', padding: '4px 10px' }}
                  >
                    <ExternalLink size={12} />
                    <span>View on GitHub</span>
                  </a>
                )}
              </div>
              <pre className="update-release-notes-body">
                {updateStatus.release.notes || 'No release notes provided for this version.'}
              </pre>
            </div>
          )}

          <div className="settings-row">
            <div className="settings-row-info">
              <span className="settings-label">Automatic Update Checks</span>
              <span className="settings-hint">
                Automatically check GitHub Releases on startup without blocking VoxPilot initialization. Automatic installation is permanently disabled for safety.
              </span>
            </div>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={updateStatus?.auto_check_enabled ?? true}
                onChange={(e) => handleToggleAutoUpdateCheck(e.target.checked)}
                disabled={!isBackendConnected || isUpdateActionPending}
              />
              <span>{updateStatus?.auto_check_enabled ?? true ? 'Enabled' : 'Disabled'}</span>
            </label>
          </div>

          <div className="settings-row">
            <div className="settings-row-info">
              <span className="settings-label">Update Release Channel</span>
              <span className="settings-hint">
                Stable only checks official full releases; Beta also includes pre-release builds.
              </span>
            </div>
            <select
              className="status-filter-select"
              value={updateStatus?.channel || 'stable'}
              onChange={handleChangeUpdateChannel}
              disabled={!isBackendConnected || isUpdateActionPending}
              aria-label="Select Update Channel"
            >
              <option value="stable">Stable Releases (Recommended)</option>
              <option value="beta">Beta &amp; Pre-Release Channel</option>
            </select>
          </div>
        </div>
      </div>

      {showSettingsConfirmInstall && updateStatus && (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="settings-confirm-install-title"
        >
          <div className="modal-card update-confirm-modal">
            <div className="modal-header">
              <div className="dashboard-card-title-group">
                <ShieldCheck size={20} className="text-accent-blue" />
                <h2 id="settings-confirm-install-title" className="dashboard-card-title">
                  Confirm VoxPilot Update Installation
                </h2>
              </div>
              <button
                type="button"
                className="refresh-btn"
                onClick={() => setShowSettingsConfirmInstall(false)}
                disabled={isUpdateActionPending}
                aria-label="Close confirmation dialog"
              >
                <X size={16} />
              </button>
            </div>

            <div className="modal-body">
              <div className="panel-error-alert" style={{ marginBottom: '12px' }}>
                <AlertTriangle size={16} />
                <span>
                  Please save any active work before proceeding. VoxPilot will close so the
                  separate Windows updater can run the verified installer.
                </span>
              </div>

              <div className="update-confirm-details">
                <div>
                  <strong>Current Version:</strong> v{updateStatus.installed_version}
                </div>
                <div>
                  <strong>New Version:</strong> v{updateStatus.latest_version}
                </div>
                <div>
                  <strong>Verified SHA-256:</strong>{' '}
                  <code>
                    {updateStatus.download.computed_sha256 ||
                      updateStatus.release?.expected_sha256}
                  </code>
                </div>
                <div>
                  <strong>Data Preservation:</strong> All user data in{' '}
                  <code>%LOCALAPPDATA%\VoxPilot</code> and local Ollama models are preserved.
                </div>
              </div>

              <label className="checkbox-label" style={{ marginTop: '14px' }}>
                <input
                  type="checkbox"
                  checked={relaunchOnInstall}
                  onChange={(e) => setRelaunchOnInstall(e.target.checked)}
                />
                <span>Automatically relaunch VoxPilot after the update completes</span>
              </label>
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="refresh-btn"
                onClick={() => setShowSettingsConfirmInstall(false)}
                disabled={isUpdateActionPending}
              >
                <span>Cancel</span>
              </button>
              <button
                type="button"
                className="state-action-btn update-install-btn"
                style={{ marginTop: 0 }}
                onClick={handleConfirmInstallFromSettings}
                disabled={isUpdateActionPending}
              >
                <CheckCircle2 size={14} />
                <span>
                  {isUpdateActionPending
                    ? 'Starting Installer...'
                    : 'Close VoxPilot & Install Update'}
                </span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default SettingsPage;
