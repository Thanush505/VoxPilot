"""
VoxPilot - Managed Voice Mode Service & Persistent Configuration
Integrates the existing VoicePipeline (AudioRecorder, StreamingVAD, SpeechToText,
TextToSpeech, VoxPilotAgent) with the FastAPI backend, React dashboard, and
customizable Windows global hotkey listener.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Union

import numpy as np
import sounddevice as sd

from backend.automation.emergency_stop import get_shared_emergency_stop_manager
from backend.paths import (
    get_bundle_dir,
    get_config_dir,
    get_executable_dir,
    get_piper_model_path,
    get_project_root,
    get_voice_temp_dir,
    is_frozen,
)
from backend.voice.audio_recorder import AudioRecorder, AudioRecorderError
from backend.voice.hotkey import (
    DEFAULT_VOICE_SHORTCUT,
    ReservedShortcutError,
    ShortcutConflictError,
    ShortcutValidationError,
    VoiceHotkeyListener,
    parse_shortcut,
)
from backend.voice.speech_to_text import SpeechToTextError
from backend.voice.text_to_speech import TextToSpeech, TextToSpeechError
from backend.voice.voice_pipeline import VoicePipeline

logger = logging.getLogger("voxpilot.voice.service")

VOICE_SETTINGS_FILENAME = "voice_settings.json"

PHASE_IDLE = "idle"
PHASE_STARTING = "starting"
PHASE_LISTENING = "listening"
PHASE_TRANSCRIBING = "transcribing"
PHASE_PROCESSING = "processing"
PHASE_PLANNING = "planning"
PHASE_EXECUTING = "executing"
PHASE_SPEAKING = "speaking"
PHASE_INTERRUPTED = "interrupted"
PHASE_ERROR = "error"


def list_available_piper_voices() -> List[Dict[str, Any]]:
    """Discover all installed Piper ONNX voice models across standard model directories."""
    search_dirs: List[Path] = []
    if is_frozen():
        search_dirs.append(get_bundle_dir() / "models" / "piper")
        search_dirs.append(get_executable_dir() / "models" / "piper")
    search_dirs.append(get_project_root() / "models" / "piper")

    custom_model = os.environ.get("VOXPILOT_PIPER_MODEL", "").strip()
    if custom_model:
        custom_path = Path(custom_model)
        if custom_path.parent.exists():
            search_dirs.insert(0, custom_path.parent)

    seen_names = set()
    voices: List[Dict[str, Any]] = []

    for d in search_dirs:
        if not d.exists() or not d.is_dir():
            continue
        for onnx_file in sorted(d.glob("*.onnx")):
            if onnx_file.name in seen_names:
                continue
            seen_names.add(onnx_file.name)
            json_config = onnx_file.with_name(f"{onnx_file.name}.json")
            voices.append({
                "id": onnx_file.name,
                "name": onnx_file.stem,
                "path": str(onnx_file.resolve()),
                "has_config": json_config.exists(),
            })

    # Always include the default model entry even if not yet downloaded
    if not voices:
        default_path = get_piper_model_path()
        voices.append({
            "id": default_path.name,
            "name": default_path.stem,
            "path": str(default_path),
            "has_config": default_path.with_name(f"{default_path.name}.json").exists(),
        })

    return voices


def resolve_piper_voice_path(voice_id_or_path: Optional[str]) -> Path:
    """Resolve a Piper voice model filename or path to its full filesystem Path."""
    if not voice_id_or_path or not str(voice_id_or_path).strip():
        return get_piper_model_path()

    candidate = Path(str(voice_id_or_path).strip())
    if candidate.is_absolute() and candidate.exists():
        return candidate.resolve()

    for voice in list_available_piper_voices():
        if voice["id"] == candidate.name or voice["name"] == candidate.stem:
            return Path(voice["path"])

    return get_piper_model_path()


class VoiceSettingsManager:
    """
    Loads, validates, and persists user voice and shortcut settings in
    %LOCALAPPDATA%\\VoxPilot\\config\\voice_settings.json.
    """

    DEFAULT_SETTINGS: Dict[str, Any] = {
        "shortcut_enabled": True,
        "shortcut": DEFAULT_VOICE_SHORTCUT,
        "microphone_device": None,
        "tts_enabled": True,
        "piper_voice": "en_US-lessac-medium.onnx",
        "streaming_vad_enabled": True,
        "wake_word_enabled": False,
        "wake_word_model": "hey_jarvis_v0.1",
        "wake_word_cooldown_seconds": 2.0,
        "tts_echo_cooldown_seconds": 1.5,
        "floating_orb_enabled": True,
        "floating_orb_visible": True,
        "floating_orb_show_when_idle": False,
        "floating_orb_corner": "bottom_right",
        "floating_orb_x": None,
        "floating_orb_y": None,
        "floating_orb_animations": True,
    }

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._explicit_config_path = config_path
        self._settings: Dict[str, Any] = dict(self.DEFAULT_SETTINGS)
        self.load()

    @property
    def config_path(self) -> Path:
        if self._explicit_config_path is not None:
            self._explicit_config_path.parent.mkdir(parents=True, exist_ok=True)
            return self._explicit_config_path
        return get_config_dir() / VOICE_SETTINGS_FILENAME

    def get_settings(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._settings)

    def load(self) -> Dict[str, Any]:
        """Load voice settings from disk, falling back to safe defaults if missing or invalid."""
        with self._lock:
            merged = dict(self.DEFAULT_SETTINGS)
            path = self.config_path
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        if "shortcut_enabled" in raw:
                            merged["shortcut_enabled"] = bool(raw["shortcut_enabled"])
                        if "shortcut" in raw and isinstance(raw["shortcut"], str):
                            try:
                                canonical, _, _ = parse_shortcut(raw["shortcut"])
                                merged["shortcut"] = canonical
                            except (ShortcutValidationError, ReservedShortcutError) as err:
                                logger.warning(
                                    f"Invalid saved shortcut '{raw.get('shortcut')}' in {path}: {err}. "
                                    f"Falling back to {DEFAULT_VOICE_SHORTCUT}."
                                )
                                merged["shortcut"] = DEFAULT_VOICE_SHORTCUT
                        if "microphone_device" in raw:
                            dev = raw["microphone_device"]
                            if dev is None or dev == "" or dev == "default":
                                merged["microphone_device"] = None
                            elif isinstance(dev, int):
                                merged["microphone_device"] = dev
                            elif isinstance(dev, str):
                                try:
                                    merged["microphone_device"] = int(dev)
                                except ValueError:
                                    merged["microphone_device"] = dev.strip()
                        if "tts_enabled" in raw:
                            merged["tts_enabled"] = bool(raw["tts_enabled"])
                        if "piper_voice" in raw and isinstance(raw["piper_voice"], str) and raw["piper_voice"].strip():
                            merged["piper_voice"] = Path(raw["piper_voice"].strip()).name
                        if "streaming_vad_enabled" in raw:
                            merged["streaming_vad_enabled"] = bool(raw["streaming_vad_enabled"])
                        if "wake_word_enabled" in raw:
                            merged["wake_word_enabled"] = bool(raw["wake_word_enabled"])
                        if "wake_word_model" in raw and isinstance(raw["wake_word_model"], str) and raw["wake_word_model"].strip():
                            merged["wake_word_model"] = raw["wake_word_model"].strip()
                        if "wake_word_cooldown_seconds" in raw:
                            try:
                                merged["wake_word_cooldown_seconds"] = max(0.5, min(30.0, float(raw["wake_word_cooldown_seconds"])))
                            except (TypeError, ValueError):
                                pass
                        if "tts_echo_cooldown_seconds" in raw:
                            try:
                                merged["tts_echo_cooldown_seconds"] = max(0.0, min(10.0, float(raw["tts_echo_cooldown_seconds"])))
                            except (TypeError, ValueError):
                                pass
                        for bool_key in (
                            "floating_orb_enabled",
                            "floating_orb_visible",
                            "floating_orb_show_when_idle",
                            "floating_orb_animations",
                        ):
                            if bool_key in raw:
                                merged[bool_key] = bool(raw[bool_key])
                        if "floating_orb_corner" in raw and isinstance(raw["floating_orb_corner"], str):
                            corner = raw["floating_orb_corner"].strip().lower()
                            if corner in ("top_left", "top_right", "bottom_left", "bottom_right", "custom"):
                                merged["floating_orb_corner"] = corner
                        for coord_key in ("floating_orb_x", "floating_orb_y"):
                            if coord_key in raw:
                                try:
                                    merged[coord_key] = int(raw[coord_key]) if raw[coord_key] is not None else None
                                except (TypeError, ValueError):
                                    merged[coord_key] = None
                except Exception as exc:
                    logger.warning(f"Failed to read voice settings from {path}: {exc}. Using defaults.")

            self._settings = merged
            return dict(self._settings)

    def save(self, updates: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Validate and atomically persist updated voice settings to disk."""
        with self._lock:
            next_settings = dict(self._settings)
            if updates:
                if "shortcut_enabled" in updates and updates["shortcut_enabled"] is not None:
                    next_settings["shortcut_enabled"] = bool(updates["shortcut_enabled"])
                if "shortcut" in updates and updates["shortcut"] is not None:
                    canonical, _, _ = parse_shortcut(str(updates["shortcut"]))
                    next_settings["shortcut"] = canonical
                if "microphone_device" in updates:
                    dev = updates["microphone_device"]
                    if dev is None or dev == "" or str(dev).strip().lower() == "default":
                        next_settings["microphone_device"] = None
                    elif isinstance(dev, int):
                        next_settings["microphone_device"] = dev
                    elif isinstance(dev, str):
                        try:
                            next_settings["microphone_device"] = int(dev.strip())
                        except ValueError:
                            next_settings["microphone_device"] = dev.strip()
                if "tts_enabled" in updates and updates["tts_enabled"] is not None:
                    next_settings["tts_enabled"] = bool(updates["tts_enabled"])
                if "piper_voice" in updates and updates["piper_voice"] is not None:
                    voice_str = str(updates["piper_voice"]).strip()
                    if voice_str and (".." in voice_str or "/" in voice_str or "\\" in voice_str):
                        raise ValueError("Invalid Piper voice identifier.")
                    if voice_str:
                        next_settings["piper_voice"] = voice_str
                if "streaming_vad_enabled" in updates and updates["streaming_vad_enabled"] is not None:
                    next_settings["streaming_vad_enabled"] = bool(updates["streaming_vad_enabled"])
                if "wake_word_enabled" in updates and updates["wake_word_enabled"] is not None:
                    next_settings["wake_word_enabled"] = bool(updates["wake_word_enabled"])
                if "wake_word_model" in updates and updates["wake_word_model"] is not None:
                    model_str = str(updates["wake_word_model"]).strip()
                    if model_str:
                        next_settings["wake_word_model"] = model_str
                if "wake_word_cooldown_seconds" in updates and updates["wake_word_cooldown_seconds"] is not None:
                    next_settings["wake_word_cooldown_seconds"] = max(0.5, min(30.0, float(updates["wake_word_cooldown_seconds"])))
                if "tts_echo_cooldown_seconds" in updates and updates["tts_echo_cooldown_seconds"] is not None:
                    next_settings["tts_echo_cooldown_seconds"] = max(0.0, min(10.0, float(updates["tts_echo_cooldown_seconds"])))
                for bool_key in (
                    "floating_orb_enabled",
                    "floating_orb_visible",
                    "floating_orb_show_when_idle",
                    "floating_orb_animations",
                ):
                    if bool_key in updates and updates[bool_key] is not None:
                        next_settings[bool_key] = bool(updates[bool_key])
                if "floating_orb_corner" in updates and updates["floating_orb_corner"] is not None:
                    corner = str(updates["floating_orb_corner"]).strip().lower()
                    if corner not in ("top_left", "top_right", "bottom_left", "bottom_right", "custom"):
                        raise ValueError(f"Invalid floating orb corner '{corner}'.")
                    next_settings["floating_orb_corner"] = corner
                for coord_key in ("floating_orb_x", "floating_orb_y"):
                    if coord_key in updates:
                        val = updates[coord_key]
                        next_settings[coord_key] = int(val) if val is not None else None

            path = self.config_path
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(".json.tmp")
            temp_path.write_text(json.dumps(next_settings, indent=2), encoding="utf-8")
            temp_path.replace(path)

            self._settings = next_settings
            return dict(self._settings)


