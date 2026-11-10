# VoxPilot — Wake Word Models Directory

This directory stores offline wake word neural network models for VoxPilot.

## "Hey VoxPilot" Custom Model Status

- **Status:** Custom model training required.
- **Pretrained availability:** openWakeWord does not bundle a pretrained "Hey VoxPilot" model.
- **Target format:** ONNX (`models/wake_word/hey_voxpilot.onnx`) or TFLite (`models/wake_word/hey_voxpilot.tflite`).

## Training a Custom "Hey VoxPilot" Model

To train a high-accuracy, production-ready "Hey VoxPilot" wake word model:

1. **Synthetic Audio Generation:**
   - Use Piper TTS or Coqui TTS with diverse multi-speaker voice profiles (20+ voices, varying accents, pitches, and cadences).
   - Generate ~20,000 to 50,000 synthetic audio clips saying "Hey VoxPilot".
   - Apply data augmentation: room impulse responses (RIR), background noise (fan, keyboard, street, music), speed perturbation (0.85x to 1.15x), and volume scaling.

2. **Negative & Adversarial Dataset:**
   - Ingest 2,000+ hours of non-target human speech (e.g. Common Voice, LibriSpeech, AudioSet).
   - Include phonetically similar adversarial false-trigger phrases:
     - "Hey Pilot", "Hey Box", "Vox", "Pilot", "The pilot", "Go pilot", etc.

3. **Feature Extraction & Transfer Learning:**
   - Pass audio through openWakeWord's frozen 96-dimensional acoustic embedding model (`embedding_model.onnx`).
   - Train a lightweight 2-layer classifier DNN or depthwise separable CNN.

4. **Export & Deployment:**
   - Export trained weights to ONNX format.
   - Save the model as:
     `models/wake_word/hey_voxpilot.onnx`
   - Set in `.env`:
     ```env
     VOXPILOT_WAKE_WORD_ENABLED=true
     VOXPILOT_WAKE_WORD_MODEL=models/wake_word/hey_voxpilot.onnx
     VOXPILOT_WAKE_WORD_THRESHOLD=0.5
     ```

## Supported Surrogate Pretrained Models

During development, any of openWakeWord's official pretrained models can be used:
- `hey_jarvis_v0.1` (Wake phrase: "Hey Jarvis")
- `alexa_v0.1` (Wake phrase: "Alexa")
- `hey_mycroft_v0.1` (Wake phrase: "Hey Mycroft")
- `hey_rhasspy_v0.1` (Wake phrase: "Hey Rhasspy")

