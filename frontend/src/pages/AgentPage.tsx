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
  VoiceStatusResponse,
  VoicePhase,
} from '../api/types';
import ChatMessage from '../components/agent/ChatMessage';
import ChatInput from '../components/agent/ChatInput';
import ConversationList from '../components/agent/ConversationList';
import ExecutionProgress from '../components/agent/ExecutionProgress';
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
  const [conversationMessages, setConversationMessages] = useState<Record<string, ChatMessageType[]>>({});
  const [activeExecution, setActiveExecution] = useState<ExecutionStatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isSessionsLoading, setIsSessionsLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Voice Mode State
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatusResponse | null>(null);
  const lastEventIdRef = useRef<number>(0);
  const currentConvRef = useRef<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    currentConvRef.current = currentConversationId;
  }, [currentConversationId]);

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

  const handleNewConversation = () => {
    setCurrentConversationId(null);
    setActiveExecution(null);
    setErrorMsg(null);
  };

  // Handle sending a new text message
  const handleSendMessage = async (userInput: string) => {
    if (!userInput.trim() || isLoading) return;

    setErrorMsg(null);
    const sessionKey = currentConversationId || '__new__';

    const userMsg: ChatMessageType = {
      id: `user-${Date.now()}`,
      sender: 'user',
      text: userInput,
      timestamp: new Date(),
    };

    setConversationMessages((prev) => ({
      ...prev,
      [sessionKey]: [...(prev[sessionKey] || []), userMsg],
    }));

    setIsLoading(true);

    try {
      const res = await apiClient.execute(
        userInput,
        currentConversationId || undefined
      );

      if (res.ok && res.data) {
        const exec = res.data;
        setActiveExecution(exec);

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
                LangGraph Agent (Qwen 3B/4B) • Voice Shortcut: {voiceShortcut}
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
            <ExecutionProgress execution={activeExecution} showDetails={false} />
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
            <EmptyState
              icon={<Bot size={32} />}
              title="VoxPilot Desktop Agent Ready"
              description={`Type a command, click the microphone button, or press ${voiceShortcut} anywhere in Windows to speak to VoxPilot.`}
            />
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
                VoxPilot is recognizing intent, planning, and executing tools...
              </span>
            </div>
          )}

          {errorMsg && (
            <div className="chat-error-banner">
              <AlertTriangle size={15} />
              <span>{errorMsg}</span>
            </div>
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
            voiceShortcut={voiceShortcut}
            voiceError={voiceError}
            lastTranscript={voiceStatus?.last_transcript || null}
            onToggleVoice={handleToggleVoiceMode}
            onCancelVoice={handleCancelVoiceMode}
          />
        </div>
      </div>
    </div>
  );
};

export default AgentPage;
