import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import server


@pytest.fixture
def http_server():
    server.API_TOKEN = "test-token"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = srv.server_address[1]
    old_port = server.PORT
    server.PORT = port
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", port
    srv.shutdown()
    thread.join()
    server.PORT = old_port
    server.API_TOKEN = "test-token"


def request(base, port, path, body=None, content_type="application/json", token=True):
    req = urllib.request.Request(base + path, data=body, method="POST" if body is not None else "GET")
    req.add_header("Host", f"127.0.0.1:{port}")
    req.add_header("Origin", f"http://127.0.0.1:{port}")
    if token:
        req.add_header("X-Jarvis-Token", "test-token")
    if body is not None:
        req.add_header("Content-Type", content_type)
    try:
        return urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as exc:
        return exc


def test_status_reports_chat_readiness_but_tools_stay_disabled(http_server):
    base, port = http_server
    response = request(base, port, "/api/status")
    status = json.loads(response.read())
    assert status["stt"] == "local"
    assert status["tts"] == "edge"
    assert status["tools_enabled"] is False
    assert status["agent_enabled"] is server.CHAT_BRIDGE.ready
    assert status["chat_enabled"] is server.CHAT_BRIDGE.ready
    assert status["scope"] == "chat-only"
    assert status["server_stt"] is True
    assert status["server_tts"] is True
    assert status["backend_ready"] is True


def test_run_and_slash_command_execution_fail_closed(http_server):
    base, port = http_server
    for message in ("hello", "/tools"):
        response = request(base, port, "/api/run", json.dumps({"message": message}).encode())
        assert response.status == 400
        assert json.loads(response.read())["error"] == "invalid chat request"


def test_speak_validates_and_returns_mp3(http_server, monkeypatch):
    monkeypatch.setattr(server.voice, "speak", lambda text: b"ID3test")
    base, port = http_server
    response = request(base, port, "/api/speak", json.dumps({"text": "สวัสดี"}).encode())
    assert response.status == 200
    assert response.headers["Content-Type"] == "audio/mpeg"
    assert response.read() == b"ID3test"
    invalid = request(base, port, "/api/speak", json.dumps({"text": "x" * 2001}).encode())
    assert invalid.status == 400


def test_listen_passes_raw_audio_and_returns_transcript(http_server, monkeypatch):
    seen = {}
    monkeypatch.setattr(server.voice, "transcribe", lambda raw, mime: seen.update(raw=raw, mime=mime) or "ทดสอบ")
    base, port = http_server
    response = request(base, port, "/api/listen", b"audio bytes", "audio/webm")
    assert response.status == 200
    assert seen == {"raw": b"audio bytes", "mime": "audio/webm"}
    assert json.loads(response.read()) == {"text": "ทดสอบ"}


def test_new_cancel_require_owned_identifiers(http_server):
    base, port = http_server
    for endpoint in ("/api/new", "/api/cancel"):
        response = request(base, port, endpoint, b"{}")
        payload = json.loads(response.read())
        assert response.status == 400
        assert payload["error"] == "invalid chat request"


def test_post_requires_token_and_rejects_transfer_encoding(http_server):
    base, port = http_server
    assert request(base, port, "/api/run", b"{}", token=False).status == 401
    import socket
    # Send the complete rejected request atomically. urllib sends chunk markers
    # separately and can race the deliberate early connection close.
    raw = (f'POST /api/run HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
           f'Origin: http://127.0.0.1:{port}\r\nX-Jarvis-Token: test-token\r\n'
           'Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n'
           'Connection: close\r\n\r\n2\r\n{}\r\n0\r\n\r\n').encode()
    with socket.create_connection(('127.0.0.1', port), timeout=5) as conn:
        conn.sendall(raw)
        response = conn.recv(4096)
    assert response.startswith(b'HTTP/1.1 400 ')
