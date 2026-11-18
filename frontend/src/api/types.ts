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
export type VoicePhase = 'idle' | 'listening' | 'transcribing' | 'processing' | 'speaking' | 'interrupted' | 'error';

export interface VoiceSettings {
  shortcut_enabled: boolean;
  shortcut: string;
  microphone_device: number | string | null;
  tts_enabled: boolean;
  piper_voice: string;
  streaming_vad_enabled: boolean;
  wake_word_enabled?: boolean;
  wake_word_cooldown_seconds?: number;
  floating_orb_enabled?: boolean;
  floating_orb_visible?: boolean;
  floating_orb_show_when_idle?: boolean;
  floating_orb_corner?: 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right' | 'custom';
  floating_orb_x?: number | null;
  floating_orb_y?: number | null;
  floating_orb_animations?: boolean;
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

export interface VoiceAudioTelemetry {
  active: boolean;
  mode: 'microphone' | 'tts' | 'idle';
  rms_level: number;
  peak_level: number;
  is_speech: boolean;
  bands: number[];
}

export interface VoiceStatusResponse {
  is_active: boolean;
  phase: VoicePhase;
  is_recording: boolean;
  is_ptt_active?: boolean;
  audio_telemetry?: VoiceAudioTelemetry;
  wake_word_enabled?: boolean;
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

export interface UpdateBackendHealth {
  ok: boolean;
  url?: string | null;
  status?: string | null;
  version?: string | null;
  error?: string | null;
}

export interface UpdateInstallResult {
  status: string;
  session_id?: string | null;
  previous_version?: string | null;
  target_version: string;
  installed_version?: string | null;
  installer_filename?: string | null;
  installer_path: string;
  installer_log?: string | null;
  runner_log?: string | null;
  session_log?: string | null;
  started_at: string;
  completed_at: string | null;
  exit_code: number | null;
  relaunch_requested: boolean;
  relaunched: boolean;
  restart_result?: string | null;
  backend_health?: UpdateBackendHealth | null;
  recovery_hint?: string | null;
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

/**
 * Universal Windows Application Discovery & Launching Types
 */
export interface DiscoveredApplicationItem {
  app_id: string;
  name: string;
  display_name: string;
  executable_path: string;
  source: string;
  launch_method: string;
  publisher: string | null;
  install_location: string | null;
  package_id: string | null;
  shortcut_path?: string | null;
  arguments: string[];
  aliases: string[];
  requires_confirmation: boolean;
  discovered_at: string;
}

export interface ApplicationsListResponse {
  status?: string;
  applications: DiscoveredApplicationItem[];
  returned_count: number;
  total_count: number;
  by_source: Record<string, number>;
  last_refreshed_at: string | null;
  errors: string[];
  message?: string;
}

export interface DirectLaunchResponse {
  success: boolean;
  app_id?: string;
  display_name: string;
  pid?: number | null;
  status: string;
  launch_method?: string;
  source?: string;
  requires_confirmation?: boolean;
  confirmation_token?: string;
  candidates?: string[];
  message: string;
  timestamp?: string;
}

/**
 * Hardware Monitor & Local AI Model Lifecycle Types (Phase 1A)
 */
export interface CpuMetrics {
  utilization_percent: number;
  logical_cores: number;
  physical_cores: number | null;
  frequency_mhz: number | null;
  min_frequency_mhz: number | null;
  max_frequency_mhz: number | null;
  temperature_celsius: number | null;
  temperature_available: boolean;
}

export interface RamMetrics {
  total_bytes: number;
  used_bytes: number;
  available_bytes: number;
  total_gb: number;
  used_gb: number;
  available_gb: number;
  utilization_percent: number;
}

export interface GpuMetrics {
  available: boolean;
  name: string | null;
  utilization_percent: number | null;
  temperature_celsius: number | null;
  vram_total_mb: number | null;
  vram_used_mb: number | null;
  vram_free_mb: number | null;
  vram_utilization_percent: number | null;
  status_reason: string | null;
}

export interface OllamaInstalledModel {
  name: string;
  model: string;
  size_bytes: number;
  size_gb: number;
  digest: string | null;
  modified_at: string | null;
  family: string | null;
  families: string[];
  parameter_size: string | null;
  quantization_level: string | null;
  format: string | null;
}

export interface OllamaRunningModel {
  name: string;
  model: string;
  size_bytes: number;
  size_gb: number;
  size_vram_bytes: number;
  size_vram_mb: number;
  vram_percent: number | null;
  digest: string | null;
  expires_at: string | null;
  parameter_size: string | null;
  quantization_level: string | null;
  context_length: number | null;
}

export interface OllamaTelemetry {
  reachable: boolean;
  base_url: string;
  installed_models: OllamaInstalledModel[];
  running_models: OllamaRunningModel[];
  installed_count: number;
  running_count: number;
  error: string | null;
}

export interface SystemHardwareResponse {
  timestamp: string;
  os_name: string;
  os_version: string;
  os_release: string;
  machine: string;
  cpu: CpuMetrics;
  ram: RamMetrics;
  gpu: GpuMetrics;
  ollama: OllamaTelemetry;
}

export interface SystemModelsResponse {
  reachable: boolean;
  base_url: string;
  default_model: string;
  active_model: string;
  installed_models: OllamaInstalledModel[];
  running_models: OllamaRunningModel[];
  installed_count: number;
  running_count: number;
  error: string | null;
}

export interface RunningModelsResponse {
  reachable: boolean;
  base_url: string;
  default_model: string;
  active_model: string;
  running_models: OllamaRunningModel[];
  running_count: number;
  error: string | null;
}

export interface ModelOperationResponse {
  success: boolean;
  action: string;
  model: string;
  active_model: string | null;
  default_model: string;
  already_loaded?: boolean | null;
  was_running?: boolean | null;
  unloaded_models: string[];
  keep_alive?: string | null;
  message: string;
  timestamp: string;
}

/**
 * Local Persistent AI Memory Management Types (Phase 1B)
 */
export type MemoryType =
  | 'user_preference'
  | 'user_fact'
  | 'instruction'
  | 'project_context'
  | 'general_context';

export interface MemoryItem {
  id: string;
  memory_id: string;
  profile_id: string;
  memory_type: MemoryType | string;
  content: string;
  normalized_content: string;
  source_conversation_id: string | null;
  importance: number;
  created_at: string | null;
  updated_at: string | null;
  last_accessed_at: string | null;
  metadata: Record<string, unknown>;
  deduplicated?: boolean | null;
}

export interface MemoryListResponse {
  memories: MemoryItem[];
  total: number;
  limit: number;
  offset: number;
  local_only: boolean;
}

export interface MemorySearchResponse {
  query: string;
  memories: MemoryItem[];
  count: number;
  local_only: boolean;
}

export interface MemoryCreateRequest {
  content: string;
  memory_type?: MemoryType | string;
  importance?: number;
  profile_id?: string;
  source_conversation_id?: string | null;
  metadata?: Record<string, unknown>;
}

export interface MemoryUpdateRequest {
  content?: string;
  memory_type?: MemoryType | string;
  importance?: number;
  metadata?: Record<string, unknown>;
}

export interface MemoryDeleteResponse {
  status: string;
  deleted: boolean;
  memory_id: string;
}

// ============================================================================
// Personalization Types (Phase 2A)
// ============================================================================

export type PreferenceCategory =
  | 'communication'
  | 'response_format'
  | 'technical'
  | 'workflow'
  | 'ai_behavior'
  | 'interface';

export interface PreferenceItem {
  id: string;
  preference_id: string;
  profile_id: string;
  category: PreferenceCategory | string;
  key: string;
  normalized_key: string;
  value: string;
  description?: string | null;
  enabled: boolean;
  priority: number;
  created_at?: string | null;
  updated_at?: string | null;
  metadata?: Record<string, unknown>;
}

export interface PreferenceListResponse {
  preferences: PreferenceItem[];
  total: number;
  limit: number;
  offset: number;
  categories: string[];
}

export interface PreferenceCreateRequest {
  category: PreferenceCategory | string;
  key: string;
  value: string;
  description?: string | null;
  enabled?: boolean;
  priority?: number;
  profile_id?: string;
  metadata?: Record<string, unknown>;
  upsert?: boolean;
}

export interface PreferenceUpdateRequest {
  category?: PreferenceCategory | string;
  key?: string;
  value?: string;
  description?: string | null;
  enabled?: boolean;
  priority?: number;
  metadata?: Record<string, unknown>;
}

export interface PreferenceDeleteResponse {
  deleted: boolean;
  preference_id: string;
}

// ============================================================================
// Multi-Profile Types (Phase 2B)
// ============================================================================

export type ProfileAvatar =
  | 'user'
  | 'briefcase'
  | 'graduation-cap'
  | 'code'
  | 'home'
  | 'sparkles';

export type ThemeMode = 'dark' | 'light' | 'system';

export type AccentColorId =
  | 'blue'
  | 'indigo'
  | 'emerald'
  | 'teal'
  | 'amber'
  | 'rose';

export interface ProfileItem {
  id: string;
  profile_id: string;
  name: string;
  normalized_name: string;
  description?: string | null;
  avatar: ProfileAvatar | string;
  theme_mode?: ThemeMode | string;
  accent_color?: AccentColorId | string;
  is_active: boolean;
  is_default: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProfileListResponse {
  profiles: ProfileItem[];
  active_profile: ProfileItem;
  total: number;
  avatars: string[];
  theme_modes?: string[];
  accent_colors?: string[];
}

export interface ProfileCreateRequest {
  name: string;
  description?: string | null;
  avatar?: ProfileAvatar | string;
  theme_mode?: ThemeMode | string;
  accent_color?: AccentColorId | string;
  activate?: boolean;
}

export interface ProfileUpdateRequest {
  name?: string;
  description?: string | null;
  avatar?: ProfileAvatar | string;
  theme_mode?: ThemeMode | string;
  accent_color?: AccentColorId | string;
}

export interface ProfileDeleteResponse {
  deleted: boolean;
  profile_id: string;
  active_profile_id: string;
}

/**
 * Screen Awareness & Local Vision Types
 */
export interface VisionModelRecommendation {
  name: string;
  display_name: string;
  storage_gb: number;
  vram_gb: number;
  ram_gb: number;
  fits_hardware: boolean;
  hardware_note: string;
  pull_command: string;
  notes: string;
}

export interface VisionAvailabilityStatus {
  vision_available: boolean;
  active_vision_model: string | null;
  installed_vision_models: string[];
  default_text_model: string;
  default_text_model_supports_vision: boolean;
  ollama_reachable: boolean;
  vram_total_mb: number | null;
  vram_free_mb: number | null;
  ram_free_gb: number | null;
  message: string;
  recommendations: VisionModelRecommendation[];
}

export interface ScreenCaptureResponse {
  capture_id: string;
  profile_id: string;
  width: number;
  height: number;
  mime_type: string;
  size_bytes: number;
  created_at: string;
  monitor_note: string;
  ephemeral: boolean;
  preview_data_url?: string;
  vision_status?: VisionAvailabilityStatus;
}

// ============================================================================
// AI Email Assistant Types (Phase 4D)
// ============================================================================

export interface EmailAccountPublicConfig {
  profile_id: string;
  email_address: string;
  imap_host: string;
  imap_port: number;
  smtp_host: string;
  smtp_port: number;
  use_starttls: boolean;
  display_name: string;
  authorized_by_user: boolean;
  connected: boolean;
}

export interface EmailAccountStatusResponse {
  connected: boolean;
  profile_id: string;
  account: EmailAccountPublicConfig | null;
}

export interface EmailDraftItem {
  draft_id: string;
  profile_id: string;
  to: string[];
  cc: string[];
  subject: string;
  body: string;
  in_reply_to?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface EmailDraftSendConfirmationResponse {
  success: boolean;
  requires_confirmation: boolean;
  confirmation_token: string;
  status: string;
  draft: EmailDraftItem;
  message: string;
}

// ============================================================================
// Wake-on-Startup Types (Phase 5A)
// ============================================================================

export interface SystemStartupStatus {
  enabled: boolean;
  supported: boolean;
  mode: string;
  mechanism: string;
  target_valid: boolean;
  reason?: string | null;
}

export interface NotificationCategoryMeta {
  id: string;
  label: string;
  description: string;
}

export interface NotificationPreferences {
  enabled: boolean;
  suppress_when_active: boolean;
  categories: Record<string, boolean>;
}

export interface NotificationStatusResponse {
  supported: boolean;
  platform: string;
  mode: string;
  mechanism: string;
  app_user_model_id: string;
  active_profile_id: string;
  ui_recently_active: boolean;
  last_error?: string | null;
  reason?: string | null;
  preferences: NotificationPreferences;
  categories_meta: NotificationCategoryMeta[];
}

export interface NotificationTestResponse {
  delivered: boolean;
  suppressed: boolean;
  reason?: string | null;
  category: string;
  title: string;
  message: string;
  status: NotificationStatusResponse;
}

export type AutomationTriggerType = 'manual' | 'scheduled' | 'user_requested';

export interface AutomationStep {
  step_id?: string;
  action: string;
  parameters: Record<string, unknown>;
  description?: string;
}

export interface AutomationTriggerConfig {
  frequency?: 'daily' | 'weekly';
  time_of_day?: string;
  weekdays?: number[];
  timezone?: string;
  paused?: boolean;
  last_scheduled_slot?: string | null;
}

export interface CustomAutomation {
  automation_id: string;
  profile_id: string;
  name: string;
  description: string;
  trigger_type: AutomationTriggerType;
  trigger_config: AutomationTriggerConfig;
  steps: AutomationStep[];
  enabled: boolean;
  last_execution_id?: string | null;
  last_execution_status?: string | null;
  last_run_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AutomationPreviewResponse {
  name: string;
  description: string;
  trigger_type: AutomationTriggerType;
  trigger_config: AutomationTriggerConfig;
  steps: AutomationStep[];
  clarification_needed: boolean;
  missing_fields: string[];
  warnings: string[];
  requires_approval: boolean;
}

export interface AutomationExecutionStep {
  step_index: number;
  step_id: string;
  action: string;
  description: string;
  input_summary?: string;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  message?: string | null;
  output_summary?: string | null;
  error?: string | null;
  error_summary?: string | null;
}

export interface AutomationExecutionStatus {
  execution_id: string;
  automation_id: string;
  automation_name: string;
  profile_id: string;
  trigger_source: string;
  trigger_type?: string;
  status: string;
  current_step?: number;
  total_steps: number;
  completed_steps?: number;
  failed_step_index?: number | null;
  cancelled?: boolean;
  duration_ms?: number | null;
  steps: AutomationExecutionStep[];
  pending_confirmation?: Record<string, unknown> | null;
  error?: string | null;
  error_summary?: string | null;
  started_at?: string;
  finished_at?: string | null;
  completed_at?: string | null;
}

export interface AutomationExecutionHistoryItem {
  execution_id: string;
  automation_id: string;
  profile_id: string;
  automation_name: string;
  trigger_type: string;
  status: string;
  total_steps: number;
  completed_steps: number;
  failed_step_index?: number | null;
  cancelled: boolean;
  error_summary?: string | null;
  duration_ms?: number | null;
  started_at: string;
  completed_at?: string | null;
}

export interface AutomationExecutionHistoryListResponse {
  items: AutomationExecutionHistoryItem[];
  total: number;
  limit: number;
  offset: number;
}

// ============================================================================
// Three-Mode AI Provider & BYOK Types (Phases 8A–8B)
// ============================================================================

export type AIProviderId = 'local' | 'byok' | 'voxpilot_cloud';
export type BYOKVendorId = 'openai' | 'anthropic' | 'google';

export interface BYOKVendorStatus {
  vendor: BYOKVendorId;
  display_name: string;
  configured: boolean;
  is_active: boolean;
  selected_model: string;
  capabilities: AIProviderCapabilities;
  models: AIProviderModelInfo[];
}

export interface AIProviderCapabilities {
  streaming: boolean;
  structured_output: boolean;
  tool_calling: boolean;
  vision: boolean;
  local_only: boolean;
}

export interface AIProviderHealth {
  provider_id: AIProviderId | string;
  available: boolean;
  configured: boolean;
  status: string;
  reason?: string | null;
  active_model?: string | null;
  latency_ms?: number | null;
}

export interface AIProviderModelInfo {
  id: string;
  name: string;
  context_length?: number | null;
  supports_tools: boolean;
  supports_vision: boolean;
}

export interface AIProviderSummary {
  provider_id: AIProviderId | string;
  display_name: string;
  is_active: boolean;
  capabilities: AIProviderCapabilities;
  health: AIProviderHealth;
  models: AIProviderModelInfo[];
}

export interface ProfileProviderConfig {
  profile_id: string;
  active_provider_id: AIProviderId | string;
  local_model: string;
  byok_vendor?: string | null;
  byok_model?: string | null;
  has_byok_credential: boolean;
  cloud_tier?: string | null;
  updated_at?: string | null;
}

export interface ProvidersListResponse {
  providers: AIProviderSummary[];
  byok_models?: AIProviderModelInfo[];
  byok_vendors?: BYOKVendorStatus[];
}

export interface BYOKConfigureResponse {
  profile_config: ProfileProviderConfig;
  vendor: BYOKVendorId | string;
  health: AIProviderHealth;
  validated: boolean;
}

export interface BYOKValidateResponse {
  provider_id: string;
  vendor: BYOKVendorId | string;
  valid: boolean;
  health: AIProviderHealth;
}

export interface ActiveProviderResponse {
  profile_config: ProfileProviderConfig;
  health: AIProviderHealth;
  capabilities: AIProviderCapabilities;
}

export interface OpenAIBYOKConfigureResponse {
  profile_config: ProfileProviderConfig;
  health: AIProviderHealth;
  validated: boolean;
}

export interface OpenAIBYOKValidateResponse {
  provider_id: string;
  vendor: string;
  valid: boolean;
  health: AIProviderHealth;
}
