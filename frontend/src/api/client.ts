/**
 * VoxPilot Frontend - Centralized API Client
 * Connects to the local FastAPI backend with timeout, error handling, and typed responses.
 */

import {
  ApiResponse,
  HealthResponse,
  RootResponse,
  EmergencyStopStatus,
  EmergencyStopTriggerResponse,
  EmergencyStopResetResponse,
  ConversationInfo,
  ConversationDetailResponse,
  ExecutionStatusResponse,
  PendingConfirmationInfo,
  ToolInfo,
  AuditExportRequest,
  AuditExportResponse,
  VoiceStatusResponse,
  VoiceSettings,
  VoiceShortcutStatus,
  MicrophoneListResponse,
  MicrophoneTestResponse,
  PiperVoiceModel,
  AppVersionResponse,
  UpdateStatusResponse,
  UpdateSettings,
  UpdateChannel,
  ApplicationsListResponse,
  DirectLaunchResponse,
  SystemHardwareResponse,
  SystemModelsResponse,
  RunningModelsResponse,
  ModelOperationResponse,
  MemoryItem,
  MemoryListResponse,
  MemorySearchResponse,
  MemoryCreateRequest,
  MemoryUpdateRequest,
  MemoryDeleteResponse,
  PreferenceItem,
  PreferenceListResponse,
  PreferenceCreateRequest,
  PreferenceUpdateRequest,
  PreferenceDeleteResponse,
  ProfileItem,
  ProfileListResponse,
  ProfileCreateRequest,
  ProfileUpdateRequest,
  ProfileDeleteResponse,
  VisionAvailabilityStatus,
  ScreenCaptureResponse,
  EmailAccountStatusResponse,
  EmailDraftItem,
  EmailDraftSendConfirmationResponse,
  SystemStartupStatus,
  NotificationPreferences,
  NotificationStatusResponse,
  NotificationTestResponse,
  AutomationTriggerType,
  AutomationStep,
  AutomationTriggerConfig,
  CustomAutomation,
  AutomationPreviewResponse,
  AutomationExecutionStatus,
  AutomationExecutionHistoryListResponse,
  AIProviderId,
  AIProviderHealth,
  ProfileProviderConfig,
  ProvidersListResponse,
  ActiveProviderResponse,
  OpenAIBYOKConfigureResponse,
  OpenAIBYOKValidateResponse,
} from './types';

class ApiClient {
  private baseUrl: string;
  private defaultTimeoutMs: number;

  constructor() {
    const rawUrl = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';
    this.baseUrl = rawUrl.replace(/\/+$/, '');
    this.defaultTimeoutMs = 120000; // 120 seconds to match max agent execution budget
  }

