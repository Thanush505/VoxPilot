from backend.voice.voice_pipeline import VoicePipeline


pipeline = VoicePipeline()

pipeline.initialize()

pipeline.run_once(duration=5)