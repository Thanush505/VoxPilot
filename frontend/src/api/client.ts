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
    executionId?: string
  ): Promise<ApiResponse<ExecutionStatusResponse>> {
    return this.request<ExecutionStatusResponse>(
      '/agent/execute',
      {
        method: 'POST',
        body: JSON.stringify({
          user_input: userInput,
          conversation_id: conversationId || undefined,
          execution_id: executionId || undefined,
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
}

export const apiClient = new ApiClient();
export default apiClient;
