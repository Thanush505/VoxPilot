"""
Hardware verification script for WakeWordDetector in VoxPilot.
Measures:
- Model load time
- Inference latency per 80 ms frame (1280 samples)
- Memory usage (RSS)
- Clean PortAudio microphone handover
"""

import os
import time
import psutil
import numpy as np
from backend.voice.wake_word import WakeWordDetector, FRAME_SIZE_SAMPLES, REQUIRED_SAMPLE_RATE
from backend.voice.audio_recorder import AudioRecorder

process = psutil.Process(os.getpid())
ram_before = process.memory_info().rss / (1024 * 1024)

print("=== VoxPilot Wake Word Hardware Verification ===")
print(f"PID: {os.getpid()}")
print(f"Initial RAM: {ram_before:.2f} MB")

# 1. Measure model load time
t0 = time.perf_counter()
detector = WakeWordDetector(model_path="hey_jarvis_v0.1", threshold=0.5)
detector.load_model()
load_time_ms = (time.perf_counter() - t0) * 1000

ram_after_load = process.memory_info().rss / (1024 * 1024)
print(f"Model load time: {load_time_ms:.2f} ms")
print(f"RAM after load: {ram_after_load:.2f} MB (Delta: {ram_after_load - ram_before:.2f} MB)")

# 2. Measure prediction latency across 50 frames
frame = np.zeros(FRAME_SIZE_SAMPLES, dtype=np.float32)
latencies = []
for _ in range(50):
    t_start = time.perf_counter()
    res = detector.predict_frame(frame)
    latencies.append((time.perf_counter() - t_start) * 1000)

avg_latency = np.mean(latencies[5:])  # skip warmup
min_latency = np.min(latencies[5:])
max_latency = np.max(latencies[5:])
rtf = (avg_latency / 1000.0) / 0.08  # Real-Time Factor (frame is 80 ms)

print(f"Inference latency: avg={avg_latency:.2f} ms, min={min_latency:.2f} ms, max={max_latency:.2f} ms")
print(f"Real-Time Factor (RTF): {rtf:.4f} (80 ms chunk processed in {avg_latency:.2f} ms)")
print(f"Estimated single-core CPU usage: {rtf * 100:.2f}%")

# 3. Test Microphone Stream Handover
print("\nTesting microphone stream handover...")
try:
    dev_idx, dev_info = AudioRecorder.resolve_device(None)
    print(f"Resolved recording device: [{dev_idx}] {dev_info['name']}")

    # Listen with a short timeout to verify stream opens and closes cleanly
    t_listen_start = time.perf_counter()
    event = detector.listen_for_wake_word(timeout=1.0)
    listen_dur = time.perf_counter() - t_listen_start
    print(f"Wake word listening timed out cleanly after {listen_dur:.2f} s as expected (no speech).")

    # Immediately attempt recording with AudioRecorder
    recorder = AudioRecorder(device=dev_idx)
    rec_audio = recorder.record(duration=1.0)
    print(f"AudioRecorder recorded {len(rec_audio)} samples ({len(rec_audio)/recorder.sample_rate:.1f} s) successfully!")
    print("Microphone handover: SUCCESS (No PortAudio conflict or device lock).")

except Exception as e:
    print(f"Microphone test error: {e}")

print("================================================")