class VoiceService:
    """
    Central controller managing interactive Voice Mode, the existing VoicePipeline,
    microphone selection/testing, and the global Windows keyboard shortcut.
    """

    MAX_EVENTS_HISTORY = 100

    def __init__(
        self,
        settings_manager: Optional[VoiceSettingsManager] = None,
        pipeline: Optional[VoicePipeline] = None,
        register_hotkey_on_init: bool = False,
    ) -> None:
        self._lock = threading.RLock()
        self.settings_manager = settings_manager or VoiceSettingsManager()
        cfg = self.settings_manager.get_settings()

        piper_path = resolve_piper_voice_path(cfg.get("piper_voice"))
        if pipeline is not None:
            self.pipeline = pipeline
        else:
            shared_agent = None
            try:
                from backend.api.agent import get_agent
                shared_agent = get_agent()
            except Exception:
                pass
            self.pipeline = VoicePipeline(
                whisper_model="base",
                piper_model=str(piper_path),
                microphone_device=cfg.get("microphone_device"),
                use_streaming_vad=cfg.get("streaming_vad_enabled", True),
                enable_wake_word=False,
                agent=shared_agent,
            )

        # Runtime state
        self._is_active: bool = False
        self._phase: str = PHASE_IDLE
        self._stop_event = threading.Event()
        self._orb_listeners: List[Any] = []
        self._worker_thread: Optional[threading.Thread] = None
        self._active_conversation_id: Optional[str] = None
        self._last_transcript: Optional[str] = None
        self._last_response: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_agent_result: Optional[Dict[str, Any]] = None
        self._last_turn_text: str = ""
        self._last_turn_time: float = 0.0

        # Push-to-talk, wake-word, and interruption runtime state (Phase 5B)
        self._ptt_active: bool = False
        self._ptt_stop_event = threading.Event()
        self._ptt_thread: Optional[threading.Thread] = None
        self._ptt_frames: List[np.ndarray] = []
        self._ptt_conversation_id: Optional[str] = None
        self._interrupted_flag: bool = False
        self._last_tts_finished_at: float = 0.0
        self._last_wake_trigger_at: float = 0.0
        self._wake_stop_event = threading.Event()
        self._wake_thread: Optional[threading.Thread] = None

        # Event buffer for dashboard synchronization
        self._next_event_id: int = 1
        self._events: List[Dict[str, Any]] = []

        # Global Hotkey Listener
        self.hotkey_listener = VoiceHotkeyListener(
            callback=self._on_global_hotkey_pressed,
            shortcut=cfg.get("shortcut", DEFAULT_VOICE_SHORTCUT),
            enabled=cfg.get("shortcut_enabled", True),
        )

        # Subscribe to Emergency Stop so voice mode halts immediately if emergency stop triggers
        try:
            get_shared_emergency_stop_manager().register_cleanup_callback(self._on_emergency_stop_triggered)
        except Exception as err:
            logger.debug(f"Could not register emergency stop callback on VoiceService: {err}")

        if register_hotkey_on_init and cfg.get("shortcut_enabled", True):
            self.start_hotkey_listener()

    def _emit_event(self, event_type: str, **payload: Any) -> Dict[str, Any]:
        """Record a timestamped voice event for frontend polling synchronization."""
        with self._lock:
            event = {
                "event_id": self._next_event_id,
                "type": event_type,
                "phase": self._phase,
                "is_active": self._is_active,
                "conversation_id": self._active_conversation_id,
                "timestamp": time.time(),
                **payload,
            }
            self._next_event_id += 1
            self._events.append(event)
            if len(self._events) > self.MAX_EVENTS_HISTORY:
                self._events = self._events[-self.MAX_EVENTS_HISTORY:]
        if event_type == "voice_error":
            try:
                from backend.automation.notifications import get_shared_notification_service

                err_text = str(payload.get("error") or "Voice subsystem encountered an error.")
                get_shared_notification_service().notify(
                    category="voice_error",
                    title="VoxPilot Voice Error",
                    message=err_text,
                    event_key=f"voice_error:{err_text[:48]}",
                )
            except Exception as notify_err:
                logger.debug("Non-fatal voice_error notification failure: %s", notify_err)
        self._notify_orb()
        return event

    def register_orb_listener(self, callback: Any) -> None:
        """Register a listener notified on voice phase, active-state, or orb-setting changes."""
        with self._lock:
            if callback not in self._orb_listeners:
                self._orb_listeners.append(callback)

    def unregister_orb_listener(self, callback: Any) -> None:
        """Remove a previously registered orb listener."""
        with self._lock:
            self._orb_listeners = [cb for cb in self._orb_listeners if cb is not callback]

    def _notify_orb(self) -> None:
        """Notify registered floating orb listeners of the latest state and settings."""
        with self._lock:
            listeners = list(self._orb_listeners)
            phase = self._phase
            is_active = self._is_active
        settings = self.settings_manager.get_settings()
        for cb in listeners:
            try:
                cb(phase, is_active, settings)
            except Exception as err:
                logger.debug("Floating orb listener notification error: %s", err)

    def start_hotkey_listener(self) -> bool:
        """Start the global Windows hotkey listener if enabled in settings."""
        cfg = self.settings_manager.get_settings()
        if not cfg.get("shortcut_enabled", True):
            return False
        return self.hotkey_listener.start()

    def stop_hotkey_listener(self) -> None:
        """Stop and unregister the global Windows hotkey listener cleanly."""
        self.hotkey_listener.stop()

    def shutdown(self) -> None:
        """Cleanly stop active voice mode, PTT, wake-word, release microphone, and unregister shortcut."""
        self.stop_wake_word_listener()
        self.interrupt_voice(reason="Application shutdown")
        self.stop_voice_mode(reason="Application shutdown")
        self.stop_hotkey_listener()

    def _on_emergency_stop_triggered(self) -> None:
        """Immediate callback when Global Emergency Stop is triggered."""
        logger.warning("Emergency Stop triggered. Aborting active voice operations immediately.")
        self.interrupt_voice(reason="Halted by Emergency Stop")
        if self.is_active:
            self.stop_voice_mode(reason="Halted by Emergency Stop")

    def interrupt_voice(self, reason: str = "Interrupted by user") -> Dict[str, Any]:
        """Immediately stop active speech playback and recording without leaving audio locked."""
        with self._lock:
            self._interrupted_flag = True
            self._ptt_stop_event.set()
            self.pipeline.stop_listening()
            self.pipeline.stop_speaking()
            self.pipeline.recorder.is_recording = False
            was_busy = self._phase in (PHASE_LISTENING, PHASE_TRANSCRIBING, PHASE_PROCESSING, PHASE_SPEAKING) or self._ptt_active
            if was_busy:
                self._phase = PHASE_INTERRUPTED
                self._emit_event("voice_interrupted", reason=reason)
                self._emit_event("phase_changed", phase=PHASE_INTERRUPTED, reason=reason)

        ptt_thread = self._ptt_thread
        if ptt_thread is not None and ptt_thread.is_alive() and ptt_thread is not threading.current_thread():
            ptt_thread.join(timeout=1.5)

        with self._lock:
            self._ptt_active = False
            self._ptt_thread = None
            self._ptt_frames.clear()
            self._last_tts_finished_at = time.monotonic()
            if not self._is_active:
                self._phase = PHASE_IDLE
                self._emit_event("phase_changed", phase=PHASE_IDLE)

        return self.get_status()

    def start_push_to_talk(self, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """Start push-to-talk microphone capture if not already recording."""
        if get_shared_emergency_stop_manager().is_emergency_stop_active():
            return {
                "success": False,
                "status": "cancelled",
                "message": "Cannot start push-to-talk while Emergency Stop is active.",
                "voice_status": self.get_status(),
            }

        with self._lock:
            if self._ptt_active or self._phase == PHASE_LISTENING:
                return {
                    "success": False,
                    "status": "already_recording",
                    "message": "Microphone recording is already active.",
                    "voice_status": self.get_status(),
                }
            self.stop_wake_word_listener()
            self._interrupted_flag = False
            self._ptt_active = True
            self._ptt_frames = []
            self._ptt_stop_event.clear()
            if conversation_id:
                self._ptt_conversation_id = conversation_id
            elif self._active_conversation_id:
                self._ptt_conversation_id = self._active_conversation_id
            self._last_error = None
            self._phase = PHASE_LISTENING
            self._emit_event("phase_changed", phase=PHASE_LISTENING, source="push_to_talk")

        def _ptt_capture_worker() -> None:
            stream = None
            try:
                cfg = self.settings_manager.get_settings()
                dev = AudioRecorder.resolve_device(cfg.get("microphone_device")) if cfg.get("microphone_device") is not None else None
                stream = sd.InputStream(samplerate=16000, channels=1, dtype="float32", device=dev)
                stream.start()
                self.pipeline.recorder.is_recording = True
                while not self._ptt_stop_event.is_set():
                    data, _ = stream.read(1024)
                    if data is not None and len(data) > 0:
                        mono = data.copy().reshape(-1)
                        self._ptt_frames.append(mono)
                        if hasattr(self.pipeline.recorder, "update_live_telemetry"):
                            self.pipeline.recorder.update_live_telemetry(mono, is_speech=True)
            except Exception as err:
                logger.warning("Push-to-talk microphone error: %s", err)
                with self._lock:
                    self._ptt_active = False
                    self._phase = PHASE_ERROR
                    self._last_error = f"Microphone error: {err}"
                    self._emit_event("voice_error", error=self._last_error)
            finally:
                self.pipeline.recorder.is_recording = False
                if hasattr(self.pipeline.recorder, "reset_live_telemetry"):
                    self.pipeline.recorder.reset_live_telemetry()
                if stream is not None:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass

        worker = threading.Thread(target=_ptt_capture_worker, name="VoxPilot-PTT-Capture", daemon=True)
        with self._lock:
            self._ptt_thread = worker
        worker.start()

        return {
            "success": True,
            "status": "listening",
            "voice_status": self.get_status(),
        }

    def stop_push_to_talk(self) -> Dict[str, Any]:
        """Stop push-to-talk recording, transcribe locally, execute agent, and speak response."""
        with self._lock:
            if not self._ptt_active and self._ptt_thread is None:
                return {
                    "success": False,
                    "status": "not_recording",
                    "message": "Push-to-talk is not currently recording.",
                    "voice_status": self.get_status(),
                }
            self._ptt_stop_event.set()
            worker = self._ptt_thread

        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)

        with self._lock:
            self._ptt_active = False
            self._ptt_thread = None
            frames = list(self._ptt_frames)
            self._ptt_frames.clear()
            conv_id = self._ptt_conversation_id or self._active_conversation_id

        if self._interrupted_flag or self._phase == PHASE_ERROR:
            with self._lock:
                if self._phase == PHASE_INTERRUPTED:
                    self._phase = PHASE_IDLE
            return {"success": False, "status": "interrupted", "voice_status": self.get_status()}

        if not frames:
            with self._lock:
                self._phase = PHASE_IDLE
                self._emit_event("phase_changed", phase=PHASE_IDLE)
            return {
                "success": True,
                "status": "empty_audio",
                "message": "No audio was captured.",
                "voice_status": self.get_status(),
            }

        audio = np.concatenate(frames).astype(np.float32)
        rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) > 0 else 0.0
        if len(audio) < 1600 or rms < 0.002:
            del audio
            with self._lock:
                self._phase = PHASE_IDLE
                self._emit_event("phase_changed", phase=PHASE_IDLE)
            return {
                "success": True,
                "status": "empty_audio",
                "message": "Recording was silent or too short.",
                "voice_status": self.get_status(),
            }

        with self._lock:
            self._phase = PHASE_TRANSCRIBING
            self._emit_event("phase_changed", phase=PHASE_TRANSCRIBING)

        try:
            if self.pipeline.stt.model is None:
                self.pipeline.stt.load_model()
            transcript = (self.pipeline.recognize(audio) or "").strip()
        except SpeechToTextError as stt_err:
            with self._lock:
                self._phase = PHASE_ERROR
                self._last_error = f"Speech recognition error: {stt_err}"
                self._emit_event("voice_error", error=self._last_error)
            return {"success": False, "status": "stt_error", "message": self._last_error, "voice_status": self.get_status()}
        finally:
            del audio

        if not transcript:
            with self._lock:
                self._phase = PHASE_IDLE
                self._emit_event("phase_changed", phase=PHASE_IDLE)
            return {"success": True, "status": "empty_transcript", "message": "No speech recognized.", "voice_status": self.get_status()}

        with self._lock:
            self._last_transcript = transcript
            self._phase = PHASE_PROCESSING
            self._emit_event("transcription", text=transcript)
            self._emit_event("phase_changed", phase=PHASE_PROCESSING, text=transcript)

        try:
            reply = self.pipeline.generate_response(transcript, conversation_id=conv_id)
            agent_result = self.pipeline.last_agent_result or {}
        except Exception as err:
            reply = "Sorry, I encountered an error while processing your voice request."
            agent_result = {"execution_status": "failed", "response": reply, "error": str(err)}

        with self._lock:
            self._last_response = reply
            self._last_agent_result = agent_result
            if agent_result.get("conversation_id"):
                self._active_conversation_id = agent_result["conversation_id"]
        self._emit_event("agent_response", user_input=transcript, response=reply, execution=agent_result)

        cfg = self.settings_manager.get_settings()
        if cfg.get("tts_enabled", True) and reply and not self._interrupted_flag and not get_shared_emergency_stop_manager().is_emergency_stop_active():
            with self._lock:
                self._phase = PHASE_SPEAKING
                self._emit_event("phase_changed", phase=PHASE_SPEAKING, response=reply)
            self._speak_safe(reply)

        with self._lock:
            if self._phase != PHASE_ERROR:
                self._phase = PHASE_IDLE
                self._emit_event("phase_changed", phase=PHASE_IDLE)

        return {
            "success": True,
            "status": "completed",
            "transcript": transcript,
            "response": reply,
            "voice_status": self.get_status(),
        }

    def _should_suppress_wake_trigger(self) -> bool:
        """Prevent duplicate wake-word triggers, concurrent streams, and TTS echo self-activation."""
        if get_shared_emergency_stop_manager().is_emergency_stop_active():
            return True
        if self._is_active or self._ptt_active:
            return True
        if self._phase in (PHASE_LISTENING, PHASE_TRANSCRIBING, PHASE_PROCESSING, PHASE_SPEAKING):
            return True
        cfg = self.settings_manager.get_settings()
        now = time.monotonic()
        echo_cooldown = float(cfg.get("tts_echo_cooldown_seconds", 1.5))
        wake_cooldown = float(cfg.get("wake_word_cooldown_seconds", 2.0))
        if (now - self._last_tts_finished_at) < echo_cooldown:
            return True
        if (now - self._last_wake_trigger_at) < wake_cooldown:
            return True
        return False

    def _handle_wake_word_detection(self, event: Optional[Dict[str, Any]]) -> bool:
        """Handle a wake-word detection event safely with cooldown and echo suppression."""
        if not event or self._should_suppress_wake_trigger():
            return False
        with self._lock:
            self._last_wake_trigger_at = time.monotonic()
        self.stop_wake_word_listener()
        self._emit_event("wake_word_detected", model=event.get("model"), score=event.get("score"))
        self.start_voice_mode(source="wake_word")
        return True

    def start_wake_word_listener(self) -> bool:
        """Start optional background wake-word listener if enabled in settings."""
        cfg = self.settings_manager.get_settings()
        if not cfg.get("wake_word_enabled", False):
            return False
        if self._wake_thread is not None and self._wake_thread.is_alive():
            return True
        self._wake_stop_event.clear()

        def _wake_worker() -> None:
            try:
                from backend.voice.wake_word import WakeWordDetector, WakeWordError
                detector = WakeWordDetector(
                    enabled=True,
                    model_path=cfg.get("wake_word_model", "hey_jarvis_v0.1"),
                    cooldown_seconds=float(cfg.get("wake_word_cooldown_seconds", 2.0)),
                    device=cfg.get("microphone_device"),
                )
                detector.load_model()
                while not self._wake_stop_event.is_set():
                    if self._should_suppress_wake_trigger():
                        time.sleep(0.2)
                        continue
                    detection = detector.listen_for_wake_word(timeout=1.0)
                    if detection and not self._wake_stop_event.is_set():
                        self._handle_wake_word_detection(detection)
                        break
            except Exception as err:
                logger.warning("Wake-word listener stopped: %s", err)
                with self._lock:
                    self._last_error = f"Wake-word warning: {err}"

        self._wake_thread = threading.Thread(target=_wake_worker, name="VoxPilot-WakeWord", daemon=True)
        self._wake_thread.start()
        return True

    def stop_wake_word_listener(self) -> None:
        """Stop the background wake-word listener and release its microphone stream."""
        self._wake_stop_event.set()
        t = self._wake_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.5)
        self._wake_thread = None

    def _on_global_hotkey_pressed(self, shortcut_name: str) -> None:
        """Toggle Voice Mode on/off when the global Windows shortcut is pressed."""
        logger.info(f"Global shortcut '{shortcut_name}' pressed -> toggling Voice Mode.")
        self.toggle_voice_mode(source=f"hotkey:{shortcut_name}")

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._is_active

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    @property
    def last_transcript(self) -> Optional[str]:
        with self._lock:
            return self._last_transcript

    @property
    def last_response(self) -> Optional[str]:
        with self._lock:
            return self._last_response

    def toggle_voice_mode(
        self,
        conversation_id: Optional[str] = None,
        source: str = "ui",
    ) -> Dict[str, Any]:
        """Toggle voice mode on if currently off, or off if currently active."""
        with self._lock:
            currently_active = self._is_active

        if currently_active:
            return self.stop_voice_mode(reason=f"Stopped via {source}")
        return self.start_voice_mode(conversation_id=conversation_id, source=source)

    def start_voice_mode(
        self,
        conversation_id: Optional[str] = None,
        source: str = "ui",
    ) -> Dict[str, Any]:
        """
        Activate Voice Mode and start the background multi-turn voice interaction worker.
        Does not allow starting if Global Emergency Stop is currently latched.
        """
        em_manager = get_shared_emergency_stop_manager()
        if em_manager.is_emergency_stop_active():
            with self._lock:
                self._is_active = False
                self._phase = PHASE_ERROR
                self._last_error = (
                    "Cannot start Voice Mode while Global Emergency Stop is active. "
                    "Please reset Emergency Stop first."
                )
                self._emit_event("voice_error", error=self._last_error, source=source)
            return self.get_status()

        # Stop any wake-word background listener before opening full Voice Mode
        # so two microphone capture streams never contend for audio hardware.
        self.stop_wake_word_listener()

        stale_worker: Optional[threading.Thread] = None
        with self._lock:
            if self._is_active and self._worker_thread is not None and self._worker_thread.is_alive():
                if conversation_id:
                    self._active_conversation_id = conversation_id
                return self.get_status()
            if self._worker_thread is not None and self._worker_thread.is_alive():
                self._stop_event.set()
                self.pipeline.stop_listening()
                self.pipeline.stop_speaking()
                stale_worker = self._worker_thread

        if (
            stale_worker is not None
            and stale_worker.is_alive()
            and stale_worker is not threading.current_thread()
        ):
            stale_worker.join(timeout=1.5)

        with self._lock:
            if self._is_active and self._worker_thread is not None and self._worker_thread.is_alive():
                if conversation_id:
                    self._active_conversation_id = conversation_id
                return self.get_status()

            # Sync latest settings into pipeline before starting
            cfg = self.settings_manager.get_settings()
            self.pipeline.recorder.device = cfg.get("microphone_device")
            self.pipeline.recorder.streaming_vad_enabled = cfg.get("streaming_vad_enabled", True)
            self.pipeline.use_streaming_vad = cfg.get("streaming_vad_enabled", True)

            # Verify microphone availability upfront so missing mic fails fast and clearly
            try:
                self.pipeline.recorder.resolve_device(self.pipeline.recorder.device)
            except AudioRecorderError as mic_err:
                self._is_active = False
                self._phase = PHASE_ERROR
                self._last_error = str(mic_err)
                self._emit_event("voice_error", error=self._last_error, source=source)
                return self.get_status()

            if conversation_id is not None:
                self._active_conversation_id = conversation_id
            elif not self._active_conversation_id:
                self._active_conversation_id = self.pipeline.current_conversation_id

            self._stop_event.clear()
            self._is_active = True
            self._phase = PHASE_STARTING
            self._last_error = None
            self._emit_event("voice_started", source=source, phase=PHASE_STARTING)

            self._worker_thread = threading.Thread(
                target=self._voice_interaction_loop,
                name="VoxPilot-VoiceModeWorker",
                daemon=True,
            )
            self._worker_thread.start()

        return self.get_status()

    def stop_voice_mode(self, reason: str = "Stopped by user") -> Dict[str, Any]:
        """
        Immediately cancel any active recording or speech synthesis, release the microphone,
        and exit Voice Mode.
        """
        with self._lock:
            was_active = self._is_active
            self._stop_event.set()
            self._is_active = False
            self.pipeline.stop_listening()
            self.pipeline.stop_speaking()
            worker = self._worker_thread

        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=2.0)

        with self._lock:
            self._worker_thread = None
            self.pipeline.recorder.is_recording = False
            if self._phase != PHASE_ERROR:
                self._phase = PHASE_IDLE
            if was_active:
                self._emit_event("voice_stopped", reason=reason)

        return self.get_status()

    def _voice_interaction_loop(self) -> None:
        """
        Continuous background voice loop while Voice Mode remains active:
        Microphone -> StreamingVAD -> Faster-Whisper STT -> VoxPilotAgent -> Piper TTS.
        """
        logger.info("Voice Mode worker loop started.")
        try:
            while not self._stop_event.is_set():
                # 1. Check Emergency Stop before listening
                if get_shared_emergency_stop_manager().is_emergency_stop_active():
                    with self._lock:
                        self._is_active = False
                        self._phase = PHASE_IDLE
                        self._last_error = "Voice Mode halted by Emergency Stop."
                        self._emit_event("voice_stopped", reason=self._last_error)
                    break

                def _mark_capture_active() -> None:
                    with self._lock:
                        if not self._is_active or self._stop_event.is_set():
                            return
                        if self._phase != PHASE_LISTENING:
                            self._phase = PHASE_LISTENING
                            self._emit_event("phase_changed", phase=PHASE_LISTENING)

                # 2. Capture speech from microphone (releases microphone immediately when done or cancelled)
                try:
                    listen_fn = self.pipeline.listen
                    listen_sig = inspect.signature(listen_fn)
                    side_effect_fn = getattr(listen_fn, "side_effect", None)
                    supports_capture_cb = "on_capture_active" in listen_sig.parameters
                    if not supports_capture_cb and callable(side_effect_fn):
                        try:
                            supports_capture_cb = "on_capture_active" in inspect.signature(side_effect_fn).parameters
                        except (TypeError, ValueError):
                            supports_capture_cb = False
                    if supports_capture_cb:
                        audio = listen_fn(
                            stop_event=self._stop_event,
                            on_capture_active=_mark_capture_active,
                        )
                    else:
                        _mark_capture_active()
                        audio = listen_fn(stop_event=self._stop_event)
                except AudioRecorderError as rec_err:
                    if self._stop_event.is_set():
                        break
                    logger.error(f"Microphone capture error in Voice Mode: {rec_err}")
                    with self._lock:
                        self._is_active = False
                        self._phase = PHASE_ERROR
                        self._last_error = str(rec_err)
                        self._emit_event("voice_error", error=self._last_error)
                    break
                except Exception as unexpected_rec_err:
                    if self._stop_event.is_set():
                        break
                    logger.error(f"Unexpected capture error: {unexpected_rec_err}", exc_info=True)
                    with self._lock:
                        self._is_active = False
                        self._phase = PHASE_ERROR
                        self._last_error = f"Microphone error: {unexpected_rec_err}"
                        self._emit_event("voice_error", error=self._last_error)
                    break

                if self._stop_event.is_set() or not self.is_active:
                    break

                # If silence timeout or empty audio, continue listening while voice mode stays active
                if audio is None or len(audio) == 0:
                    continue

                # 3. Transcribe speech via Faster-Whisper
                with self._lock:
                    if self._stop_event.is_set() or not self._is_active:
                        break
                    self._phase = PHASE_TRANSCRIBING
                    self._emit_event("phase_changed", phase=PHASE_TRANSCRIBING)

                try:
                    if self.pipeline.stt.model is None:
                        self.pipeline.stt.load_model()
                    recognized_text = self.pipeline.recognize(audio)
                except SpeechToTextError as stt_err:
                    logger.error(f"Speech recognition failed: {stt_err}")
                    with self._lock:
                        self._phase = PHASE_ERROR
                        self._last_error = f"Speech recognition error: {stt_err}"
                        self._is_active = False
                        self._emit_event("voice_error", error=self._last_error)
                    break
                finally:
                    # Explicitly release raw audio buffer from memory immediately after transcription
                    del audio

                if self._stop_event.is_set() or not self.is_active:
                    break

                cleaned_text = (recognized_text or "").strip()
                if not cleaned_text:
                    continue

                # Deduplicate rapid identical audio transcripts within 1.5s
                now_mono = time.monotonic()
                with self._lock:
                    if (
                        cleaned_text.lower() == self._last_turn_text.lower()
                        and (now_mono - self._last_turn_time) < 1.5
                    ):
                        logger.info(f"Skipping duplicate voice transcript within debounce window: '{cleaned_text}'")
                        continue
                    self._last_turn_text = cleaned_text
                    self._last_turn_time = now_mono
                    self._last_transcript = cleaned_text
                    conv_id = self._active_conversation_id

                # Check exit command ("exit", "quit", "stop listening", "goodbye")
                if self.pipeline.is_exit_command(cleaned_text):
                    self._emit_event(
                        "transcription",
                        text=cleaned_text,
                        is_exit_command=True,
                    )
                    cfg = self.settings_manager.get_settings()
                    exit_reply = "Voice mode deactivated."
                    with self._lock:
                        self._last_response = exit_reply
                    self._emit_event(
                        "agent_response",
                        user_input=cleaned_text,
                        response=exit_reply,
                        execution=None,
                    )
                    if cfg.get("tts_enabled", True) and not self._stop_event.is_set():
                        self._speak_safe(exit_reply)
                    with self._lock:
                        self._is_active = False
                        self._phase = PHASE_IDLE
                        self._emit_event("voice_stopped", reason="Exit voice command spoken")
                    break

                # Emit transcription event so UI immediately displays user's spoken request
                self._emit_event("transcription", text=cleaned_text)

                # 4. Process transcribed text through VoxPilotAgent
                with self._lock:
                    if self._stop_event.is_set() or not self._is_active:
                        break
                    self._phase = PHASE_PROCESSING
                    self._emit_event("phase_changed", phase=PHASE_PROCESSING, text=cleaned_text)

                try:
                    response_text = self.pipeline.generate_response(
                        cleaned_text,
                        conversation_id=conv_id,
                    )
                    agent_result = self.pipeline.last_agent_result or {}
                    exec_id = agent_result.get("execution_id")
                    if exec_id:
                        try:
                            from backend.api.agent import save_execution
                            save_execution(exec_id, agent_result)
                        except Exception:
                            pass
                except Exception as agent_err:
                    logger.error(f"Agent execution error in Voice Mode: {agent_err}", exc_info=True)
                    response_text = "Sorry, I encountered an error while processing your voice request."
                    agent_result = {
                        "execution_status": "failed",
                        "user_input": cleaned_text,
                        "response": response_text,
                        "error": str(agent_err),
                    }

                with self._lock:
                    if agent_result.get("conversation_id"):
                        self._active_conversation_id = agent_result["conversation_id"]
                        self.pipeline.current_conversation_id = agent_result["conversation_id"]
                    self._last_response = response_text
                    self._last_agent_result = agent_result

                self._emit_event(
                    "agent_response",
                    user_input=cleaned_text,
                    response=response_text,
                    execution=agent_result,
                )

                if self._stop_event.is_set() or not self.is_active:
                    break

                if get_shared_emergency_stop_manager().is_emergency_stop_active():
                    with self._lock:
                        self._is_active = False
                        self._phase = PHASE_IDLE
                        self._emit_event("voice_stopped", reason="Halted by Emergency Stop")
                    break

                # 5. Speak response through Piper TTS if enabled
                cfg = self.settings_manager.get_settings()
                if cfg.get("tts_enabled", True) and response_text and not self._stop_event.is_set():
                    with self._lock:
                        self._phase = PHASE_SPEAKING
                        self._emit_event("phase_changed", phase=PHASE_SPEAKING, response=response_text)
                    self._speak_safe(response_text)

        finally:
            self.pipeline.recorder.is_recording = False
            with self._lock:
                if not self._is_active and self._phase not in (PHASE_IDLE, PHASE_ERROR):
                    self._phase = PHASE_IDLE
            logger.info("Voice Mode worker loop exited.")

    def _speak_safe(self, text: str) -> None:
        """Synthesize and play speech through Piper TTS, handling cancellation or model errors gracefully."""
        if not text or not text.strip() or self._stop_event.is_set() or self._interrupted_flag:
            return
        try:
            cfg = self.settings_manager.get_settings()
            desired_model = resolve_piper_voice_path(cfg.get("piper_voice"))
            if self.pipeline.tts.model_path != desired_model:
                self.pipeline.tts = TextToSpeech(model_path=str(desired_model))
            if self.pipeline.tts.voice is None:
                self.pipeline.tts.load_model()
            self.pipeline.speak(text, cleanup_temp_file=True)
        except TextToSpeechError as tts_err:
            logger.warning(f"Piper TTS error during voice playback: {tts_err}")
            with self._lock:
                self._last_error = f"TTS warning: {tts_err}"
        except Exception as err:
            logger.warning(f"Audio playback warning: {err}")
        finally:
            with self._lock:
                self._last_tts_finished_at = time.monotonic()

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update persistent voice settings, synchronize hardware/pipeline parameters,
        and apply global shortcut changes if included.
        """
        with self._lock:
            # Handle shortcut or shortcut_enabled updates through hotkey_listener first
            if "shortcut" in updates and updates["shortcut"] is not None:
                target_enabled = updates.get(
                    "shortcut_enabled",
                    self.settings_manager.get_settings().get("shortcut_enabled", True),
                )
                canonical = self.hotkey_listener.update_shortcut(
                    str(updates["shortcut"]),
                    enabled=bool(target_enabled),
                )
                updates = dict(updates)
                updates["shortcut"] = canonical
            elif "shortcut_enabled" in updates and updates["shortcut_enabled"] is not None:
                self.hotkey_listener.set_enabled(bool(updates["shortcut_enabled"]))

            # Validate microphone device if specified
            if "microphone_device" in updates and updates["microphone_device"] not in (None, "", "default"):
                AudioRecorder.resolve_device(updates["microphone_device"])

            saved = self.settings_manager.save(updates)

            # Sync pipeline settings
            self.pipeline.recorder.device = saved.get("microphone_device")
            self.pipeline.recorder.streaming_vad_enabled = saved.get("streaming_vad_enabled", True)
            self.pipeline.use_streaming_vad = saved.get("streaming_vad_enabled", True)

            piper_path = resolve_piper_voice_path(saved.get("piper_voice"))
            if self.pipeline.tts.model_path != piper_path:
                self.pipeline.tts = TextToSpeech(model_path=str(piper_path))

            if "wake_word_enabled" in updates:
                if saved.get("wake_word_enabled", False):
                    self.start_wake_word_listener()
                else:
                    self.stop_wake_word_listener()

            self._notify_orb()
            return self.get_status()

    def set_phase(self, phase: str, **payload: Any) -> None:
        """Update the current voice phase (e.g. planning, executing) and notify listeners."""
        with self._lock:
            if not self._is_active and phase not in (PHASE_IDLE, PHASE_ERROR):
                return
            self._phase = phase
            self._emit_event("phase_changed", phase=phase, **payload)

    def update_shortcut(self, shortcut: str, enabled: Optional[bool] = None) -> Dict[str, Any]:
        """
        Validate, register, and persist a new global keyboard shortcut.
        If registration fails due to conflict, restores the previous shortcut and raises ShortcutConflictError.
        """
        with self._lock:
            current_cfg = self.settings_manager.get_settings()
            target_enabled = current_cfg.get("shortcut_enabled", True) if enabled is None else bool(enabled)

            canonical = self.hotkey_listener.update_shortcut(shortcut, enabled=target_enabled)
            self.settings_manager.save({
                "shortcut": canonical,
                "shortcut_enabled": target_enabled,
            })
            return self.get_status()

    def list_microphones(self) -> Dict[str, Any]:
        """List available microphone input devices and currently selected microphone."""
        cfg = self.settings_manager.get_settings()
        configured_dev = cfg.get("microphone_device")
        devices: List[Dict[str, Any]] = []
        selected_info: Optional[Dict[str, Any]] = None
        error_msg: Optional[str] = None

        try:
            devices = AudioRecorder.get_input_devices()
            if devices:
                try:
                    _, selected_info = AudioRecorder.resolve_device(configured_dev)
                except AudioRecorderError as resolve_err:
                    error_msg = str(resolve_err)
                    _, selected_info = AudioRecorder.resolve_device(None)
        except AudioRecorderError as err:
            error_msg = str(err)

        return {
            "devices": devices,
            "configured_device": configured_dev,
            "selected_device": selected_info,
            "error": error_msg,
        }

    def test_microphone(
        self,
        device: Optional[Union[int, str]] = None,
        duration_seconds: float = 0.5,
    ) -> Dict[str, Any]:
        """
        Perform a non-destructive hardware test on the specified or configured microphone.
        Captures a brief in-memory sample to verify device opening and signal level,
        immediately releases the microphone, and never saves raw audio to disk.
        """
        cfg = self.settings_manager.get_settings()
        target_device = cfg.get("microphone_device") if device in (None, "", "default") else device

        test_dur = max(0.1, min(float(duration_seconds), 2.0))
        temp_recorder = AudioRecorder(device=target_device, streaming_vad_enabled=False)

        try:
            dev_idx, dev_info = temp_recorder.resolve_device(target_device)
            rec_sr, rec_channels, _ = temp_recorder._determine_recording_parameters(dev_idx)

            frames = int(test_dur * rec_sr)
            raw_audio = sd.rec(
                frames,
                samplerate=rec_sr,
                channels=rec_channels,
                dtype="float32",
                device=dev_idx,
            )
            sd.wait()

            arr = np.squeeze(raw_audio)
            if arr.ndim > 1:
                arr = np.mean(arr, axis=1)

            rms = float(np.sqrt(np.mean(np.square(arr)))) if len(arr) > 0 else 0.0
            peak = float(np.max(np.abs(arr))) if len(arr) > 0 else 0.0
            del raw_audio
            del arr

            return {
                "success": True,
                "device": dev_info,
                "sample_rate": rec_sr,
                "channels": rec_channels,
                "rms_level": round(rms, 6),
                "peak_level": round(peak, 6),
                "signal_detected": bool(peak > 0.001),
                "message": (
                    f"Microphone [{dev_info['index']}] '{dev_info['name']}' tested successfully "
                    f"({rec_sr} Hz, peak level: {peak:.4f})."
                ),
            }
        except Exception as err:
            return {
                "success": False,
                "device": None,
                "sample_rate": 0,
                "channels": 0,
                "rms_level": 0.0,
                "peak_level": 0.0,
                "signal_detected": False,
                "message": f"Microphone test failed: {err}",
            }

    def get_status(self, since_event_id: int = 0) -> Dict[str, Any]:
        """Return comprehensive voice subsystem state, configuration, and recent events."""
        with self._lock:
            cfg = self.settings_manager.get_settings()
            mic_info = self.list_microphones()
            available_voices = list_available_piper_voices()
            piper_path = resolve_piper_voice_path(cfg.get("piper_voice"))

            hf_hub = Path(os.environ.get("USERPROFILE", "")) / ".cache" / "huggingface" / "hub"
            whisper_cached = bool(
                hf_hub.exists() and list(hf_hub.glob(f"models--Systran--faster-whisper-{self.pipeline.stt.model_size}"))
            )

            events_slice = [
                ev for ev in self._events if ev["event_id"] > since_event_id
            ]

            rec_active = bool(getattr(self.pipeline.recorder, "is_recording", False)) or self._ptt_active
            raw_telemetry = (
                self.pipeline.recorder.get_live_telemetry()
                if hasattr(self.pipeline.recorder, "get_live_telemetry")
                else {
                    "rms_level": float(getattr(self.pipeline.recorder, "current_rms", 0.0)),
                    "peak_level": float(getattr(self.pipeline.recorder, "current_peak", 0.0)),
                    "is_speech": bool(getattr(self.pipeline.recorder, "current_is_speech", False)),
                    "bands": list(getattr(self.pipeline.recorder, "current_bands", [0.0] * 16)),
                }
            )
            telemetry_mode = (
                "microphone"
                if rec_active
                else ("tts" if self._phase == PHASE_SPEAKING else "idle")
            )
            audio_telemetry = {
                "active": bool(rec_active or self._phase == PHASE_SPEAKING),
                "mode": telemetry_mode,
                "rms_level": raw_telemetry["rms_level"] if rec_active else 0.0,
                "peak_level": raw_telemetry["peak_level"] if rec_active else 0.0,
                "is_speech": raw_telemetry["is_speech"] if rec_active else False,
                "bands": raw_telemetry["bands"] if rec_active else [0.0] * 16,
            }

            return {
                "is_active": self._is_active,
                "phase": self._phase,
                "is_recording": rec_active,
                "is_ptt_active": self._ptt_active,
                "audio_telemetry": audio_telemetry,
                "wake_word_enabled": cfg.get("wake_word_enabled", False),
                "conversation_id": self._active_conversation_id,
                "last_transcript": self._last_transcript,
                "last_response": self._last_response,
                "last_error": self._last_error,
                "last_agent_result": self._last_agent_result,
                "settings": cfg,
                "shortcut_status": self.hotkey_listener.get_status(),
                "components": {
                    "microphone": {
                        "available": len(mic_info["devices"]) > 0,
                        "device_count": len(mic_info["devices"]),
                        "selected_device": mic_info["selected_device"],
                        "error": mic_info["error"],
                    },
                    "stt": {
                        "available": True,
                        "engine": "Faster-Whisper",
                        "model_size": self.pipeline.stt.model_size,
                        "device": self.pipeline.stt.device,
                        "compute_type": self.pipeline.stt.compute_type,
                        "loaded": self.pipeline.stt.model is not None,
                        "cached": whisper_cached or (self.pipeline.stt.model is not None),
                    },
                    "tts": {
                        "available": piper_path.exists(),
                        "enabled": cfg.get("tts_enabled", True),
                        "engine": "Piper ONNX",
                        "piper_voice": cfg.get("piper_voice", "en_US-lessac-medium.onnx"),
                        "model_path": str(piper_path),
                        "model_exists": piper_path.exists(),
                        "loaded": self.pipeline.tts.voice is not None,
                        "available_voices": available_voices,
                    },
                    "vad": {
                        "available": True,
                        "enabled": cfg.get("streaming_vad_enabled", True),
                        "engine": "WebRTC + Silero VAD",
                        "aggressiveness": self.pipeline.recorder.aggressiveness or 2,
                        "frame_duration_ms": self.pipeline.recorder.frame_duration_ms or 30,
                        "end_silence_ms": self.pipeline.recorder.end_silence_ms or 900,
                    },
                },
                "events": events_slice,
                "latest_event_id": (self._next_event_id - 1),
            }


# Singleton Instance Management
_SHARED_VOICE_SERVICE: Optional[VoiceService] = None
_SERVICE_LOCK = threading.Lock()


def get_shared_voice_service() -> VoiceService:
    """Retrieve or initialize the singleton VoiceService instance."""
    global _SHARED_VOICE_SERVICE
    with _SERVICE_LOCK:
        if _SHARED_VOICE_SERVICE is None:
            _SHARED_VOICE_SERVICE = VoiceService(register_hotkey_on_init=False)
        return _SHARED_VOICE_SERVICE
