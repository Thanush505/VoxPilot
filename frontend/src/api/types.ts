/**
 * VoxPilot Frontend - TypeScript API Types
 * Definitions matching the actual backend FastAPI schemas in backend/main.py and backend/api/agent.py.
 */

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
}

export interface RootResponse {
  app: string;
  version: string;
  status: string;
}

export interface EmergencyStopStatus {
  is_active: boolean;
  trigger_reason: string | null;
  trigger_source: string | null;
  triggered_at: number | null;
  reset_at: number | null;
  active_operations_count: number;
  active_operations: Array<{
    operation_id: string;
    execution_id?: string;
    category: string;
    description: string;
    start_time: number;
    status: string;
    pids?: number[];
  }>;
}

export interface EmergencyStopTriggerResponse {
  success: boolean;
  message: string;
  is_active: boolean;
  trigger_reason?: string;
  trigger_source?: string;
  triggered_at?: number;
}

export interface EmergencyStopResetResponse {
  success: boolean;
  message: string;
  is_active: boolean;
  reset_at?: number;
}

export interface ConversationInfo {
  conversation_id: string;
  title: string | null;
  summary: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ConversationDetailResponse {
  conversation_id: string;
  title: string | null;
  summary: string | null;
  created_at: string | null;
  updated_at: string | null;
  context_summary: string | null;
  execution_count: number;
}

export interface ToolInfo {
  name: string;
  category: string;
  description: string;
  is_consequential: boolean;
}

export interface PendingConfirmationInfo {
  token: string;
  action: string;
  parameters: Record<string, unknown>;
  description?: string | null;
  execution_id?: string | null;
  step_index?: number | null;
}

export interface ExecutionPlanStep {
  step_index?: number;
  action: string;
  parameters?: Record<string, unknown>;
  description?: string;
  is_consequential?: boolean;
}

export interface ExecutionStepResult {
  step_index?: number;
  action?: string;
  status: string;
  result?: unknown;
  error?: string | null;
  timestamp?: number | string;
}

export interface ExecutionStatusResponse {
  execution_id: string;
  conversation_id?: string | null;
  execution_status: 'pending' | 'planning' | 'executing' | 'confirmation_required' | 'completed' | 'failed' | 'cancelled' | 'rate_limited' | string;
  user_input: string;
  intent?: string | null;
  confidence?: number | null;
  plan: ExecutionPlanStep[];
  execution_results: ExecutionStepResult[];
  completed_steps: ExecutionStepResult[];
  failed_steps: ExecutionStepResult[];
  response?: string | null;
  error?: string | null;
  requires_confirmation: boolean;
  pending_confirmation?: PendingConfirmationInfo | null;
  replan_count?: number;
  replan_history?: Array<Record<string, unknown>>;
}

export interface ExecuteRequest {
  user_input: string;
  execution_id?: string;
  conversation_id?: string;
}

export interface ConfirmationApprovalRequest {
  token: string;
}

export interface ConfirmationRejectionRequest {
  token: string;
  reason?: string;
}

export interface ApiResponse<T> {
  data: T | null;
  error: string | null;
  status: number;
  ok: boolean;
}

export interface SystemOverviewState {
  isAvailable: boolean;
  reason: string;
  metrics: {
    os: string | null;
    cpuPercent: number | null;
    ramPercent: number | null;
    ramTotalGb: number | null;
    ramAvailableGb: number | null;
  };
}

/**
 * Chat Message Model for UI rendering
 */
export interface ChatMessage {
  id: string;
  sender: 'user' | 'agent' | 'system';
  text: string;
  timestamp: Date;
  executionId?: string;
  status?: string;
  plan?: ExecutionPlanStep[];
  completedSteps?: ExecutionStepResult[];
  error?: string | null;
  pendingConfirmation?: PendingConfirmationInfo | null;
  isVoice?: boolean;
}

export interface AuditExportRequest {
  format: 'json' | 'csv';
  filename?: string;
  execution_id?: string;
  conversation_id?: string;
  status_filter?: 'completed' | 'failed' | 'cancelled' | 'rate_limited' | string;
  start_date?: string;
  end_date?: string;
  confirm_overwrite?: boolean;
}

export interface AuditExportResponse {
  export_id: string;
  filename: string;
  file_path: string;
  format: string;
  record_count: number;
  exported_at?: string;
  status: string;
}

/**
 * Voice Mode & Customizable Global Shortcut Types
 */
export type VoicePhase = 'idle' | 'listening' | 'transcribing' | 'processing' | 'speaking' | 'error';

export interface VoiceSettings {
  shortcut_enabled: boolean;
  shortcut: string;
  microphone_device: number | string | null;
  tts_enabled: boolean;
  piper_voice: string;
  streaming_vad_enabled: boolean;
}

export interface VoiceShortcutStatus {
  shortcut: string;
  enabled: boolean;
  is_registered: boolean;
  registration_error: string | null;
  trigger_count: number;
  reserved_emergency_shortcut: string;
}

export interface MicrophoneDevice {
  index: number;
  name: string;
  host_api: string;
  channels: number;
  default_sample_rate: number;
}

export interface MicrophoneListResponse {
  devices: MicrophoneDevice[];
  configured_device: number | string | null;
  selected_device: MicrophoneDevice | null;
  error: string | null;
}

export interface MicrophoneTestResponse {
  success: boolean;
  device: MicrophoneDevice | null;
  sample_rate: number;
  channels: number;
  rms_level: number;
  peak_level: number;
  signal_detected: boolean;
  message: string;
}

export interface PiperVoiceModel {
  id: string;
  name: string;
  path: string;
  has_config: boolean;
}

export interface VoiceEvent {
  event_id: number;
  type: string;
  phase: VoicePhase;
  is_active: boolean;
  conversation_id: string | null;
  timestamp: number;
  text?: string;
  user_input?: string;
  response?: string;
  error?: string;
  reason?: string;
  source?: string;
  execution?: ExecutionStatusResponse | null;
}

export interface VoiceStatusResponse {
  is_active: boolean;
  phase: VoicePhase;
  is_recording: boolean;
  conversation_id: string | null;
  last_transcript: string | null;
  last_response: string | null;
  last_error: string | null;
  last_agent_result: ExecutionStatusResponse | null;
  settings: VoiceSettings;
  shortcut_status: VoiceShortcutStatus;
  components: {
    microphone: {
      available: boolean;
      device_count: number;
      selected_device: MicrophoneDevice | null;
      error: string | null;
    };
    stt: {
      available: boolean;
      engine: string;
      model_size: string;
      device: string;
      compute_type: string;
      loaded: boolean;
      cached: boolean;
    };
    tts: {
      available: boolean;
      enabled: boolean;
      engine: string;
      piper_voice: string;
      model_path: string;
      model_exists: boolean;
      loaded: boolean;
      available_voices: PiperVoiceModel[];
    };
    vad: {
      available: boolean;
      enabled: boolean;
      engine: string;
      aggressiveness: number;
      frame_duration_ms: number;
      end_silence_ms: number;
    };
  };
  events: VoiceEvent[];
  latest_event_id: number;
}

/**
 * Software Update & Version Management Types
 */
export type UpdateLifecycleState =
  | 'idle'
  | 'checking'
  | 'up_to_date'
  | 'update_available'
  | 'downloading'
  | 'verifying'
  | 'ready_to_install'
  | 'installing'
  | 'failed'
  | 'offline';

export type UpdateChannel = 'stable' | 'beta';

export interface AppVersionResponse {
  app_name: string;
  installed_version: string;
  is_prerelease: boolean;
  channel: UpdateChannel;
  release_repo: string;
  is_frozen: boolean;
  last_checked_at: string | null;
}

export interface UpdateReleaseInfo {
  version: string;
  tag_name: string | null;
  title: string | null;
  notes: string;
  published_at: string | null;
  release_url: string | null;
  installer_name: string | null;
  installer_url: string | null;
  installer_size_bytes: number | null;
  expected_sha256: string | null;
}

export interface UpdateSignatureInfo {
  signature_checked: boolean;
  signature_status: string;
  signature_valid: boolean;
  signer_subject: string | null;
  integrity_verification: string;
  publisher_verification_note: string;
}

export interface UpdateDownloadInfo {
  downloaded_bytes: number;
  total_bytes: number;
  progress_pct: number;
  installer_path: string | null;
  computed_sha256: string | null;
  sha256_verified: boolean;
  signature: UpdateSignatureInfo | null;
}

export interface UpdateInstallResult {
  status: string;
  target_version: string;
  installer_path: string;
  started_at: string;
  completed_at: string | null;
  exit_code: number | null;
  relaunch_requested: boolean;
  relaunched: boolean;
  error: string | null;
}

export interface UpdateStatusResponse {
  state: UpdateLifecycleState;
  installed_version: string;
  latest_version: string | null;
  update_available: boolean;
  notification_dismissed: boolean;
  channel: UpdateChannel;
  auto_check_enabled: boolean;
  auto_install_enabled: boolean;
  release_repo: string;
  last_checked_at: string | null;
  release: UpdateReleaseInfo | null;
  download: UpdateDownloadInfo;
  last_install_result: UpdateInstallResult | null;
  status_message: string;
  error_code: string | null;
  error_message: string | null;
}

export interface UpdateSettings {
  auto_check_enabled: boolean;
  channel: UpdateChannel;
  release_repo: string;
  last_checked_at: string | null;
  last_available_version: string | null;
  dismissed_version: string | null;
  auto_install_enabled: boolean;
}



