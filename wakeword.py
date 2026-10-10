#!/usr/bin/env python3
"""Wake word listener for the JARVIS pilot — opt-in, never started by the server.

Run it yourself: `python3 wakeword.py` (mic permission required). It listens
locally, and only after it recognises a wake word does it record the command and
send it through the SAME guarded HTTP API the browser uses — token, Host and
Origin checks, approval panel, all unchanged. Audio stays on this machine
(`/api/listen` runs the local Whisper model); nothing here stores recordings.

Toggle phrases are the Thai name plus the usual ASR spellings (see WAKE_WORDS).
Stop with Ctrl-C.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave

WAKE_WORDS = ('จาร์วิส', 'จาวิส', 'จารวิส', 'jarvis')
SILENCE_RMS = 0.012          # below this = silence (normalised -1..1)
SPEECH_HOLD_SECONDS = 0.35   # speech must last this long before we record
UTTERANCE_MAX_SECONDS = 8.0
FOLLOWUP_WINDOW_SECONDS = 7.0  # after the wake word, wait this long for a command
FRAME_SECONDS = 0.1


def is_wake(text):
    """True when any wake phrase occurs anywhere in the transcript."""
    if not isinstance(text, str) or not text.strip():
        return False
    folded = text.lower()
    return any(word in folded for word in WAKE_WORDS)


def extract_command(text):
    """Remove the wake phrase (and stray punctuation) from the transcript."""
    if not isinstance(text, str):
        return ''
    cleaned = text
    for word in WAKE_WORDS:
        cleaned = re.sub(re.escape(word), ' ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    # Trim punctuation at the EDGES only, so filenames like quickstart.md survive.
    return cleaned.strip('.,!?。，！？:：;；-–—()[]{}').strip()


def parse_token(html):
    """Read the per-launch token the page already carries (meta jarvis-token)."""
    if isinstance(html, bytes):
        html = html.decode('utf-8', errors='replace')
    match = re.search(r'name=["\']jarvis-token["\'][^>]*content=["\']([^"\']+)["\']', html)
    if not match:
        match = re.search(r'content=["\']([^"\']+)["\'][^>]*name=["\']jarvis-token["\']', html)
    return match.group(1) if match else ''


def run_payload(message, client_id, request_id):
    return {'message': message, 'client_id': client_id, 'request_id': request_id}


def api_headers(token, port, mime=None):
    headers = {'X-Jarvis-Token': token,
               'Origin': f'http://127.0.0.1:{port}',
               'Host': f'127.0.0.1:{port}'}
    if mime:
        headers['Content-Type'] = mime
    return headers


def rms(frames):
    """Normalised loudness of int16 frames — no numpy needed for the check."""
    if len(frames) == 0:
        return 0.0
    total = 0
    for value in frames:
        total += (int(value) / 32768.0) ** 2
    return (total / len(frames)) ** 0.5


class UtteranceDetector:
    """Energy gate: armed by sustained speech, closed by silence or max length."""

    def __init__(self, threshold=SILENCE_RMS, hold_seconds=SPEECH_HOLD_SECONDS,
                 max_seconds=UTTERANCE_MAX_SECONDS):
        self.threshold = threshold
        self.hold_frames = max(1, round(hold_seconds / FRAME_SECONDS))
        self.max_frames = max(self.hold_frames + 1, round(max_seconds / FRAME_SECONDS))
        self.speech_run = 0
        self.capturing = False
        self.frames = 0

    def feed(self, level):
        """Returns 'start', 'frame', 'end' or None."""
        if not self.capturing:
            if level >= self.threshold:
                self.speech_run += 1
                if self.speech_run >= self.hold_frames:
                    self.capturing = True
                    self.frames = 0
                    return 'start'
            else:
                self.speech_run = 0
            return None
        self.frames += 1
        if level < self.threshold or self.frames >= self.max_frames:
            self.capturing = False
            self.speech_run = 0
            return 'end'
        return 'frame'


def post_json(url, payload, headers, timeout=60):
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(url, data=body, method='POST', headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def send_transcript(base, token, port, wav_bytes):
    request = urllib.request.Request(f'{base}/api/listen', data=wav_bytes, method='POST',
                                     headers=api_headers(token, port, 'audio/wav'))
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode('utf-8')).get('text', '')


def send_command(base, token, port, message):
    payload = run_payload(message, 'wakeword-' + uuid.uuid4().hex[:8], str(uuid.uuid4()))
    return post_json(f'{base}/api/run', payload, api_headers(token, port))


def refresh_token(base):
    with urllib.request.urlopen(f'{base}/', timeout=15) as response:
        return parse_token(response.read())


def capture_wav(frames, samplerate, channels=1):
    import io
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(samplerate)
        handle.writeframes(frames.tobytes() if hasattr(frames, 'tobytes') else bytes(frames))
    return buffer.getvalue()


def listen_loop(port=8730, device=None):
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as error:
        raise SystemExit(f'ต้องการ sounddevice/numpy สำหรับ wake word: {error}')

    base = f'http://127.0.0.1:{port}'
    token = refresh_token(base)
    print(f'wake word: {", ".join(WAKE_WORDS)} — Ctrl-C เพื่อหยุด', flush=True)
    detector = UtteranceDetector()
    pending_wake_until = 0.0

    try:
        with sd.InputStream(samplerate=48000, channels=1, dtype='int16',
                            blocksize=int(FRAME_SECONDS * 48000), device=device) as stream:
            while True:
                block, _ = stream.read(int(FRAME_SECONDS * 48000))
                event = detector.feed(rms(block))
                if event != 'start':
                    if pending_wake_until and time.monotonic() > pending_wake_until:
                        pending_wake_until = 0.0
                    continue
                raw = record_utterance(stream, np, max_seconds=UTTERANCE_MAX_SECONDS)
                try:
                    text = send_transcript(base, token, port, capture_wav(raw, 48000))
                except urllib.error.HTTPError as error:
                    if error.code in (401,):
                        token = refresh_token(base)
                        continue
                    print(f'transcribe failed: {error.code}', file=sys.stderr, flush=True)
                    continue
                if not is_wake(text):
                    continue
                print(' detected:', text, flush=True)
                pending_wake_until = time.monotonic() + FOLLOWUP_WINDOW_SECONDS
                command = extract_command(text)
                while not command and time.monotonic() < pending_wake_until:
                    follow = record_utterance(
                        stream, np,
                        max_seconds=min(FOLLOWUP_WINDOW_SECONDS,
                                        pending_wake_until - time.monotonic()))
                    follow_text = send_transcript(base, token, port, capture_wav(follow, 48000))
                    command = extract_command(follow_text)
                if not command:
                    continue
                print('command:', command, flush=True)
                try:
                    result = send_command(base, token, port, command)
                except urllib.error.HTTPError as error:
                    if error.code == 401:
                        token = refresh_token(base)
                        result = send_command(base, token, port, command)
                    else:
                        print(f'send failed: {error.code}', file=sys.stderr, flush=True)
                        continue
                reply = (result.get('reply') or result.get('error') or '').strip()
                if reply:
                    print('jarvis:', reply, flush=True)
                if result.get('needs_approval'):
                    print('  → เปิดหน้าเว็บเพื่ออนุมัติการอ่านไฟล์', flush=True)
    except KeyboardInterrupt:
        print('\nstopped', flush=True)


def record_utterance(stream, np, max_seconds=UTTERANCE_MAX_SECONDS, samplerate=48000):
    """Read from the already-open stream until a short silence or the cap.
    No second stream is opened, so nothing is dropped between gate and capture."""
    if max_seconds <= 0:
        return b''
    chunks = []
    silent = 0
    started = time.monotonic()
    while time.monotonic() - started < max_seconds:
        block, _ = stream.read(int(FRAME_SECONDS * samplerate))
        chunk = np.asarray(block)
        chunks.append(chunk)
        if rms(chunk) < SILENCE_RMS:
            silent += 1
            if silent >= 3 and len(chunks) > 5:
                break
        else:
            silent = 0
    if not chunks:
        return b''
    return np.concatenate(chunks).tobytes()


def main(argv=None):
    parser = argparse.ArgumentParser(description='JARVIS wake word (opt-in)')
    parser.add_argument('--port', type=int, default=8730)
    parser.add_argument('--device', type=int, default=None, help='index ของไมโครโฟนจาก sounddevice')
    args = parser.parse_args(argv)
    listen_loop(port=args.port, device=args.device)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
