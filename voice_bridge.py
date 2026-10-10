"""Bounded voice subprocesses using Hermes' installed managed runtime.
No agent invocation, cloud STT fallback, microphone, or server-side playback.
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HERMES_SOURCE = Path.home() / '.hermes' / 'hermes-agent'
SCRATCH = Path.home() / '.hermes' / 'cache' / 'scratch'
MIMES = {'audio/webm', 'audio/ogg', 'audio/mp4', 'audio/mpeg', 'audio/wav', 'audio/x-wav'}
ALLOWED_STT_MODELS = ('tiny', 'base', 'small', 'medium', 'large-v3')
_LOCK = threading.Lock()
_READY = None
_READY_AT = 0.0


def _stt_config():
    from tools.transcription_tools import _load_stt_config
    return _load_stt_config()


def stt_model_override():
    """Pilot-scoped model choice from the launcher environment only.
    Never from a client request and never written to ~/.hermes/config.yaml."""
    model = os.environ.get('JARVIS_STT_MODEL', '').strip()
    if not model:
        return ''
    if model not in ALLOWED_STT_MODELS:
        raise RuntimeError('unsupported JARVIS_STT_MODEL')
    return model


def _transcribe_file(path):
    config = _stt_config()
    if config.get('provider') != 'local' or not config.get('enabled'):
        raise RuntimeError('local STT is required')
    from tools.transcription_tools import transcribe_audio
    model = stt_model_override()
    kwargs = {'file_path': str(path), 'source': 'jarvis-dashboard'}
    if model:
        kwargs['model'] = model
    return transcribe_audio(**kwargs)


def _speak_file(text, path):
    from tools.tts_tool import text_to_speech_tool, _load_tts_config
    config = _load_tts_config()
    if config.get('provider') != 'edge' or config.get('edge', {}).get('voice') != 'th-TH-NiwatNeural':
        raise RuntimeError('configured Thai Edge voice is required')
    result = json.loads(text_to_speech_tool(text=text, output_path=str(path), provider='edge'))
    if result.get('success') is False or not path.exists() or path.stat().st_size == 0:
        raise RuntimeError('TTS did not create audio')
    return {'bytes': path.stat().st_size}


def _probe_ready():
    from tools.tts_tool import _load_tts_config, text_to_speech_tool
    import edge_tts
    import faster_whisper
    sc = _stt_config()
    tc = _load_tts_config()
    override = stt_model_override()
    return {'stt': bool(sc.get('enabled') and sc.get('provider') == 'local'),
            'tts': bool(tc.get('provider') == 'edge' and tc.get('edge', {}).get('voice') == 'th-TH-NiwatNeural'),
            'model': override or sc.get('local', {}).get('model'),
            'model_source': 'pilot-override' if override else 'config',
            'language': sc.get('local', {}).get('language'),
            'voice': tc.get('edge', {}).get('voice'),
            'network_checked': False}


def _call(payload, timeout=90):
    # Import launcher only. Runtime bootstrap belongs to the subprocess, never
    # the host HTTP/pytest process (which may use a different Python ABI).
    if str(HERMES_SOURCE) not in sys.path:
        sys.path.insert(0, str(HERMES_SOURCE))
    from hermes_cli._launchers import runtime_command
    code = 'import runpy; runpy.run_path(' + repr(str(Path(__file__).resolve())) + ', run_name="__main__")'
    cmd = runtime_command(HERMES_SOURCE, code=code)
    env = dict(os.environ)
    env['TMPDIR'] = str(SCRATCH)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
    try:
        stdout, _ = proc.communicate(json.dumps(payload), timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise RuntimeError('voice provider timed out') from None
    if proc.returncode != 0:
        raise RuntimeError('voice provider unavailable')
    try:
        return json.loads(stdout)
    except (TypeError, ValueError):
        raise RuntimeError('invalid provider response') from None


def readiness():
    global _READY, _READY_AT
    if _READY is None or time.monotonic() - _READY_AT > 60:
        try:
            _READY = _call({'op': 'ready'}, timeout=20)
        except Exception:
            _READY = {'stt': False, 'tts': False, 'network_checked': False}
        _READY_AT = time.monotonic()
    return dict(_READY)


def decode_audio(raw):
    # WebM from MediaRecorder often has duration=N/A. Decode a bounded window
    # instead of trusting container metadata. Output is <=31s mono PCM WAV.
    p = subprocess.run(['ffmpeg', '-v', 'error', '-i', 'pipe:0', '-t', '31',
                        '-ar', '16000', '-ac', '1', '-f', 'wav', 'pipe:1'],
                       input=raw, capture_output=True, timeout=10)
    if p.returncode or len(p.stdout) < 100:
        raise ValueError('invalid audio')
    # ffmpeg streaming WAV has unknown length in its header; its data payload
    # still provides a strict upper bound on decoded samples.
    index = p.stdout.find(b'data')
    if index < 0:
        raise ValueError('invalid decoded audio')
    seconds = (len(p.stdout) - index - 8) / 32000
    if not math.isfinite(seconds) or seconds <= 0 or seconds > 30:
        raise ValueError('audio must be at most 30 seconds')
    return p.stdout


def transcribe(audio, mime='audio/webm'):
    if mime.split(';', 1)[0].lower() not in MIMES:
        raise ValueError('unsupported audio type')
    if not audio or len(audio) > 4 * 1024 * 1024:
        raise ValueError('invalid audio size')
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError('voice provider busy')
    try:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='jarvis-voice-', dir=SCRATCH) as folder:
            path = Path(folder) / 'recording.wav'
            path.write_bytes(decode_audio(audio))
            result = _call({'op': 'stt', 'path': str(path)})
            if not result.get('success'):
                raise RuntimeError('transcription unavailable')
            text = str(result.get('transcript', '')).strip()
            if not text:
                raise ValueError('no speech detected')
            return text
    finally:
        _LOCK.release()


def speak(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError('text must contain 1 to 2000 characters')
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError('voice provider busy')
    try:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='jarvis-voice-', dir=SCRATCH) as folder:
            path = Path(folder) / 'speech.mp3'
            _call({'op': 'tts', 'text': text, 'path': str(path)}, timeout=45)
            if not path.exists() or path.stat().st_size > 5 * 1024 * 1024:
                raise RuntimeError('invalid audio output')
            return path.read_bytes()
    finally:
        _LOCK.release()


def main():
    payload = json.load(sys.stdin)
    with contextlib.redirect_stdout(io.StringIO()):
        op = payload.get('op')
        if op == 'ready':
            result = _probe_ready()
        elif op == 'stt':
            result = _transcribe_file(Path(payload['path']))
        elif op == 'tts':
            result = _speak_file(payload['text'], Path(payload['path']))
        else:
            raise ValueError('unknown voice operation')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
