"""Voice-only adapters. Agent execution is deliberately unavailable."""
import voice_bridge


def readiness():
    return voice_bridge.readiness()


def available():
    ready = readiness()
    return bool(ready.get('stt') and ready.get('tts'))


def voice_id():
    return 'th-TH-NiwatNeural'


def transcribe(audio, mime='audio/webm'):
    return voice_bridge.transcribe(audio, mime)


def speak(text):
    return voice_bridge.speak(text)
