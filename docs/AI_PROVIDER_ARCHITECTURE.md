# VoxPilot Three-Mode AI Provider Architecture (Phase 8A)

## 1. Overview

VoxPilot supports a three-mode AI provider architecture designed around explicit user control, profile isolation, and local-first privacy:

1. **`local`** (`LocalOllamaProvider`): Wraps the existing local Ollama runtime (`OllamaLLM`, `ModelManager`, `HardwareMonitor`, default `qwen3:4b`). This remains the default active provider.
2. **`byok`** (`BYOKProvider`): Bring Your Own Key contract for future external vendor integrations (`openai`, `anthropic`, `google`, `openai_compatible`). Explicitly reports `not_configured` / `available=False` in Phase 8A.
3. **`voxpilot_cloud`** (`VoxPilotCloudProvider`): Managed VoxPilot Cloud contract. Explicitly reports `unavailable` / `available=False` in Phase 8A until a real cloud service contract exists.

No automatic fallback or silent provider switching is performed.

## 2. Provider Interface & Registry

- **Contract (`backend/ai/providers/base.py`):** Defines `AIProvider`, `ProviderCapabilities`, `ProviderHealth`, `ProviderModelInfo`, `AIProviderRequest`, `AIProviderResponse`, normalized error types (`AIProviderError`, `ProviderUnavailableError`, `ProviderNotConfiguredError`, `ProviderAuthenticationError`, `ProviderTimeoutError`, `ProviderCancelledError`, `ProviderConfigurationError`), and `CredentialStoreInterface`.
- **Registry (`backend/ai/providers/registry.py`):** `AIProviderRegistry` registers providers, resolves profile-specific configurations, validates non-secret configs, checks health, and blocks selection of unavailable providers.

## 3. Database & Credential Security

- **Table `profile_provider_configs` (`backend/database/models.py`):** Stores per-profile non-secret configuration (`active_provider_id`, `local_model`, `byok_vendor`, `byok_model`, `byok_credential_ref`, `cloud_tier`, `config_json`).
- **Idempotent Migration (`backend/database/connection.py`):** Backfills default `local` (`qwen3:4b`) provider configs for existing `profilerecord` rows.
- **Credential Non-Disclosure:** `assert_no_plaintext_secrets()` rejects raw API keys, tokens, or passwords in any configuration payload. Future Phase 8B BYOK credentials must use OS-backed encryption (`CredentialStoreInterface`, e.g., Windows DPAPI) referenced solely by opaque `byok_credential_ref`.

## 4. API Endpoints (`backend/api/providers.py`)

- `GET /api/providers` — Lists registered providers, capabilities, health, and available models.
- `GET /api/providers/active` — Returns the active profile's provider config, health, and capabilities.
- `POST /api/providers/validate` — Validates a non-secret provider configuration.
- `POST /api/providers/select` — Selects an available provider for the active profile (rejects unavailable providers).

## 5. Phase 8B Migration Plan for Direct Ollama Callers

The following modules currently invoke `ollama.chat` directly and are preserved intact in Phase 8A:
- `backend/agent/llm.py` (`OllamaLLM._call_ollama_chat`)
- `backend/agent/intent.py`
- `backend/agent/planner.py`
- `backend/ai/file_assistant.py`
- `backend/ai/browser_research.py`
- `backend/ai/email_assistant.py`
- `backend/ai/vision.py` (`OllamaVisionProvider`)

In Phase 8B+, these callers can route text generation through `get_shared_provider_registry().get_active_provider().generate(...)` once encrypted BYOK credential storage and vendor adapters are implemented.

## Phase 8B: Encrypted Credential Storage & OpenAI BYOK Adapter

### 1. Windows DPAPI Credential Storage (`backend/ai/providers/credentials.py`)
- `WindowsDPAPIProviderCredentialStore` implements `CredentialStoreInterface` by reusing `DPAPICredentialStore.protect_secret` / `unprotect_secret` (`crypt32.CryptProtectData` / `CryptUnprotectData`).
- Encrypted blobs are stored outside SQLite and outside ordinary provider config JSON at `<user_data_dir>/credentials/byok_<profile_id>_<vendor>.dpapi.json`.
- `ProfileProviderConfigRecord` stores only an opaque `byok_credential_ref` (`byok:<profile_id>:openai`) and never plaintext secrets.
- Cross-profile credential access is denied by verifying profile ownership on every lookup/delete.
- No silent plaintext fallback is permitted if DPAPI encryption/decryption fails.