  public getBaseUrl(): string {
    return this.baseUrl;
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {},
    timeoutMs?: number
  ): Promise<ApiResponse<T>> {
    const url = `${this.baseUrl}${endpoint.startsWith('/') ? endpoint : `/${endpoint}`}`;
    const controller = new AbortController();
    const timeout = setTimeout(() => {
      controller.abort();
    }, timeoutMs || this.defaultTimeoutMs);

    try {
      const response = await fetch(url, {
        ...options,
        signal: controller.signal,
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          ...options.headers,
        },
      });

      clearTimeout(timeout);

      let data: T | null = null;
      let errorMsg: string | null = null;

      const contentType = response.headers.get('content-type');
      if (contentType && contentType.includes('application/json')) {
        try {
          data = await response.json();
        } catch {
          errorMsg = 'Failed to parse JSON response from backend.';
        }
      } else {
        const text = await response.text();
        if (!response.ok) {
          errorMsg = text || `HTTP error ${response.status}: ${response.statusText}`;
        }
      }

      if (!response.ok) {
        if (!errorMsg) {
          errorMsg = (data as Record<string, unknown>)?.detail
            ? String((data as Record<string, unknown>).detail)
            : `HTTP ${response.status}: ${response.statusText}`;
        }
        return {
          data: null,
          error: errorMsg,
          status: response.status,
          ok: false,
        };
      }

      return {
        data,
        error: null,
        status: response.status,
        ok: true,
      };
    } catch (err: unknown) {
      clearTimeout(timeout);

      let errorMessage = 'Network error: could not connect to VoxPilot backend.';
      if (err instanceof Error) {
        if (err.name === 'AbortError') {
          errorMessage = `Connection timed out after ${timeoutMs || this.defaultTimeoutMs}ms.`;
        } else {
          errorMessage = err.message;
        }
      }

      return {
        data: null,
        error: errorMessage,
        status: 0,
        ok: false,
      };
    }
  }

  /**
   * Health check endpoint: GET /health
   */
  public async getHealth(): Promise<ApiResponse<HealthResponse>> {
    return this.request<HealthResponse>('/health', {}, 10000);
  }

  /**
   * Root service metadata endpoint: GET /
   */
  public async getRoot(): Promise<ApiResponse<RootResponse>> {
    return this.request<RootResponse>('/', {}, 10000);
  }

  /**
   * Emergency stop latch & operation status: GET /agent/emergency-stop/status
   */
  public async getEmergencyStopStatus(): Promise<ApiResponse<EmergencyStopStatus>> {
    return this.request<EmergencyStopStatus>('/agent/emergency-stop/status', {}, 10000);
  }

  /**
   * Trigger emergency stop: POST /agent/emergency-stop
   */
  public async triggerEmergencyStop(reason?: string): Promise<ApiResponse<EmergencyStopTriggerResponse>> {
    return this.request<EmergencyStopTriggerResponse>(
      '/agent/emergency-stop',
      {
        method: 'POST',
        body: JSON.stringify({ reason: reason || 'Emergency stop triggered via frontend dashboard.' }),
      },
      5000
    );
  }

  /**
   * Safe reset emergency stop: POST /agent/emergency-stop/reset
   */
  public async resetEmergencyStop(): Promise<ApiResponse<EmergencyStopResetResponse>> {
    return this.request<EmergencyStopResetResponse>(
      '/agent/emergency-stop/reset',
      {
        method: 'POST',
        body: JSON.stringify({ force: false }),
      },
      5000
    );
  }

  /**
   * Execute an agent natural language request: POST /agent/execute
   */
  public async execute(
    userInput: string,
    conversationId?: string,
    executionId?: string,
    captureId?: string
  ): Promise<ApiResponse<ExecutionStatusResponse>> {
    return this.request<ExecutionStatusResponse>(
      '/agent/execute',
      {
        method: 'POST',
        body: JSON.stringify({
          user_input: userInput,
          conversation_id: conversationId || undefined,
          execution_id: executionId || undefined,
          capture_id: captureId || undefined,
        }),
      },
      125000
    );
  }

  /**
   * Query status of an execution: GET /agent/status/{execution_id}
   */
  public async getExecutionStatus(executionId: string): Promise<ApiResponse<ExecutionStatusResponse>> {
    return this.request<ExecutionStatusResponse>(
      `/agent/status/${encodeURIComponent(executionId)}`,
      {},
      8000
    );
  }

  /**
   * List recent persistent conversations: GET /agent/conversations
   */
  public async getConversations(
    limit: number = 20,
    offset: number = 0
  ): Promise<ApiResponse<ConversationInfo[]>> {
    return this.request<ConversationInfo[]>(
      `/agent/conversations?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
      {},
      8000
    );
  }

  /**
   * Retrieve conversation details: GET /agent/conversations/{conversation_id}
   */
  public async getConversationDetails(
    conversationId: string
  ): Promise<ApiResponse<ConversationDetailResponse>> {
    return this.request<ConversationDetailResponse>(
      `/agent/conversations/${encodeURIComponent(conversationId)}`,
      {},
      8000
    );
  }

  /**
   * Retrieve all executions for a conversation: GET /agent/conversations/{conversation_id}/executions
   */
  public async getConversationExecutions(
    conversationId: string
  ): Promise<ApiResponse<ExecutionStatusResponse[]>> {
    return this.request<ExecutionStatusResponse[]>(
      `/agent/conversations/${encodeURIComponent(conversationId)}/executions`,
      {},
      10000
    );
  }

  /**
   * Retrieve pending confirmation for execution: GET /agent/confirmations/{execution_id}
   */
  public async getPendingConfirmation(
    executionId: string
  ): Promise<ApiResponse<PendingConfirmationInfo>> {
    return this.request<PendingConfirmationInfo>(
      `/agent/confirmations/${encodeURIComponent(executionId)}`,
      {},
      5000
    );
  }

  /**
   * Approve pending confirmation: POST /agent/confirmations/{execution_id}/approve
   */
  public async approveConfirmation(
    executionId: string,
    token: string
  ): Promise<ApiResponse<ExecutionStatusResponse>> {
    return this.request<ExecutionStatusResponse>(
      `/agent/confirmations/${encodeURIComponent(executionId)}/approve`,
      {
        method: 'POST',
        body: JSON.stringify({ token }),
      },
      125000
    );
  }

  /**
   * Reject pending confirmation: POST /agent/confirmations/{execution_id}/reject
   */
  public async rejectConfirmation(
    executionId: string,
    token: string,
    reason?: string
  ): Promise<ApiResponse<ExecutionStatusResponse>> {
    return this.request<ExecutionStatusResponse>(
      `/agent/confirmations/${encodeURIComponent(executionId)}/reject`,
      {
        method: 'POST',
        body: JSON.stringify({ token, reason: reason || 'User rejected the action.' }),
      },
      15000
    );
  }

  /**
   * List available registered agent tools: GET /agent/tools
   */
  public async getTools(): Promise<ApiResponse<ToolInfo[]>> {
    return this.request<ToolInfo[]>('/agent/tools', {}, 8000);
  }

  /**
   * Export scrubbed audit trail: POST /agent/audit/export
   */
  public async exportAuditTrail(
    request: AuditExportRequest
  ): Promise<ApiResponse<AuditExportResponse>> {
    return this.request<AuditExportResponse>(
      '/agent/audit/export',
      {
        method: 'POST',
        body: JSON.stringify(request),
      },
      30000
    );
  }

  /**
   * Retrieve metadata for an export: GET /agent/audit/exports/{export_id}/status
   */
  public async getAuditExportStatus(
    exportId: string
  ): Promise<ApiResponse<AuditExportResponse>> {
    return this.request<AuditExportResponse>(
      `/agent/audit/exports/${encodeURIComponent(exportId)}/status`,
      {},
      8000
    );
  }

  /**
   * List all generated audit exports: GET /agent/audit/exports
   */
  public async listAuditExports(): Promise<ApiResponse<AuditExportResponse[]>> {
    return this.request<AuditExportResponse[]>(
      '/agent/audit/exports',
      {},
      8000
    );
  }

  /**
   * Safe file download for an audit export.
   * Streams the file blob, verifies content-type, triggers browser file save,
   * cleans up temporary object URLs, and never auto-executes files.
   */
  public async downloadAuditExport(
    exportId: string,
    filename: string
  ): Promise<{ ok: boolean; error?: string }> {
    if (!exportId || exportId.includes('/') || exportId.includes('\\') || exportId.includes('..')) {
      return { ok: false, error: 'Invalid export ID format: path traversal forbidden.' };
    }

    const cleanFilename = (filename || `${exportId}.json`).replace(/[/\\?%*:|"<>]/g, '_');
    const url = `${this.baseUrl}/agent/audit/exports/${encodeURIComponent(exportId)}/download`;

    try {
      const response = await fetch(url, {
        method: 'GET',
        headers: {
          Accept: 'application/json, text/csv, */*',
        },
      });

      if (!response.ok) {
        let errDetail = `HTTP ${response.status}: ${response.statusText}`;
        try {
          const errJson = await response.json();
          if (errJson?.detail) errDetail = String(errJson.detail);
        } catch {
          // ignore json parse failure on error body
        }
        return { ok: false, error: errDetail };
      }

      const blob = await response.blob();
      const objectUrl = window.URL.createObjectURL(blob);

      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = cleanFilename;
      anchor.style.display = 'none';
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);

      setTimeout(() => {
        window.URL.revokeObjectURL(objectUrl);
      }, 1000);

      return { ok: true };
    } catch (err: unknown) {
      return {
        ok: false,
        error: err instanceof Error ? err.message : 'Network failure during export download.',
      };
    }
  }

  /**
   * Retrieve live voice mode state, component diagnostics, shortcut status, and recent events:
   * GET /agent/voice/status
   */
  public async getVoiceStatus(sinceEventId: number = 0): Promise<ApiResponse<VoiceStatusResponse>> {
    return this.request<VoiceStatusResponse>(
      `/agent/voice/status?since_event_id=${encodeURIComponent(sinceEventId)}`,
      {},
      8000
    );
  }

  /**
   * Start Voice Mode: POST /agent/voice/start
   */
  public async startVoiceMode(conversationId?: string): Promise<ApiResponse<VoiceStatusResponse>> {
    return this.request<VoiceStatusResponse>(
      '/agent/voice/start',
      {
        method: 'POST',
        body: JSON.stringify({ conversation_id: conversationId || undefined }),
      },
      10000
    );
  }

  /**
   * Stop Voice Mode immediately and release microphone: POST /agent/voice/stop
   */
  public async stopVoiceMode(reason?: string): Promise<ApiResponse<VoiceStatusResponse>> {
    return this.request<VoiceStatusResponse>(
      '/agent/voice/stop',
      {
        method: 'POST',
        body: JSON.stringify({ reason: reason || 'Stopped by user' }),
      },
      10000
    );
  }

  /**
   * Toggle Voice Mode on or off: POST /agent/voice/toggle
   */
  public async toggleVoiceMode(conversationId?: string): Promise<ApiResponse<VoiceStatusResponse>> {
    return this.request<VoiceStatusResponse>(
      '/agent/voice/toggle',
      {
        method: 'POST',
        body: JSON.stringify({ conversation_id: conversationId || undefined }),
      },
      10000
    );
  }

  /**
   * Interrupt active speech playback or voice capture: POST /agent/voice/interrupt
   */
  public async interruptVoice(reason?: string): Promise<ApiResponse<VoiceStatusResponse>> {
    return this.request<VoiceStatusResponse>(
      '/agent/voice/interrupt',
      {
        method: 'POST',
        body: JSON.stringify({ reason: reason || 'Interrupted by user' }),
      },
      10000
    );
  }

  /**
   * Start push-to-talk capture: POST /agent/voice/ptt/start
   */
  public async startPushToTalk(conversationId?: string): Promise<ApiResponse<VoiceStatusResponse>> {
    return this.request<VoiceStatusResponse>(
      '/agent/voice/ptt/start',
      {
        method: 'POST',
        body: JSON.stringify({ conversation_id: conversationId || undefined }),
      },
      10000
    );
  }

  /**
   * Stop push-to-talk capture and process speech: POST /agent/voice/ptt/stop
   */
  public async stopPushToTalk(): Promise<ApiResponse<VoiceStatusResponse>> {
    return this.request<VoiceStatusResponse>(
      '/agent/voice/ptt/stop',
      {
        method: 'POST',
        body: JSON.stringify({}),
      },
      15000
    );
  }

  /**
   * Retrieve persistent voice settings & shortcut status: GET /agent/voice/config
   */
  public async getVoiceConfig(): Promise<
    ApiResponse<{ settings: VoiceSettings; shortcut_status: VoiceShortcutStatus }>
  > {
    return this.request<{ settings: VoiceSettings; shortcut_status: VoiceShortcutStatus }>(
      '/agent/voice/config',
      {},
      8000
    );
  }

  /**
   * Update persistent voice settings: POST /agent/voice/config
   */
  public async updateVoiceConfig(
    updates: Partial<VoiceSettings>
  ): Promise<ApiResponse<VoiceStatusResponse>> {
    return this.request<VoiceStatusResponse>(
      '/agent/voice/config',
      {
        method: 'POST',
        body: JSON.stringify(updates),
      },
      10000
    );
  }

  /**
   * Update and register a custom global Voice Mode keyboard shortcut: POST /agent/voice/shortcut
   */
  public async updateVoiceShortcut(
    shortcut: string,
    enabled?: boolean
  ): Promise<ApiResponse<VoiceStatusResponse>> {
    return this.request<VoiceStatusResponse>(
      '/agent/voice/shortcut',
      {
        method: 'POST',
        body: JSON.stringify({ shortcut, enabled }),
      },
      10000
    );
  }

  /**
   * List available microphone input devices: GET /agent/voice/microphones
   */
  public async getMicrophones(): Promise<ApiResponse<MicrophoneListResponse>> {
    return this.request<MicrophoneListResponse>('/agent/voice/microphones', {}, 8000);
  }

  /**
   * Test microphone hardware capture & signal level: POST /agent/voice/microphones/test
   */
  public async testMicrophone(
    device?: number | string | null,
    durationSeconds: number = 0.5
  ): Promise<ApiResponse<MicrophoneTestResponse>> {
    return this.request<MicrophoneTestResponse>(
      '/agent/voice/microphones/test',
      {
        method: 'POST',
        body: JSON.stringify({
          device: device ?? null,
          duration_seconds: durationSeconds,
        }),
      },
      15000
    );
  }

  /**
   * List installed Piper ONNX voice models: GET /agent/voice/voices
   */
  public async getPiperVoices(): Promise<ApiResponse<{ voices: PiperVoiceModel[] }>> {
    return this.request<{ voices: PiperVoiceModel[] }>('/agent/voice/voices', {}, 8000);
  }

  /**
   * Retrieve authoritative application version metadata: GET /api/version
   */
  public async getVersion(): Promise<ApiResponse<AppVersionResponse>> {
    return this.request<AppVersionResponse>('/api/version', {}, 8000);
  }

  /**
   * Retrieve current software update status: GET /api/updates/status
   */
  public async getUpdateStatus(): Promise<ApiResponse<UpdateStatusResponse>> {
    return this.request<UpdateStatusResponse>('/api/updates/status', {}, 8000);
  }

  /**
   * Check GitHub Releases for newer official versions: POST /api/updates/check
   */
  public async checkForUpdates(force: boolean = true): Promise<ApiResponse<UpdateStatusResponse>> {
    return this.request<UpdateStatusResponse>(
      '/api/updates/check',
      {
        method: 'POST',
        body: JSON.stringify({ force }),
      },
      15000
    );
  }

  /**
   * Start downloading and verifying the official installer: POST /api/updates/download
   */
  public async startUpdateDownload(
    background: boolean = true
  ): Promise<ApiResponse<UpdateStatusResponse>> {
    return this.request<UpdateStatusResponse>(
      '/api/updates/download',
      {
        method: 'POST',
        body: JSON.stringify({ background }),
      },
      30000
    );
  }

  /**
   * Cancel an active update installer download: POST /api/updates/cancel
   */
  public async cancelUpdateDownload(): Promise<ApiResponse<UpdateStatusResponse>> {
    return this.request<UpdateStatusResponse>(
      '/api/updates/cancel',
      {
        method: 'POST',
      },
      8000
    );
  }

  /**
   * Dismiss update notification banner ("Remind Me Later"): POST /api/updates/dismiss
   */
  public async dismissUpdateNotification(
    version?: string
  ): Promise<ApiResponse<UpdateStatusResponse>> {
    return this.request<UpdateStatusResponse>(
      '/api/updates/dismiss',
      {
        method: 'POST',
        body: JSON.stringify({ version: version ?? null }),
      },
      8000
    );
  }

  /**
   * Retrieve persistent update settings: GET /api/updates/settings
   */
  public async getUpdateSettings(): Promise<ApiResponse<UpdateSettings>> {
    return this.request<UpdateSettings>('/api/updates/settings', {}, 8000);
  }

  /**
   * Update persistent update settings: POST /api/updates/settings
   */
  public async updateUpdateSettings(updates: {
    auto_check_enabled?: boolean;
    channel?: UpdateChannel;
    release_repo?: string;
  }): Promise<ApiResponse<{ status: string; settings: UpdateSettings }>> {
    return this.request<{ status: string; settings: UpdateSettings }>(
      '/api/updates/settings',
      {
        method: 'POST',
        body: JSON.stringify(updates),
      },
      10000
    );
  }

  /**
   * Launch the external Windows updater after explicit user confirmation: POST /api/updates/install
   */
  public async installUpdate(
    confirm: boolean,
    relaunch: boolean = true,
    shutdownApp: boolean = true
  ): Promise<ApiResponse<UpdateStatusResponse>> {
    return this.request<UpdateStatusResponse>(
      '/api/updates/install',
      {
        method: 'POST',
        body: JSON.stringify({
          confirm,
          relaunch,
          shutdown_app: shutdownApp,
        }),
      },
      15000
    );
  }

  // ===========================================================================
  // Universal Windows Application Discovery & Launching API
  // ===========================================================================

  /**
   * List or search discovered Windows applications: GET /agent/applications
   */
  public async getApplications(
    query?: string,
    source?: string,
    limit: number = 250
  ): Promise<ApiResponse<ApplicationsListResponse>> {
    const params = new URLSearchParams();
    if (query && query.trim()) {
      params.set('query', query.trim());
    }
    if (source && source.trim() && source !== 'all') {
      params.set('source', source.trim());
    }
    params.set('limit', String(limit));
    const qs = params.toString();
    return this.request<ApplicationsListResponse>(
      `/agent/applications${qs ? `?${qs}` : ''}`,
      {},
      20000
    );
  }

  /**
   * Refresh the Windows application discovery index: POST /agent/applications/refresh
   */
  public async refreshApplications(
    includeStoreApps: boolean = true
  ): Promise<ApiResponse<ApplicationsListResponse>> {
    return this.request<ApplicationsListResponse>(
      '/agent/applications/refresh',
      {
        method: 'POST',
        body: JSON.stringify({ include_store_apps: includeStoreApps }),
      },
      30000
    );
  }

  /**
   * Launch a discovered application, Store app, Settings page, or utility: POST /agent/applications/launch
   */
  public async launchApplication(
    query: string,
    confirmationToken?: string
  ): Promise<ApiResponse<DirectLaunchResponse>> {
    return this.request<DirectLaunchResponse>(
      '/agent/applications/launch',
      {
        method: 'POST',
        body: JSON.stringify({
          query,
          confirmation_token: confirmationToken ?? null,
        }),
      },
      15000
    );
  }

  // ===========================================================================
  // Hardware Telemetry & Local AI Model Lifecycle API (Phase 1A)
  // ===========================================================================

  /**
   * Fetch read-only host CPU, RAM, GPU, and Ollama telemetry: GET /api/system/hardware
   */
  public async getSystemHardware(): Promise<ApiResponse<SystemHardwareResponse>> {
    return this.request<SystemHardwareResponse>('/api/system/hardware', {}, 10000);
  }

  /**
   * Fetch installed and running Ollama models: GET /api/system/models
   */
  public async getSystemModels(): Promise<ApiResponse<SystemModelsResponse>> {
    return this.request<SystemModelsResponse>('/api/system/models', {}, 10000);
  }

  /**
   * Fetch currently loaded Ollama models: GET /api/system/models/running
   */
  public async getRunningModels(): Promise<ApiResponse<RunningModelsResponse>> {
    return this.request<RunningModelsResponse>('/api/system/models/running', {}, 10000);
  }

  /**
   * Load an installed Ollama model with resource checks: POST /api/system/models/load
   */
  public async loadSystemModel(
    modelName: string,
    keepAlive?: string
  ): Promise<ApiResponse<ModelOperationResponse>> {
    return this.request<ModelOperationResponse>(
      '/api/system/models/load',
      {
        method: 'POST',
        body: JSON.stringify({
          model_name: modelName,
          keep_alive: keepAlive ?? null,
        }),
      },
      65000
    );
  }

  /**
   * Safely unload a running Ollama model: POST /api/system/models/unload
   */
  public async unloadSystemModel(
    modelName: string
  ): Promise<ApiResponse<ModelOperationResponse>> {
    return this.request<ModelOperationResponse>(
      '/api/system/models/unload',
      {
        method: 'POST',
        body: JSON.stringify({
          model_name: modelName,
        }),
      },
      15000
    );
  }

  // ===========================================================================
  // Local Persistent AI Memory Management API (Phase 1B)
  // ===========================================================================

  /**
   * Fetch paginated local long-term AI memories: GET /api/memory
   */
  public async getMemories(
    limit: number = 50,
    offset: number = 0,
    memoryType?: string,
    profileId?: string
  ): Promise<ApiResponse<MemoryListResponse>> {
    const params = new URLSearchParams();
    params.set('limit', String(limit));
    params.set('offset', String(offset));
    if (profileId) {
      params.set('profile_id', profileId);
    }
    if (memoryType && memoryType !== 'all') {
      params.set('memory_type', memoryType);
    }
    return this.request<MemoryListResponse>(`/api/memory?${params.toString()}`, {}, 10000);
  }

  /**
   * Full-text search local long-term AI memories: GET /api/memory/search
   */
  public async searchMemories(
    query: string,
    limit: number = 25,
    memoryType?: string,
    profileId?: string
  ): Promise<ApiResponse<MemorySearchResponse>> {
    const params = new URLSearchParams();
    params.set('q', query);
    params.set('limit', String(limit));
    if (profileId) {
      params.set('profile_id', profileId);
    }
    if (memoryType && memoryType !== 'all') {
      params.set('memory_type', memoryType);
    }
    return this.request<MemorySearchResponse>(`/api/memory/search?${params.toString()}`, {}, 10000);
  }

  /**
   * Fetch single memory item by ID: GET /api/memory/{memory_id}
   */
  public async getMemory(memoryId: string): Promise<ApiResponse<MemoryItem>> {
    return this.request<MemoryItem>(
      `/api/memory/${encodeURIComponent(memoryId)}`,
      {},
      8000
    );
  }

  /**
   * Store a new local long-term memory item: POST /api/memory
   */
  public async createMemory(payload: MemoryCreateRequest): Promise<ApiResponse<MemoryItem>> {
    return this.request<MemoryItem>(
      '/api/memory',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      10000
    );
  }

  /**
   * Update an existing local memory item: PATCH /api/memory/{memory_id}
   */
  public async updateMemory(
    memoryId: string,
    payload: MemoryUpdateRequest
  ): Promise<ApiResponse<MemoryItem>> {
    return this.request<MemoryItem>(
      `/api/memory/${encodeURIComponent(memoryId)}`,
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      },
      10000
    );
  }

  /**
   * Delete a stored local memory item: DELETE /api/memory/{memory_id}
   */
  public async deleteMemory(memoryId: string): Promise<ApiResponse<MemoryDeleteResponse>> {
    return this.request<MemoryDeleteResponse>(
      `/api/memory/${encodeURIComponent(memoryId)}`,
      {
        method: 'DELETE',
      },
      10000
    );
  }

  // ==========================================================================
  // Personalization Endpoints (Phase 2A)
  // ==========================================================================

  /**
   * List explicit user preferences: GET /api/personalization
   */
  public async getPreferences(params?: {
    profile_id?: string;
    category?: string;
    enabled_only?: boolean;
    limit?: number;
    offset?: number;
  }): Promise<ApiResponse<PreferenceListResponse>> {
    const query = new URLSearchParams();
    if (params?.profile_id) query.set('profile_id', params.profile_id);
    if (params?.category) query.set('category', params.category);
    if (params?.enabled_only !== undefined) query.set('enabled_only', String(params.enabled_only));
    if (params?.limit !== undefined) query.set('limit', String(params.limit));
    if (params?.offset !== undefined) query.set('offset', String(params.offset));
    const qs = query.toString();
    return this.request<PreferenceListResponse>(
      `/api/personalization${qs ? `?${qs}` : ''}`,
      {},
      10000
    );
  }

  /**
   * Get a single preference: GET /api/personalization/{preference_id}
   */
  public async getPreference(preferenceId: string): Promise<ApiResponse<PreferenceItem>> {
    return this.request<PreferenceItem>(
      `/api/personalization/${encodeURIComponent(preferenceId)}`,
      {},
      10000
    );
  }

  /**
   * Create a new explicit user preference: POST /api/personalization
   */
  public async createPreference(
    payload: PreferenceCreateRequest
  ): Promise<ApiResponse<PreferenceItem>> {
    return this.request<PreferenceItem>(
      '/api/personalization',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      10000
    );
  }

  /**
   * Update an existing preference: PATCH /api/personalization/{preference_id}
   */
  public async updatePreference(
    preferenceId: string,
    payload: PreferenceUpdateRequest
  ): Promise<ApiResponse<PreferenceItem>> {
    return this.request<PreferenceItem>(
      `/api/personalization/${encodeURIComponent(preferenceId)}`,
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      },
      10000
    );
  }

  /**
   * Delete an explicit user preference: DELETE /api/personalization/{preference_id}
   */
  public async deletePreference(
    preferenceId: string
  ): Promise<ApiResponse<PreferenceDeleteResponse>> {
    return this.request<PreferenceDeleteResponse>(
      `/api/personalization/${encodeURIComponent(preferenceId)}`,
      {
        method: 'DELETE',
      },
      10000
    );
  }

  // ==========================================================================
  // Multi-Profile Endpoints (Phase 2B)
  // ==========================================================================

  /**
   * List all local profiles and the currently active profile: GET /api/profiles
   */
  public async getProfiles(): Promise<ApiResponse<ProfileListResponse>> {
    return this.request<ProfileListResponse>('/api/profiles', {}, 10000);
  }

  /**
   * Get the currently active local profile: GET /api/profiles/active
   */
  public async getActiveProfile(): Promise<ApiResponse<ProfileItem>> {
    return this.request<ProfileItem>('/api/profiles/active', {}, 10000);
  }

  /**
   * Create a new local profile: POST /api/profiles
   */
  public async createProfile(
    payload: ProfileCreateRequest
  ): Promise<ApiResponse<ProfileItem>> {
    return this.request<ProfileItem>(
      '/api/profiles',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      10000
    );
  }

  /**
   * Update a local profile: PATCH /api/profiles/{profile_id}
   */
  public async updateProfile(
    profileId: string,
    payload: ProfileUpdateRequest
  ): Promise<ApiResponse<ProfileItem>> {
    return this.request<ProfileItem>(
      `/api/profiles/${encodeURIComponent(profileId)}`,
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      },
      10000
    );
  }

  /**
   * Switch active local profile: POST /api/profiles/{profile_id}/activate
   */
  public async activateProfile(profileId: string): Promise<ApiResponse<ProfileItem>> {
    return this.request<ProfileItem>(
      `/api/profiles/${encodeURIComponent(profileId)}/activate`,
      {
        method: 'POST',
      },
      10000
    );
  }

  /**
   * Delete an eligible local profile: DELETE /api/profiles/{profile_id}
   */
  public async deleteProfile(
    profileId: string
  ): Promise<ApiResponse<ProfileDeleteResponse>> {
    return this.request<ProfileDeleteResponse>(
      `/api/profiles/${encodeURIComponent(profileId)}`,
      {
        method: 'DELETE',
      },
      10000
    );
  }

  // ============================================================================
  // Screen Awareness & Local Vision Endpoints
  // ============================================================================

  public async getScreenVisionStatus(): Promise<ApiResponse<VisionAvailabilityStatus>> {
    return this.request<VisionAvailabilityStatus>('/api/screen/status', {}, 10000);
  }

  public async captureScreen(
    confirmed: boolean = true,
    profileId?: string
  ): Promise<ApiResponse<ScreenCaptureResponse>> {
    return this.request<ScreenCaptureResponse>(
      '/api/screen/capture',
      {
        method: 'POST',
        body: JSON.stringify({
          confirmed,
          profile_id: profileId || undefined,
          include_preview: true,
        }),
      },
      15000
    );
  }

  public async deleteScreenCapture(
    captureId: string,
    profileId?: string
  ): Promise<ApiResponse<{ capture_id: string; removed: boolean; profile_id: string }>> {
    const query = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : '';
    return this.request<{ capture_id: string; removed: boolean; profile_id: string }>(
      `/api/screen/capture/${encodeURIComponent(captureId)}${query}`,
      {
        method: 'DELETE',
      },
      10000
    );
  }

  // ============================================================================
  // AI Email Assistant API (Phase 4D)
  // ============================================================================

  public async getEmailAccountStatus(
    profileId?: string
  ): Promise<ApiResponse<EmailAccountStatusResponse>> {
    const query = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : '';
    return this.request<EmailAccountStatusResponse>(`/api/email/account${query}`, {}, 10000);
  }

  public async configureEmailAccount(payload: {
    profile_id?: string;
    email_address: string;
    password: string;
    imap_host: string;
    imap_port: number;
    smtp_host: string;
    smtp_port: number;
    use_starttls: boolean;
    display_name?: string;
    authorized_by_user: boolean;
  }): Promise<ApiResponse<EmailAccountStatusResponse>> {
    return this.request<EmailAccountStatusResponse>(
      '/api/email/account',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      15000
    );
  }

  public async disconnectEmailAccount(
    profileId?: string
  ): Promise<ApiResponse<{ success: boolean; disconnected: boolean; profile_id: string }>> {
    const query = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : '';
    return this.request<{ success: boolean; disconnected: boolean; profile_id: string }>(
      `/api/email/account${query}`,
      { method: 'DELETE' },
      10000
    );
  }

  public async listEmailDrafts(
    profileId?: string
  ): Promise<ApiResponse<{ drafts: EmailDraftItem[] }>> {
    const query = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : '';
    return this.request<{ drafts: EmailDraftItem[] }>(`/api/email/drafts${query}`, {}, 10000);
  }

  public async updateEmailDraft(
    draftId: string,
    payload: { profile_id?: string; to?: string[]; subject?: string; body?: string }
  ): Promise<ApiResponse<{ success: boolean; draft: EmailDraftItem }>> {
    return this.request<{ success: boolean; draft: EmailDraftItem }>(
      `/api/email/drafts/${encodeURIComponent(draftId)}`,
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      },
      10000
    );
  }

  public async requestEmailSendConfirmation(
    draftId: string,
    profileId?: string
  ): Promise<ApiResponse<EmailDraftSendConfirmationResponse>> {
    const query = profileId ? `?profile_id=${encodeURIComponent(profileId)}` : '';
    return this.request<EmailDraftSendConfirmationResponse>(
      `/api/email/drafts/${encodeURIComponent(draftId)}/request-send${query}`,
      { method: 'POST' },
      10000
    );
  }

  public async confirmAndSendEmail(
    draftId: string,
    payload: { profile_id?: string; confirmation_token: string; approved: boolean }
  ): Promise<ApiResponse<{ success: boolean; status: string; draft: EmailDraftItem; message: string }>> {
    return this.request<{ success: boolean; status: string; draft: EmailDraftItem; message: string }>(
      `/api/email/drafts/${encodeURIComponent(draftId)}/send`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      20000
    );
  }

  // ============================================================================
  // Wake-on-Startup API (Phase 5A)
  // ============================================================================

  public async getStartupStatus(): Promise<ApiResponse<SystemStartupStatus>> {
    return this.request<SystemStartupStatus>('/api/system/startup', {}, 10000);
  }

  public async updateStartupStatus(
    enabled: boolean
  ): Promise<ApiResponse<SystemStartupStatus>> {
    return this.request<SystemStartupStatus>(
      '/api/system/startup',
      {
        method: 'POST',
        body: JSON.stringify({ enabled }),
      },
      10000
    );
  }

  // ============================================================================
  // Native Desktop Notifications API (Phase 5C)
  // ============================================================================

  public async getNotificationStatus(): Promise<ApiResponse<NotificationStatusResponse>> {
    return this.request<NotificationStatusResponse>('/api/system/notifications', {}, 10000);
  }

  public async updateNotificationPreferences(
    updates: Partial<NotificationPreferences>
  ): Promise<ApiResponse<NotificationStatusResponse>> {
    return this.request<NotificationStatusResponse>(
      '/api/system/notifications',
      {
        method: 'POST',
        body: JSON.stringify(updates),
      },
      10000
    );
  }

  public async resetNotificationPreferences(): Promise<ApiResponse<NotificationStatusResponse>> {
    return this.request<NotificationStatusResponse>(
      '/api/system/notifications/reset',
      {
        method: 'POST',
        body: JSON.stringify({}),
      },
      10000
    );
  }

  public async sendTestNotification(): Promise<ApiResponse<NotificationTestResponse>> {
    return this.request<NotificationTestResponse>(
      '/api/system/notifications/test',
      {
        method: 'POST',
        body: JSON.stringify({}),
      },
      10000
    );
  }

  // ============================================================================
  // Custom Automation Builder API (Phase 6A)
  // ============================================================================

  public async previewAutomation(prompt: string): Promise<ApiResponse<AutomationPreviewResponse>> {
    return this.request<AutomationPreviewResponse>(
      '/api/automations/preview',
      {
        method: 'POST',
        body: JSON.stringify({ prompt }),
      },
      15000
    );
  }

  public async listAutomations(): Promise<ApiResponse<{ automations: CustomAutomation[] }>> {
    return this.request<{ automations: CustomAutomation[] }>('/api/automations', {}, 10000);
  }

  public async createAutomation(payload: {
    name: string;
    description: string;
    trigger_type: AutomationTriggerType;
    trigger_config: AutomationTriggerConfig;
    steps: AutomationStep[];
    enabled?: boolean;
  }): Promise<ApiResponse<CustomAutomation>> {
    return this.request<CustomAutomation>(
      '/api/automations',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      10000
    );
  }

  public async toggleAutomation(
    automationId: string,
    enabled: boolean
  ): Promise<ApiResponse<CustomAutomation>> {
    return this.request<CustomAutomation>(
      `/api/automations/${encodeURIComponent(automationId)}/toggle`,
      {
        method: 'POST',
        body: JSON.stringify({ enabled }),
      },
      10000
    );
  }

  public async updateAutomationSchedule(
    automationId: string,
    schedule: AutomationTriggerConfig
  ): Promise<ApiResponse<CustomAutomation>> {
    return this.request<CustomAutomation>(
      `/api/automations/${encodeURIComponent(automationId)}/schedule`,
      {
        method: 'POST',
        body: JSON.stringify(schedule),
      },
      10000
    );
  }

  public async deleteAutomation(
    automationId: string
  ): Promise<ApiResponse<{ deleted: boolean; automation_id: string }>> {
    return this.request<{ deleted: boolean; automation_id: string }>(
      `/api/automations/${encodeURIComponent(automationId)}`,
      { method: 'DELETE' },
      10000
    );
  }

  public async executeAutomation(
    automationId: string,
    confirmationToken?: string
  ): Promise<ApiResponse<AutomationExecutionStatus>> {
    return this.request<AutomationExecutionStatus>(
      `/api/automations/${encodeURIComponent(automationId)}/execute`,
      {
        method: 'POST',
        body: JSON.stringify({ confirmation_token: confirmationToken || null }),
      },
      60000
    );
  }

  public async cancelAutomationExecution(
    executionId: string
  ): Promise<ApiResponse<AutomationExecutionStatus>> {
    return this.request<AutomationExecutionStatus>(
      `/api/automations/executions/${encodeURIComponent(executionId)}/cancel`,
      {
        method: 'POST',
        body: JSON.stringify({}),
      },
      10000
    );
  }

  // ============================================================================
  // Automation History & Management API (Phase 6B)
  // ============================================================================

  public async listAutomationHistory(params?: {
    automation_id?: string;
    status?: string;
    trigger_type?: string;
    date_from?: string;
    date_to?: string;
    limit?: number;
    offset?: number;
  }): Promise<ApiResponse<AutomationExecutionHistoryListResponse>> {
    const qs = new URLSearchParams();
    if (params?.automation_id) qs.set('automation_id', params.automation_id);
    if (params?.status) qs.set('status', params.status);
    if (params?.trigger_type) qs.set('trigger_type', params.trigger_type);
    if (params?.date_from) qs.set('date_from', params.date_from);
    if (params?.date_to) qs.set('date_to', params.date_to);
    if (params?.limit !== undefined) qs.set('limit', String(params.limit));
    if (params?.offset !== undefined) qs.set('offset', String(params.offset));
    const query = qs.toString() ? `?${qs.toString()}` : '';
    return this.request<AutomationExecutionHistoryListResponse>(
      `/api/automations/history${query}`,
      {},
      10000
    );
  }

  public async getAutomationExecution(
    executionId: string
  ): Promise<ApiResponse<AutomationExecutionStatus>> {
    return this.request<AutomationExecutionStatus>(
      `/api/automations/executions/${encodeURIComponent(executionId)}`,
      {},
      10000
    );
  }

  public async retryAutomationExecution(
    executionId: string,
    allowRerunWithSideEffects: boolean = false
  ): Promise<ApiResponse<AutomationExecutionStatus>> {
    return this.request<AutomationExecutionStatus>(
      `/api/automations/executions/${encodeURIComponent(executionId)}/retry`,
      {
        method: 'POST',
        body: JSON.stringify({ allow_rerun_with_side_effects: allowRerunWithSideEffects }),
      },
      60000
    );
  }

  public async deleteAutomationHistory(
    executionId: string
  ): Promise<ApiResponse<{ deleted: boolean; execution_id: string }>> {
    return this.request<{ deleted: boolean; execution_id: string }>(
      `/api/automations/executions/${encodeURIComponent(executionId)}`,
      { method: 'DELETE' },
      10000
    );
  }

  public async clearAutomationHistory(
    confirm: boolean = true
  ): Promise<ApiResponse<{ cleared: boolean; deleted_count: number; profile_id: string }>> {
    return this.request<{ cleared: boolean; deleted_count: number; profile_id: string }>(
      `/api/automations/history?confirm=${confirm ? 'true' : 'false'}`,
      { method: 'DELETE' },
      10000
    );
  }

  // ============================================================================
  // Three-Mode AI Provider & BYOK API (Phases 8A–8B)
  // ============================================================================

  public async getProviders(): Promise<ApiResponse<ProvidersListResponse>> {
    return this.request<ProvidersListResponse>('/api/providers', {}, 10000);
  }

  public async getActiveProvider(): Promise<ApiResponse<ActiveProviderResponse>> {
    return this.request<ActiveProviderResponse>('/api/providers/active', {}, 10000);
  }

  public async selectProvider(
    providerId: AIProviderId | string,
    config?: Record<string, unknown>
  ): Promise<ApiResponse<{ profile_config: ProfileProviderConfig; health: AIProviderHealth }>> {
    return this.request<{ profile_config: ProfileProviderConfig; health: AIProviderHealth }>(
      '/api/providers/select',
      {
        method: 'POST',
        body: JSON.stringify({ provider_id: providerId, config: config ?? null }),
      },
      15000
    );
  }

  public async configureOpenAIBYOK(payload: {
    api_key: string;
    model?: string;
    validate_connection?: boolean;
  }): Promise<ApiResponse<OpenAIBYOKConfigureResponse>> {
    return this.request<OpenAIBYOKConfigureResponse>(
      '/api/providers/byok/openai/configure',
      {
        method: 'POST',
        body: JSON.stringify(payload),
      },
      20000
    );
  }

  public async validateOpenAIBYOK(payload?: {
    api_key?: string;
    model?: string;
  }): Promise<ApiResponse<OpenAIBYOKValidateResponse>> {
    return this.request<OpenAIBYOKValidateResponse>(
      '/api/providers/byok/openai/validate',
      {
        method: 'POST',
        body: JSON.stringify(payload ?? {}),
      },
      20000
    );
  }

  public async setOpenAIBYOKModel(
    model: string
  ): Promise<ApiResponse<{ profile_config: ProfileProviderConfig }>> {
    return this.request<{ profile_config: ProfileProviderConfig }>(
      '/api/providers/byok/openai/model',
      {
        method: 'POST',
        body: JSON.stringify({ model }),
      },
      10000
    );
  }

  public async removeOpenAIBYOKCredentials(): Promise<
    ApiResponse<{ profile_config: ProfileProviderConfig; removed: boolean }>
  > {
    return this.request<{ profile_config: ProfileProviderConfig; removed: boolean }>(
      '/api/providers/byok/openai/credentials',
      { method: 'DELETE' },
      10000
    );
  }
}

export const apiClient = new ApiClient();
export default apiClient;
