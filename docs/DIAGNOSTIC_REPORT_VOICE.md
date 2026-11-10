# VoxPilot — Phase 2: Voice Recognition Diagnostic Report
**Date:** November 10, 2026  
**Document:** `docs/DIAGNOSTIC_REPORT_VOICE.md`  
**Target System:** ASUS TUF Gaming F16 FX607VJR (Intel Core 5 205H, RTX 3050 6GB VRAM, 16GB DDR5, Windows 11)  
**Status:** Diagnostic Complete — No Source Code Changes Applied

---

## 1. Executive Summary

This diagnostic investigation analyzed the microphone capture and Faster-Whisper speech recognition pipeline to determine the root causes of recognition inaccuracies—such as the observed hallucination:
> *"If you want to read your notes and listen to my color, that's a good session."*

### Key Findings
1. **Device Selection Mismatch & Legacy Audio Driver:** `VoicePipeline` hardcodes `microphone_device=1`, selecting the laptop's built-in **Realtek Microphone Array** over the legacy **MME** driver layer, even though an external **Sony WF-1000XM5** headset microphone is present on the system. Built-in laptop microphones suffer from low signal-to-noise ratio (SNR), room reverberation, and chassis noise.
2. **Low Audio Amplitude & Zero Gain Normalization:** Audio captured in `microphone_test.wav` peaks at only **0.137 (-17.26 dBFS)** with an RMS of **0.0155 (-36.18 dBFS)**. Neither `audio_recorder.py` nor `speech_to_text.py` performs audio normalization or gain boosting, starving Whisper of high-frequency consonant acoustics.
3. **Absence of Voice Activity Detection (VAD):** `speech_to_text.py` invokes `model.transcribe()` without `vad_filter=True`. Feeding multi-second silent or low-energy ambient noise buffers into Whisper causes the autoregressive decoder to hallucinate phantom sentences.
4. **Model Capacity Limitations:** The `base` Whisper model (74M parameters) has limited acoustic robustness under poor SNR conditions. Under low signal levels, it frequently substitutes phonetic homophones and hallucinated filler.

---

## 2. Audio Hardware & Microphone Diagnostics

### 2.1 Available Audio Input Devices
Sounddevice enumeration identified the following input devices:

| Device Index | Device Name | Host API | Default Sample Rate | Channels | Latency (Low / High) | Status |
|---|---|---|---|---|---|---|
| **0** | Microsoft Sound Mapper - Input | MME | 44,100 Hz | 2 | 90ms / 180ms | Windows default virtual mapper |
| **1** *(Active)* | Microphone Array (Realtek(R) Audio) | MME | 44,100 Hz | 2 | 90ms / 180ms | **Hardcoded in `VoicePipeline`** |
| **2** | Headset (WF-1000XM5) | MME | 44,100 Hz | 1 | 90ms / 180ms | Bluetooth Headset (MME) |
| **7** | Primary Sound Capture Driver | DirectSound | 44,100 Hz | 2 | 120ms / 240ms | DirectSound wrapper |
| **8** | Microphone Array (Realtek(R) Audio) | DirectSound | 44,100 Hz | 2 | 120ms / 240ms | Realtek DirectSound |
| **9** | Headset (WF-1000XM5) | DirectSound | 44,100 Hz | 1 | 120ms / 240ms | Bluetooth Headset DirectSound |
| **17** | Headset (WF-1000XM5) | WASAPI | 16,000 Hz | 1 | 3ms / 10ms | **Native 16kHz Bluetooth Voice** |
| **18** | Microphone Array (Realtek(R) Audio) | WASAPI | 48,000 Hz | 2 | 1.3ms / 10ms | Native 48kHz Laptop Array |

### 2.2 Physical Device vs. Configured Device Analysis
- **Configured:** `device=1` in `VoicePipeline(microphone_device=1)`.
- **Target Hardware:** Laptop internal Realtek Microphone Array.
- **Limitation:** If the user wears the WF-1000XM5 headset and speaks at normal conversational levels, the audio is actually being captured by the laptop microphones located several feet away on the desk.
- **Host API:** MME is an outdated audio layer introducing software resampling and latency. WASAPI provides direct hardware access with lower latency and higher fidelity.

---

## 3. Audio Quality Analysis (`microphone_test.wav`)

Detailed waveform analysis on the existing sample recording:

| Metric | Measured Value | Optimal Range | Evaluation |
|---|---|---|---|
| **Duration** | 5.000 seconds | Dynamic / VAD-controlled | Fixed duration captures silence |
| **Sample Rate** | 16,000 Hz | 16,000 Hz | Correct for Whisper |
| **Channels** | 1 (Mono) | 1 (Mono) | Correct |
| **Bit Depth / Format** | PCM_16 (WAV) | 16-bit PCM / float32 | Correct |
| **Peak Amplitude** | 0.137146 (-17.26 dBFS) | 0.70 to 0.95 (-3 to -0.5 dBFS) | **Sub-optimal (very quiet)** |
| **RMS Amplitude** | 0.015519 (-36.18 dBFS) | 0.08 to 0.20 (-22 to -14 dBFS) | **Quiet speech signal** |
| **DC Offset** | 0.000000 | ~0.0000 | Excellent (no DC bias) |
| **Clipping** | 0 samples clipped (0.0%) | 0 samples | Clean (no digital clipping) |