### 2. OpenAI BYOK Adapter (`backend/ai/providers/openai_adapter.py`)
- `OpenAIBYOKAdapter` uses `httpx.Client` against `https://api.openai.com/v1` (`/models` for explicit validation and `/chat/completions` for single-turn generation).
- Normalizes HTTP 401/403 (`ProviderAuthenticationError`), 429 quota/rate limits (`ProviderUnavailableError`), 400/404 (`ProviderConfigurationError`), timeouts (`ProviderTimeoutError`), and EmergencyStop cancellation (`ProviderCancelledError`).
- Redacts API keys and `Bearer` tokens from all error messages and logs via `sanitize_secret`.

### 3. Safe Selection & API Endpoints (`backend/api/providers.py`)
- Saving/validating an OpenAI API key (`POST /api/providers/byok/openai/configure`) **never** auto-activates BYOK; Local AI (`local` / `qwen3:4b`) remains active until explicitly switched via `POST /api/providers/select`.
- Additional endpoints: `POST /api/providers/byok/openai/validate`, `POST /api/providers/byok/openai/model`, and `DELETE /api/providers/byok/openai/credentials`.
- `ProviderSettingsCard` in `SettingsPage` provides masked key input, validation, model selection, provider switching, and confirmed key removal without persisting secrets in browser storage.

## Phase 8C: Unified Runtime AI Provider Integration

### 1. Runtime Provider Resolution (`backend/ai/providers/runtime.py`)
- `resolve_runtime_provider(profile_id)` and `run_provider_chat(...)` resolve the active profile's selected provider from `AIProviderRegistry` at request time.
- **No silent fallback:** If BYOK OpenAI fails (401/403, 429 rate limit/quota, timeout, network error) or lacks a required capability, VoxPilot surfaces an explicit error instead of silently falling back to Local AI.
- **Local AI remains default:** Profiles default to `local` (`qwen3:4b`) and preserve `ModelManager.track_inference(...)` and `EmergencyStopManager.is_emergency_stop_active()`.

### 2. Integrated Workflows & Capability Differences
| Workflow | Module | Local AI (`ollama`) | BYOK OpenAI (`httpx`) | Notes |
|---|---|---|---|---|
| Main Agent Response | `backend/agent/llm.py`, `graph.py` | Supported (`qwen3:4b`) | Supported (`gpt-4o-mini`, etc.) | Preserves system prompt, memories, and recent turns |
| Intent Classification (LLM fallback) | `backend/agent/intent.py` | Supported (`format="json"`) | Supported (`response_format={"type":"json_object"}`) | `structured_output=True` |
| Task Planning | `backend/agent/planner.py` | Supported (`format="json"`) | Supported (`response_format={"type":"json_object"}`) | `structured_output=True`; tool execution stays local via `ToolExecutor` |
| File Assistant | `backend/ai/file_assistant.py` | Supported | Supported (only when BYOK active) | Preserves path containment and truncation notices |
| Browser Research | `backend/ai/browser_research.py` | Supported | Supported (only when BYOK active) | Preserves SSRF & prompt-injection protections |
| Email Assistant | `backend/ai/email_assistant.py` | Supported | Supported (only when BYOK active) | Preserves DPAPI IMAP/SMTP isolation & send confirmation |
| Vision / Screen Analysis | `backend/ai/vision.py` | Supported if local vision model installed | **Blocked (`unsupported_capability`)** | `vision=False` on OpenAI BYOK; never sends images to text-only adapter |
| Voice STT / TTS | `backend/voice/voice_pipeline.py` | Local Faster-Whisper & Piper | Reasoning uses active provider; STT/TTS stay local | Mic audio is never sent externally |

### 3. Remaining Direct Ollama Call Sites
- `backend/agent/llm.py` (`OllamaLLM._call_ollama_chat`): pure local Ollama transport invoked by `LocalOllamaProvider` and `local_executor`.
- `backend/ai/vision.py` (`VisionService.analyze_image`): local multimodal `images=[...]` call for local vision models (`moondream`, `qwen2.5vl:3b`) when Local AI is active.
- `backend/voice/voice_pipeline.py` (`ollama.list()`): only invoked during initialization when `active_provider_id == "local"`.
- `backend/ai/hardware_monitor.py` & `backend/ai/model_manager.py`: local Ollama hardware/model lifecycle management.
