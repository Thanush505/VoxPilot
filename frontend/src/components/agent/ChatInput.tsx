import React, { useState, useRef, useEffect } from 'react';
import { Send, Loader2, Mic, MicOff, Square, Volume2, AlertCircle, Radio } from 'lucide-react';
import { VoicePhase } from '../../api/types';

interface ChatInputProps {
  onSendMessage: (text: string) => void;
  isLoading: boolean;
  disabled?: boolean;
  placeholder?: string;
  isVoiceActive?: boolean;
  voicePhase?: VoicePhase;
  isRecording?: boolean;
  voiceShortcut?: string;
  voiceError?: string | null;
  lastTranscript?: string | null;
  onToggleVoice?: () => void;
  onCancelVoice?: () => void;
}

const VOICE_PHASE_LABELS: Record<VoicePhase, string> = {
  idle: 'Voice Mode Idle',
  listening: 'Listening for speech...',
  transcribing: 'Transcribing speech (Faster-Whisper)...',
  processing: 'Processing command with VoxPilot Agent...',
  speaking: 'Speaking response (Piper TTS)...',
  error: 'Voice Mode Error',
};

export const ChatInput: React.FC<ChatInputProps> = ({
  onSendMessage,
  isLoading,
  disabled = false,
  placeholder = "Type an automation command (e.g., 'Open notepad and type Hello World')...",
  isVoiceActive = false,
  voicePhase = 'idle',
  isRecording = false,
  voiceShortcut = 'Ctrl+Shift+V',
  voiceError = null,
  lastTranscript = null,
  onToggleVoice,
  onCancelVoice,
}) => {
  const [text, setText] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!isLoading && !isVoiceActive && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [isLoading, isVoiceActive]);

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || isLoading || disabled) return;

    onSendMessage(trimmed);
    setText('');

    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    const target = e.target;
    target.style.height = 'auto';
    target.style.height = `${Math.min(target.scrollHeight, 140)}px`;
  };

  const showVoiceStatusBar = isVoiceActive || voicePhase !== 'idle' || Boolean(voiceError);

  return (
    <form className="chat-input-form" onSubmit={handleSubmit}>
      {showVoiceStatusBar && (
        <div
          className={`voice-status-bar phase-${voiceError ? 'error' : voicePhase}`}
          role="status"
          aria-live="polite"
        >
          <div className="voice-status-left">
            <span
              className={`voice-recording-indicator ${isRecording || voicePhase === 'listening' ? 'recording-pulse' : ''
                } phase-${voicePhase}`}
            >
              {voicePhase === 'speaking' ? (
                <Volume2 size={14} />
              ) : voiceError || voicePhase === 'error' ? (
                <AlertCircle size={14} />
              ) : (
                <Radio size={14} />
              )}
            </span>

            <div className="voice-status-text-group">
              <span className="voice-phase-badge">
                {VOICE_PHASE_LABELS[voiceError ? 'error' : voicePhase]}
              </span>
              {voiceError ? (
                <span className="voice-error-text">{voiceError}</span>
              ) : lastTranscript && voicePhase !== 'listening' ? (
                <span className="voice-transcript-preview">
                  Heard: &ldquo;{lastTranscript}&rdquo;
                </span>
              ) : null}
            </div>
          </div>

          <div className="voice-status-right">
            <span className="voice-shortcut-pill" title="Global Windows Toggle Shortcut">
              {voiceShortcut}
            </span>
            {isVoiceActive && onCancelVoice && (
              <button
                type="button"
                className="voice-cancel-button"
                onClick={onCancelVoice}
                title="Cancel active voice interaction and release microphone"
                aria-label="Cancel voice mode"
              >
                <Square size={12} />
                <span>Stop Voice</span>
              </button>
            )}
          </div>
        </div>
      )}

      <div className={`chat-input-wrapper ${isVoiceActive ? 'voice-active-border' : ''}`}>
        <textarea
          ref={textareaRef}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={
            isVoiceActive
              ? `Voice Mode Active (${VOICE_PHASE_LABELS[voicePhase]}) — or type a command...`
              : placeholder
          }
          disabled={disabled || isLoading}
          rows={1}
          className="chat-textarea"
        />

        {onToggleVoice && (
          <button
            type="button"
            onClick={onToggleVoice}
            disabled={disabled && !isVoiceActive}
            className={`chat-mic-button ${isVoiceActive ? 'active' : ''} ${isRecording || voicePhase === 'listening' ? 'recording' : ''
              }`}
            aria-label={isVoiceActive ? 'Stop Voice Mode' : 'Start Voice Mode'}
            title={
              isVoiceActive
                ? `Stop Voice Mode (${voiceShortcut})`
                : `Start Voice Mode (${voiceShortcut})`
            }
          >
            {isVoiceActive ? <MicOff size={16} /> : <Mic size={16} />}
          </button>
        )}

        <button
          type="submit"
          disabled={disabled || isLoading || !text.trim()}
          className="chat-send-button"
          aria-label="Send message"
          title="Send command (Enter)"
        >
          {isLoading ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Send size={16} />
          )}
        </button>
      </div>
      <div className="chat-input-hints">
        <span>
          Press <b>Enter</b> to send, <b>Shift + Enter</b> for new line
          {voiceShortcut ? (
            <>
              {' '}
              &bull; Global Voice Toggle: <b>{voiceShortcut}</b>
            </>
          ) : null}
        </span>
      </div>
    </form>
  );
};

export default ChatInput;