### 3.1 Window-by-Window Temporal Analysis (500ms Windows)
```text
[ 0.0s -  0.5s]  | Peak: 0.0040 | RMS: 0.0006 | -64.6 dBFS  (Ambient noise)
[ 0.5s -  1.0s]  | Peak: 0.0194 | RMS: 0.0020 | -53.9 dBFS  (Ambient noise)
[ 1.0s -  1.5s]  | Peak: 0.0166 | RMS: 0.0019 | -54.5 dBFS  (Ambient noise)
[ 1.5s -  2.0s]  | Peak: 0.0938 | RMS: 0.0260 | -31.7 dBFS  (Speech onset)
[ 2.0s -  2.5s]  | Peak: 0.0710 | RMS: 0.0145 | -36.7 dBFS  (Speech)
[ 2.5s -  3.0s]  | Peak: 0.0871 | RMS: 0.0270 | -31.4 dBFS  (Speech)
[ 3.0s -  3.5s]  | Peak: 0.0557 | RMS: 0.0138 | -37.2 dBFS  (Speech)
[ 3.5s -  4.0s]  | Peak: 0.0853 | RMS: 0.0158 | -36.0 dBFS  (Speech)
[ 4.0s -  4.5s]  | Peak: 0.1371 | RMS: 0.0183 | -34.7 dBFS  (Speech peak)
[ 4.5s -  5.0s]  | Peak: 0.0086 | RMS: 0.0016 | -56.0 dBFS  (Trailing silence)
```
- **Speech Window:** Only spans 1.5s to 4.5s (3.0 seconds total).
- **Silence Window:** 1.5 seconds of leading silence and 0.5 seconds of trailing silence are fed directly into the model.

---

## 4. Faster-Whisper Configuration & Inference Diagnostics

### 4.1 Current Parameter Settings
In `backend/voice/speech_to_text.py`:
- `model_size`: `"base"`
- `device`: `"cpu"`
- `compute_type`: `"int8"`
- `beam_size`: `5`
- `language`: `"en"`
- `vad_filter`: **Not specified (defaults to `False`)**
- `condition_on_previous_text`: **Not specified (defaults to `True`)**
- `temperature`: Defaults to fallback list `[0.0, 0.2, 0.4, 0.6, 0.8, 1.0]`

### 4.2 Diagnostic Experimentation on Existing Sample
Running controlled parameter comparisons on `microphone_test.wav`:

1. **Current Parameters (Unnormalized, VAD Off):**
   - Result: `[0.00s -> 7.00s] ' Hello, hello, hello, hello.'`
   - Uncertainty: `no_speech_prob: 0.4005` (40% uncertainty).
   - Timestamp anomaly: Model hallucinated a 7.00s end timestamp on a 5.00s audio file due to unpadded trailing silence.
2. **With VAD Filter (`vad_filter=True`):**
   - Result: `[1.33s -> 4.81s] ' Hello, hello, hello, hello.'`
   - Uncertainty: `no_speech_prob: 0.2570` (uncertainty reduced to 25.7%).
   - Speech boundaries detected precisely between 1.33s and 4.81s.
3. **With Peak Normalization (Scaled to 0.95 Peak):**
   - Uncertainty: `no_speech_prob: 0.2517`, `avg_logprob: -0.5738`.
4. **With Peak Normalization + VAD Filter:**
   - Result: `[1.36s -> 4.76s] ' Hello, hello, hello, hello.'`
   - Uncertainty: `no_speech_prob: 0.2074` (50% reduction in model uncertainty vs baseline).

---

## 5. Whisper Model Comparison (ASUS TUF Gaming F16)

Hardware environment: Intel Core 5 205H, NVIDIA RTX 3050 (6GB VRAM), 16GB DDR5 RAM.

| Model | Parameters | Disk Cache | RAM / VRAM Usage | CPU Inference (16 threads) | GPU Inference (RTX 3050) | WER / Accuracy Under Noise | Suitability for VoxPilot |
|---|---|---|---|---|---|---|---|
| **`tiny`** | 39 M | ~75 MB | ~150–250 MB | ~50–80 ms | ~30 ms | High WER (~12–15%); poor on technical/command terms | Not recommended |
| **`base`** *(Current)* | 74 M | ~145 MB | ~300–450 MB | ~150–250 ms | ~60 ms | Moderate WER (~9–11%); prone to phonetic hallucination on quiet audio | Minimum baseline |
| **`small`** | 244 M | ~480 MB | ~850 MB–1.1 GB | ~450–700 ms | ~120 ms | **Low WER (~5–7%)**; highly resilient to noise and accents | **Strongly Recommended** |

### Evaluation:
- **`small` on CPU:** Requires ~1 GB RAM. With 16GB DDR5 on the machine, 1 GB is completely negligible. Inference latency of ~500ms is imperceptible in conversational desktop control.
- **`small` on GPU (CUDA FP16):** Requires ~1.2 GB VRAM. When paired with Ollama `qwen3:4b` (2.5 GB VRAM), total VRAM footprint is ~3.7 GB, well below the 6.0 GB physical VRAM ceiling.

---

## 6. Recommendations for Next Implementation Step (Pending Approval)

Do not implement these changes immediately; wait for explicit phase approval.

1. **Dynamic / Configurable Microphone Selection:**
   - Detect connected headsets (e.g., Sony WF-1000XM5) and prioritize them over built-in laptop microphones, or expose a microphone selector configuration in `.env` / settings.
2. **Audio Input Normalization:**
   - Add RMS or peak normalization in `AudioRecorder` / `SpeechToText._prepare_audio()` to boost quiet speech up to standard operating levels (-3 dBFS peak).
3. **Enable Silero VAD Filter in Faster-Whisper:**
   - Enable `vad_filter=True` in `model.transcribe()` with `float32` typing to eliminate hallucination over leading and trailing silence.
4. **Upgrade Whisper Model to `small`:**
   - Transition from `base` to `small` (or `small.en`) to substantially improve word accuracy on desktop command vocabularies.

