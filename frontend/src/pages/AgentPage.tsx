import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  Bot,
  Plus,
  RefreshCw,
  AlertTriangle,
  Mic,
  MicOff,
} from 'lucide-react';
import apiClient from '../api/client';
import {
  ChatMessage as ChatMessageType,
  ConversationInfo,
  ExecutionStatusResponse,
  ScreenCaptureResponse,
  VoiceStatusResponse,
  VoicePhase,
  EmailDraftItem,
  ActiveProviderResponse,
} from '../api/types';
import ChatMessage from '../components/agent/ChatMessage';
import ChatInput from '../components/agent/ChatInput';
import ConversationList from '../components/agent/ConversationList';
import ExecutionProgress from '../components/agent/ExecutionProgress';
import EmailDraftPanel from '../components/agent/EmailDraftPanel';
import EmptyState from '../components/common/EmptyState';

interface AgentPageProps {
  onNavigateToConfirmations: () => void;
  isBackendConnected: boolean;
}

export const AgentPage: React.FC<AgentPageProps> = ({
  onNavigateToConfirmations,
  isBackendConnected,
}) => {
  const [conversations, setConversations] = useState<ConversationInfo[]>([]);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [activeEmailDraft, setActiveEmailDraft] = useState<EmailDraftItem | null>(null);
  const [conversationMessages, setConversationMessages] = useState<Record<string, ChatMessageType[]>>({});
  const [activeExecution, setActiveExecution] = useState<ExecutionStatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isSessionsLoading, setIsSessionsLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Ephemeral On-Demand Screen Awareness State
  const [capturedScreen, setCapturedScreen] = useState<ScreenCaptureResponse | null>(null);
  const [isCapturingScreen, setIsCapturingScreen] = useState<boolean>(false);
  const [showCaptureConfirm, setShowCaptureConfirm] = useState<boolean>(false);
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [isAnalyzingScreen, setIsAnalyzingScreen] = useState<boolean>(false);

  // Voice Mode State
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatusResponse | null>(null);
  const [activeProvider, setActiveProvider] = useState<ActiveProviderResponse | null>(null);
  const lastEventIdRef = useRef<number>(0);
  const currentConvRef = useRef<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    currentConvRef.current = currentConversationId;
  }, [currentConversationId]);

  useEffect(() => {
    if (!isBackendConnected) return;
    let cancelled = false;
    apiClient.getActiveProvider().then((res) => {
      if (!cancelled && res.ok && res.data) {
        setActiveProvider(res.data);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [isBackendConnected]);

  const activeMessages = useMemo(() => {
    return currentConversationId
      ? conversationMessages[currentConversationId] || []
      : conversationMessages['__new__'] || [];
  }, [currentConversationId, conversationMessages]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [activeMessages, isLoading, voiceStatus?.phase]);

  // Load conversation sessions
  const loadConversations = useCallback(async () => {
    if (!isBackendConnected) return;
    setIsSessionsLoading(true);
    try {
      const res = await apiClient.getConversations(20, 0);
      if (res.ok && res.data) {
        setConversations(res.data);
      }
    } catch (err: unknown) {
      console.error('Failed to load conversations:', err);
    } finally {
      setIsSessionsLoading(false);
    }
  }, [isBackendConnected]);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  // Synchronize Voice Mode state and incoming transcription/response events
  const pollVoiceStatus = useCallback(async () => {
    if (!isBackendConnected) return;
    try {
      const res = await apiClient.getVoiceStatus(lastEventIdRef.current);
      if (res.ok && res.data) {
        const statusData = res.data;
        setVoiceStatus(statusData);

        if (statusData.events && statusData.events.length > 0) {
          let shouldReloadSessions = false;

          statusData.events.forEach((ev) => {
            if (ev.event_id <= lastEventIdRef.current) return;
            lastEventIdRef.current = ev.event_id;

            if (ev.type === 'transcription' && ev.text) {
              const targetKey = ev.conversation_id || currentConvRef.current || '__new__';
              if (ev.conversation_id && !currentConvRef.current) {
                currentConvRef.current = ev.conversation_id;
                setCurrentConversationId(ev.conversation_id);
              }
              const userVoiceMsg: ChatMessageType = {
                id: `voice-user-${ev.event_id}`,
                sender: 'user',
                text: ev.text,
                timestamp: new Date(ev.timestamp * 1000),
                isVoice: true,
              };
              setConversationMessages((prev) => ({
                ...prev,
                [targetKey]: [...(prev[targetKey] || []), userVoiceMsg],
              }));
            } else if (ev.type === 'agent_response') {
              const exec = ev.execution || null;
              if (exec) {
                setActiveExecution(exec);
              }
              const newConvId = exec?.conversation_id || ev.conversation_id || currentConvRef.current;
              const prevKey = currentConvRef.current || '__new__';
              const targetKey = newConvId || prevKey;

              if (newConvId && newConvId !== currentConvRef.current) {
                currentConvRef.current = newConvId;
                setCurrentConversationId(newConvId);
              }

              const agentVoiceMsg: ChatMessageType = {
                id: `voice-agent-${ev.event_id}`,
                sender: 'agent',
                text:
                  ev.response ||
                  exec?.response ||
                  (exec?.execution_status === 'confirmation_required'
                    ? 'A consequential action requires your approval before continuing.'
                    : exec?.error
                      ? `Execution failed: ${exec.error}`
                      : 'Voice command processed.'),
                timestamp: new Date(ev.timestamp * 1000),
                executionId: exec?.execution_id,
                status: exec?.execution_status,
                plan: exec?.plan,
                completedSteps: exec?.completed_steps,
                error: exec?.error,
                pendingConfirmation: exec?.pending_confirmation,
                isVoice: true,
              };

              setConversationMessages((prev) => {
                const existing = prev[targetKey] || prev[prevKey] || [];
                if (newConvId && prevKey === '__new__' && prev['__new__']) {
                  const updated = { ...prev };
                  delete updated['__new__'];
                  updated[newConvId] = [...existing, agentVoiceMsg];
                  return updated;
                }
                return {
                  ...prev,
                  [targetKey]: [...existing, agentVoiceMsg],
                };
              });

              shouldReloadSessions = true;
            }
          });

          if (shouldReloadSessions) {
            loadConversations();
          }
        }
      }
    } catch {
      // Ignore transient polling error
    }
  }, [isBackendConnected, loadConversations]);

  useEffect(() => {
    pollVoiceStatus();
    const intervalMs = voiceStatus?.is_active ? 1000 : 2500;
    const timer = setInterval(pollVoiceStatus, intervalMs);
    return () => clearInterval(timer);
  }, [pollVoiceStatus, voiceStatus?.is_active]);

  const handleToggleVoiceMode = async () => {
    setErrorMsg(null);
    const res = await apiClient.toggleVoiceMode(currentConversationId || undefined);
    if (res.ok && res.data) {
      setVoiceStatus(res.data);
      if (res.data.latest_event_id > lastEventIdRef.current) {
        await pollVoiceStatus();
      }
    } else if (res.error) {
      setErrorMsg(res.error);
    }
  };

  const handleCancelVoiceMode = async () => {
    const res = await apiClient.stopVoiceMode('Cancelled by user from Agent chat');
    if (res.ok && res.data) {
      setVoiceStatus(res.data);
    }
  };

  const handleStartPtt = async () => {
    setErrorMsg(null);
    const res = await apiClient.startPushToTalk(currentConversationId || undefined);
    if (res.ok && res.data) {
      setVoiceStatus(res.data);
    } else if (res.error) {
      setErrorMsg(res.error);
    }
  };

  const handleStopPtt = async () => {
    const res = await apiClient.stopPushToTalk();
    if (res.ok && res.data) {
      setVoiceStatus(res.data);
      if (res.data.latest_event_id > lastEventIdRef.current) {
        await pollVoiceStatus();
      }
    } else if (res.error) {
      setErrorMsg(res.error);
    }
  };

  const handleInterruptVoice = async () => {
    const res = await apiClient.interruptVoice('Interrupted by user from Agent chat');
    if (res.ok && res.data) {
      setVoiceStatus(res.data);
    }
  };

  const handleToggleWakeWord = async () => {
    setErrorMsg(null);
    const currentEnabled = Boolean(
      voiceStatus?.wake_word_enabled ?? voiceStatus?.settings?.wake_word_enabled
    );
    const res = await apiClient.updateVoiceConfig({ wake_word_enabled: !currentEnabled });
    if (res.ok && res.data) {
      setVoiceStatus(res.data);
    } else if (res.error) {
      setErrorMsg(res.error);
    }
  };

  // When switching conversations, load previous executions for that session if not already in memory
  const handleSelectConversation = async (conversationId: string) => {
    setCurrentConversationId(conversationId);
    setErrorMsg(null);
    setActiveExecution(null);

    if (conversationMessages[conversationId] && conversationMessages[conversationId].length > 0) {
      return;
    }

    try {
      const res = await apiClient.getConversationExecutions(conversationId);
      if (res.ok && res.data && res.data.length > 0) {
        const loadedMessages: ChatMessageType[] = [];
        res.data.forEach((exec) => {
          loadedMessages.push({
            id: `${exec.execution_id}-user`,
            sender: 'user',
            text: exec.user_input,
            timestamp: new Date(),
            executionId: exec.execution_id,
          });
          loadedMessages.push({
            id: `${exec.execution_id}-agent`,
            sender: 'agent',
            text: exec.response || (exec.error ? `Error: ${exec.error}` : 'Execution completed.'),
            timestamp: new Date(),
            executionId: exec.execution_id,
            status: exec.execution_status,
            plan: exec.plan,
            completedSteps: exec.completed_steps,
            error: exec.error,
            pendingConfirmation: exec.pending_confirmation,
          });
        });

        setConversationMessages((prev) => ({
          ...prev,
          [conversationId]: loadedMessages,
        }));
      }
    } catch (err: unknown) {
      console.error('Failed to load conversation executions:', err);
    }
  };

  const handleRequestScreenCapture = () => {
    setCaptureError(null);
    setShowCaptureConfirm((prev) => !prev);
  };

  const handleCancelScreenCapture = () => {
    setShowCaptureConfirm(false);
    setCaptureError(null);
  };

  const handleConfirmScreenCapture = async () => {
    if (isCapturingScreen) return;
    setIsCapturingScreen(true);
    setCaptureError(null);

    try {
      const res = await apiClient.captureScreen(true);
      if (res.ok && res.data) {
        setCapturedScreen(res.data);
        setShowCaptureConfirm(false);
      } else {
        setCaptureError(res.error || 'Failed to capture primary display.');
      }
    } catch (err: unknown) {
      setCaptureError(err instanceof Error ? err.message : 'Screen capture failed.');
    } finally {
      setIsCapturingScreen(false);
    }
  };

  const handleRemoveScreenCapture = async () => {
    const existing = capturedScreen;
    setCapturedScreen(null);
    setCaptureError(null);
    if (existing?.capture_id) {
      await apiClient.deleteScreenCapture(existing.capture_id, existing.profile_id).catch(() => { });
    }
  };

  const handleNewConversation = () => {
    setCurrentConversationId(null);
    setActiveExecution(null);
    setErrorMsg(null);
    setShowCaptureConfirm(false);
  };

  // Handle sending a new text message
  const handleSendMessage = async (userInput: string) => {
    if (!userInput.trim() || isLoading) return;

    setErrorMsg(null);
    setCaptureError(null);
    setShowCaptureConfirm(false);
    const sessionKey = currentConversationId || '__new__';
    const attachedCapture = capturedScreen;

    const userMsg: ChatMessageType = {
      id: `user-${Date.now()}`,
      sender: 'user',
      text: attachedCapture ? `[Screen Attached] ${userInput}` : userInput,
      timestamp: new Date(),
    };

    setConversationMessages((prev) => ({
      ...prev,
      [sessionKey]: [...(prev[sessionKey] || []), userMsg],
    }));

    setIsLoading(true);
    setIsAnalyzingScreen(Boolean(attachedCapture));
    if (attachedCapture) {
      setCapturedScreen(null);
    }

    try {
      const res = await apiClient.execute(
        userInput,
        currentConversationId || undefined,
        undefined,
        attachedCapture?.capture_id
      );

      if (res.ok && res.data) {
        const exec = res.data;
        setActiveExecution(exec);
        const maybeDraft = (exec.execution_results?.[0] as { draft?: EmailDraftItem } | undefined)?.draft;
        if (maybeDraft) {
          setActiveEmailDraft(maybeDraft);
        }

        const newConvId = exec.conversation_id || currentConversationId;
        if (newConvId && newConvId !== currentConversationId) {
          setCurrentConversationId(newConvId);
        }

        const agentMsg: ChatMessageType = {
          id: `agent-${exec.execution_id}`,
          sender: 'agent',
          text:
            exec.response ||
            (exec.execution_status === 'confirmation_required'
              ? 'A consequential action requires your approval before continuing.'
              : exec.execution_status === 'cancelled'
                ? 'Execution was cancelled by emergency stop.'
                : exec.execution_status === 'rate_limited'
                  ? 'Request halted: Execution budget or rate limit reached.'
                  : exec.error
                    ? `Execution failed: ${exec.error}`
                    : 'Execution completed successfully.'),
          timestamp: new Date(),
          executionId: exec.execution_id,
          status: exec.execution_status,
          plan: exec.plan,
          completedSteps: exec.completed_steps,
          error: exec.error,
          pendingConfirmation: exec.pending_confirmation,
        };

        const targetKey = newConvId || sessionKey;

        setConversationMessages((prev) => {
          const existing = prev[sessionKey] || [];
          if (newConvId && sessionKey === '__new__') {
            const updated = { ...prev };
            delete updated['__new__'];
            updated[newConvId] = [...existing, agentMsg];
            return updated;
          }
          return {
            ...prev,
            [targetKey]: [...existing, agentMsg],
          };
        });

        loadConversations();
      } else {
        const errorText = res.error || 'Failed to execute command.';
        setErrorMsg(errorText);

        const errorMsgObj: ChatMessageType = {
          id: `error-${Date.now()}`,
          sender: 'agent',
          text: `Command error: ${errorText}`,
          timestamp: new Date(),
          status: 'failed',
          error: errorText,
        };

        setConversationMessages((prev) => ({
          ...prev,
          [sessionKey]: [...(prev[sessionKey] || []), errorMsgObj],
        }));
      }
    } catch (err: unknown) {
      const errorText = err instanceof Error ? err.message : 'Unknown execution failure.';
      setErrorMsg(errorText);

      const errorMsgObj: ChatMessageType = {
        id: `error-${Date.now()}`,
        sender: 'agent',
        text: `Execution error: ${errorText}`,
        timestamp: new Date(),
        status: 'failed',
        error: errorText,
      };

      setConversationMessages((prev) => ({
        ...prev,
        [sessionKey]: [...(prev[sessionKey] || []), errorMsgObj],
      }));
    } finally {
      setIsLoading(false);
      setIsAnalyzingScreen(false);
    }
  };

  const isVoiceActive = voiceStatus?.is_active ?? false;
  const voicePhase: VoicePhase = voiceStatus?.phase ?? 'idle';
  const isRecording = voiceStatus?.is_recording ?? false;
  const voiceShortcut = voiceStatus?.shortcut_status?.shortcut || voiceStatus?.settings?.shortcut || 'Ctrl+Shift+V';
  const voiceError = voiceStatus?.last_error || null;

  return (
    <div className="agent-page-layout">
      {/* Sessions Sidebar Panel */}
      <ConversationList
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
        isLoading={isSessionsLoading}
      />

      {/* Main Chat & Progression View */}
      <div className="chat-main-area">
        {/* Chat Header Toolbar */}
        <div className="chat-header-bar">
          <div className="chat-header-info">
            <Bot size={18} className="text-accent-blue" />
            <div>
              <h2 className="chat-session-title">
                {currentConversationId
                  ? `Session ${currentConversationId.slice(0, 8)}...`
                  : 'New Conversation Session'}
              </h2>
              <span className="chat-session-subtitle">
                {activeProvider?.profile_config?.active_provider_id === 'byok' ? (
                  <>
                    BYOK (OpenAI • {activeProvider.profile_config.byok_model || 'gpt-4o-mini'}) •{' '}
                    <span className="text-amber-600 dark:text-amber-400 font-medium">
                      External AI
                    </span>
                    {!activeProvider.health?.available && (
                      <span className="text-rose-600 dark:text-rose-400">
                        {' '}• {activeProvider.health?.reason || 'Unavailable'}
                      </span>
                    )}
                    {!activeProvider.capabilities?.vision && ' • Vision unsupported'}
                  </>
                ) : (
                  <>
                    Local AI ({activeProvider?.profile_config?.local_model || 'qwen3:4b'}) • Voice Shortcut:{' '}
                    {voiceShortcut}
                  </>
                )}
              </span>
            </div>
          </div>

          <div className="chat-header-actions">
            <button
              className={`refresh-btn ${isVoiceActive ? 'voice-header-btn-active' : ''}`}
              onClick={handleToggleVoiceMode}
              disabled={!isBackendConnected && !isVoiceActive}
              title={
                isVoiceActive
                  ? `Stop Voice Mode (${voiceShortcut})`
                  : `Start Voice Mode (${voiceShortcut})`
              }
              type="button"
            >
              {isVoiceActive ? <MicOff size={14} /> : <Mic size={14} />}
              <span>{isVoiceActive ? `Voice Active (${voicePhase})` : 'Voice Mode'}</span>
            </button>
            <button
              className="refresh-btn"
              onClick={handleNewConversation}
              title="Start a new isolated conversation"
              type="button"
            >
              <Plus size={14} />
              <span>New Session</span>
            </button>
            <button
              className="refresh-btn"
              onClick={loadConversations}
              disabled={isSessionsLoading}
              title="Reload conversation history"
              type="button"
            >
              <RefreshCw size={14} className={isSessionsLoading ? 'animate-spin' : ''} />
            </button>
          </div>
        </div>

        {/* In-Flight Execution Progress Tracker */}
        {activeExecution && (
          <div className="chat-progress-banner">
            <ExecutionProgress execution={activeExecution} showDetails={true} />
          </div>
        )}

        {/* Messages Stream Container */}
        <div className="messages-stream-container">
          {!isBackendConnected && (
            <div className="chat-offline-banner">
              <AlertTriangle size={15} />
              <span>Backend disconnected. Ensure FastAPI is running on http://127.0.0.1:8000.</span>
            </div>
          )}

          {activeMessages.length === 0 ? (
            <div className="agent-empty-workspace">
              <EmptyState
                icon={<Bot size={32} />}
                title="VoxPilot Desktop Agent Ready"
                description={`Type a command, click the microphone button, or press ${voiceShortcut} anywhere in Windows to speak to VoxPilot.`}
              />
              <div className="agent-quick-prompts" aria-label="Suggested commands">
                {[
                  'Check system hardware status',
                  'List running applications',
                  'Open Notepad and type a summary',
                ].map((prompt) => (
                  <button
                    key={prompt}
                    type="button"
                    className="agent-quick-prompt-chip"
                    disabled={!isBackendConnected || isLoading}
                    onClick={() => handleSendMessage(prompt)}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            activeMessages.map((msg) => (
              <ChatMessage
                key={msg.id}
                message={msg}
                onNavigateToConfirmations={onNavigateToConfirmations}
              />
            ))
          )}

          {(isLoading || voicePhase === 'processing') && (
            <div className="chat-thinking-indicator">
              <div className="pulse-dots">
                <span />
                <span />
                <span />
              </div>
              <span className="thinking-text">
                {isAnalyzingScreen
                  ? 'Analyzing captured screen context locally...'
                  : 'VoxPilot is recognizing intent, planning, and executing tools...'}
              </span>
            </div>
          )}

          {errorMsg && (
            <div className="chat-error-banner">
              <AlertTriangle size={15} />
              <span>{errorMsg}</span>
            </div>
          )}

          {activeEmailDraft && (
            <EmailDraftPanel
              draft={activeEmailDraft}
              profileId={activeEmailDraft.profile_id}
              onDraftUpdated={(updated) => setActiveEmailDraft(updated)}
              onSent={() => setActiveEmailDraft(null)}
            />
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Chat Input Bar */}
        <div className="chat-input-container">
          <ChatInput
            onSendMessage={handleSendMessage}
            isLoading={isLoading}
            disabled={!isBackendConnected}
            isVoiceActive={isVoiceActive}
            voicePhase={voicePhase}
            isRecording={isRecording}
            isPttActive={Boolean(voiceStatus?.is_ptt_active)}
            wakeWordEnabled={Boolean(
              voiceStatus?.wake_word_enabled ?? voiceStatus?.settings?.wake_word_enabled
            )}
            voiceShortcut={voiceShortcut}
            voiceError={voiceError}
            lastTranscript={voiceStatus?.last_transcript || null}
            onToggleVoice={handleToggleVoiceMode}
            onCancelVoice={handleCancelVoiceMode}
            onStartPtt={handleStartPtt}
            onStopPtt={handleStopPtt}
            onInterruptVoice={handleInterruptVoice}
            onToggleWakeWord={handleToggleWakeWord}
            capturedScreen={capturedScreen}
            isCapturingScreen={isCapturingScreen}
            showCaptureConfirm={showCaptureConfirm}
            captureError={captureError}
            onRequestScreenCapture={handleRequestScreenCapture}
            onConfirmScreenCapture={handleConfirmScreenCapture}
            onCancelScreenCapture={handleCancelScreenCapture}
            onRemoveScreenCapture={handleRemoveScreenCapture}
          />
        </div>
      </div>
    </div>
  );
};

export default AgentPage;
