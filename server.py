"""Loopback-only Thai voice and restricted Hermes text conversation pilot."""
import json
import mimetypes
import os
import re
from pathlib import Path
import secrets
import socket
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import voice
from agent_bridge import CHAT_BRIDGE, BridgeError, validate_ids, validate_run
import tools_readonly

ROOT = Path(__file__).resolve().parent
UI = ROOT / 'ui'
PORT = int(os.environ.get('JARVIS_PORT', '8730'))
API_TOKEN = secrets.token_urlsafe(32)
MAX_JSON_BODY = 16 * 1024
MAX_AUDIO_BODY = 4 * 1024 * 1024
DISABLED = 'agent execution disabled pending verified approval integration'


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *args):
        pass  # No prompts, transcripts, audio, tokens or raw errors in logs.

    def _bytes(self, data, ctype, code=200):
        self.close_connection = True
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        return self._bytes(json.dumps(obj, ensure_ascii=False).encode(), 'application/json; charset=utf-8', code)

    def _host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get('Host') in {f'127.0.0.1:{port}', f'localhost:{port}'}

    def _origin_ok(self):
        port = self.server.server_address[1]
        return self.headers.get('Origin') in {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}

    def _token_ok(self):
        supplied = self.headers.get('X-Jarvis-Token', '')
        return bool(supplied) and secrets.compare_digest(supplied, API_TOKEN)

    def _read(self, limit):
        if self.headers.get('Transfer-Encoding'):
            raise ValueError('invalid framing')
        lengths = self.headers.get_all('Content-Length') or []
        if len(lengths) != 1 or not lengths[0].isdecimal():
            raise ValueError('invalid length')
        length = int(lengths[0])
        if length > limit:
            raise OverflowError('too large')
        data = self.rfile.read(length)
        if len(data) != length:
            raise ValueError('short body')
        return data

    def do_GET(self):
        if not self._host_ok():
            return self._json({'error': 'invalid host'}, 403)
        path = urlparse(self.path).path
        if path == '/api/status':
            ready = voice.readiness()
            return self._json({'stt': 'local', 'tts': 'edge', 'tools_enabled': False,
                               'agent_enabled': CHAT_BRIDGE.ready, 'chat_enabled': CHAT_BRIDGE.ready,
                               'scope': 'chat-only', 'server_stt': bool(ready.get('stt')),
                               'chat_provider': os.environ.get('JARVIS_CHAT_PROVIDER', ''),
                               'chat_model': os.environ.get('JARVIS_CHAT_MODEL', ''),
                               'stt_model_override': os.environ.get('JARVIS_STT_MODEL', ''),
                               'tools_read_only': bool(tools_readonly.load_gate_config().get('enabled')),
                               'tools_layer': ('read_only-after-approval'
                                               if tools_readonly.load_gate_config().get('enabled') else 'off'),
                               'server_tts': bool(ready.get('tts')),
                               'backend_ready': bool(ready.get('stt') and ready.get('tts')),
                               'model': ready.get('model'), 'language': ready.get('language'),
                               'voice': ready.get('voice'), 'network_checked': False})
        if path.startswith('/api/'):
            if not self._token_ok():
                return self._json({'error': 'unauthorized'}, 401)
            return self._json({'error': DISABLED}, 503)
        rel = 'index.html' if path == '/' else path.lstrip('/')
        file = (UI / rel).resolve()
        if not file.is_relative_to(UI.resolve()) or not file.is_file():
            return self._bytes(b'not found', 'text/plain', 404)
        data = file.read_bytes()
        if rel == 'index.html':
            data = data.replace(b'__JARVIS_TOKEN__', API_TOKEN.encode())
        return self._bytes(data, mimetypes.guess_type(file.name)[0] or 'application/octet-stream')

    def do_POST(self):
        if not self._host_ok() or not self._origin_ok():
            return self._json({'error': 'request origin rejected'}, 403)
        if not self._token_ok():
            return self._json({'error': 'unauthorized'}, 401)
        path = urlparse(self.path).path
        mime = self.headers.get('Content-Type', '').split(';', 1)[0].lower()
        if path == '/api/listen':
            if mime not in voice.voice_bridge.MIMES:
                return self._json({'error': 'unsupported audio type'}, 415)
        elif mime != 'application/json':
            return self._json({'error': 'application/json required'}, 415)
        try:
            raw = self._read(MAX_AUDIO_BODY if path == '/api/listen' else MAX_JSON_BODY)
        except OverflowError:
            return self._json({'error': 'request body too large'}, 413)
        except (ValueError, OSError, socket.timeout):
            return self._json({'error': 'invalid request body'}, 400)
        if path == '/api/listen':
            if not raw:
                return self._json({'error': 'empty audio', 'text': ''}, 400)
            try:
                return self._json({'text': voice.transcribe(raw, mime)})
            except ValueError:
                return self._json({'error': 'invalid, overlong or empty speech audio', 'text': ''}, 400)
            except Exception:
                return self._json({'error': 'transcription unavailable or busy', 'text': ''}, 503)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('invalid schema')
        except (ValueError, UnicodeDecodeError):
            return self._json({'error': 'invalid JSON object'}, 400)
        if path == '/api/speak':
            if set(payload) != {'text'} or not isinstance(payload['text'], str):
                return self._json({'error': 'invalid request schema'}, 400)
            text = payload['text'].strip()
            if not text or len(text) > 2000:
                return self._json({'error': 'text must contain 1 to 2000 characters'}, 400)
            try:
                return self._bytes(voice.speak(text), 'audio/mpeg')
            except Exception:
                return self._json({'error': 'speech synthesis unavailable or busy'}, 503)
        if path in {'/api/run', '/api/new', '/api/cancel', '/api/approve', '/api/deny'}:
            try:
                if path == '/api/run':
                    message, client, request = validate_run(payload)
                    result = CHAT_BRIDGE.run(message, client, request)
                elif path == '/api/approve':
                    if set(payload) != {'client_id', 'request_id', 'digest'}:
                        raise ValueError('invalid approval schema')
                    if not isinstance(payload['digest'], str) or not re.fullmatch(r'[0-9a-f]{64}', payload['digest']):
                        raise ValueError('invalid approval digest')
                    client, request = validate_ids(
                        {k: payload[k] for k in ('client_id', 'request_id')}, ('client_id', 'request_id'))
                    result = CHAT_BRIDGE.approve(client, request, payload['digest'])
                elif path == '/api/deny':
                    client, request = validate_ids(payload, ('client_id', 'request_id'))
                    result = CHAT_BRIDGE.deny(client, request)
                elif path == '/api/cancel':
                    client, request = validate_ids(payload, ('client_id', 'request_id'))
                    result = CHAT_BRIDGE.cancel(client, request)
                else:
                    client, = validate_ids(payload, ('client_id',))
                    result = CHAT_BRIDGE.new(client)
                return self._json(result)
            except ValueError:
                return self._json({'error': 'invalid chat request'}, 400)
            except BridgeError as error:
                return self._json({'error': str(error)}, error.status)
            except Exception:
                return self._json({'error': 'chat unavailable'}, 503)
        if path == '/api/jobs':
            return self._json({'error': DISABLED}, 503)
        return self._json({'error': 'no such endpoint'}, 404)


def main():
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    srv.daemon_threads = True
    print(f'JARVIS Thai chat-only pilot: http://127.0.0.1:{PORT}\nLocal STT + online Edge TTS. Chat readiness={CHAT_BRIDGE.ready}; tools DISABLED.', flush=True)
    if os.environ.get('JARVIS_OPEN', '0') == '1':
        webbrowser.open(f'http://127.0.0.1:{PORT}')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == '__main__':
    main()
