"""Phase 4: resilience + security negative tests.

Deterministic and isolated — no live LLM, no microphone, no playback, no
changes to ~/.hermes. Each test runs against its own fixture worker or its own
HTTP server on an ephemeral port; the pilot on 8730 is never touched.
"""
import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
import uuid
from http.server import ThreadingHTTPServer

import pytest

import agent_bridge
import server

CONFIG_PATH = os.path.expanduser('~/.hermes/config.yaml')
SCRATCH = os.path.expanduser('~/.hermes/cache/scratch')


def sha256(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()


def uid():
    return str(uuid.uuid4())


def safe_proof():
    return {'constructed': True, 'tools': [], 'valid_tool_names': [],
            'capability_denials': agent_bridge.DENIED_TOOLS, 'scope': 'chat-only',
            'mock_mode': False}


@pytest.fixture(autouse=True)
def config_never_modified():
    """Nothing in this module may write ~/.hermes/config.yaml."""
    before = sha256(CONFIG_PATH)
    yield
    assert sha256(CONFIG_PATH) == before, 'config.yaml changed during Phase-4 tests'


# ---------------------------------------------------------------- restart ----
def test_restart_loses_volatile_state_and_fails_closed_until_proof():
    def good_worker(payload, on_process=None):
        if payload['op'] == 'proof':
            return safe_proof()
        return {'reply': 'ok', 'tools_before': 0, 'tools_after': 0, 'proof': safe_proof()}

    first = agent_bridge.AgentBridge(good_worker)
    cid, rid = uid(), uid()
    first.run('remember me', cid, rid)
    assert len(first.state.history(cid)) == 2, 'history should exist before restart'

    # Simulated restart: a brand-new process has no volatile state at all.
    restarted = agent_bridge.AgentBridge(good_worker)
    assert restarted.state.history(cid) == [], 'chat history must not survive a restart'
    assert restarted.state.active is None

    # Readiness is fail-closed: no proof -> no chat endpoint.
    def broken_worker(payload, on_process=None):
        raise RuntimeError('proof unavailable after restart')

    down = agent_bridge.AgentBridge(broken_worker)
    assert down.ready is False
    with pytest.raises(agent_bridge.BridgeError) as ex:
        down.run('any message', uid(), uid())
    assert ex.value.status == 503
    assert 'tool call' not in str(ex.value)


# ----------------------------------------------------------- contention -----
def test_second_run_is_409_and_each_client_keeps_only_its_own_result():
    release = threading.Event()
    started = threading.Event()

    def worker(payload, on_process=None):
        if payload['op'] == 'proof':
            return safe_proof()
        started.set()
        release.wait(timeout=5)
        return {'reply': 'reply-to-' + payload['message'], 'tools_before': 0,
                'tools_after': 0, 'proof': safe_proof()}

    bridge = agent_bridge.AgentBridge(worker)
    tab_a, tab_b = uid(), uid()
    rid_a, rid_b = uid(), uid()
    outcome = {}

    def run_a():
        try:
            outcome['a'] = bridge.run('A', tab_a, rid_a)
        except agent_bridge.BridgeError as error:
            outcome['a'] = error.status

    thread = threading.Thread(target=run_a)
    thread.start()
    assert started.wait(timeout=3), 'first run never started'

    with pytest.raises(agent_bridge.BridgeError) as busy:
        bridge.run('B', tab_b, rid_b)
    assert busy.value.status == 409, 'a second concurrent run must be rejected, not queued'

    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert outcome['a']['reply'] == 'reply-to-A'
    assert bridge.state.history(tab_b) == [], 'rejected run must not write any history'
    assert bridge.state.history(tab_a)[1]['content'] == 'reply-to-A'
    # The lock must be free again, and an *accepted* request id stays replay-protected
    # (an id rejected as busy was never reserved, so a client may legitimately retry it).
    assert bridge.run('A2', tab_a, uid())['reply'] == 'reply-to-A2'
    with pytest.raises(agent_bridge.BridgeError) as replay:
        bridge.run('again', tab_a, rid_a)
    assert replay.value.status == 409


# ---------------------------------------------------- guard matrix -----------
@pytest.fixture
def guarded_server(monkeypatch):
    """Real Handler, ephemeral port, instrumented backends."""
    calls = {'listen': 0, 'speak': 0, 'run': 0}

    def fake_transcribe(raw, mime):
        calls['listen'] += 1
        return 'ไม่ควรถูกเรียก'

    def fake_speak(text):
        calls['speak'] += 1
        return b'ID3'

    class CountingBridge:
        ready = True
        def run(self, *a, **k):
            calls['run'] += 1
            return {'reply': 'x', 'session_id': 's', 'request_id': a[2] if len(a) > 2 else ''}
        def cancel(self, *a, **k):
            calls['run'] += 1
            return {'stopped': False}
        def new(self, *a, **k):
            calls['run'] += 1
            return {'ok': True}

    monkeypatch.setattr(server.voice, 'transcribe', fake_transcribe)
    monkeypatch.setattr(server.voice, 'speak', fake_speak)
    monkeypatch.setattr(server, 'CHAT_BRIDGE', CountingBridge())
    old_token = server.API_TOKEN
    server.API_TOKEN = 'phase4-token'
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{port}', port, calls
    httpd.shutdown()
    httpd.server_close()
    server.API_TOKEN = old_token


def post(base, port, path, data, *, token='phase4-token', origin=None, host=None,
         content_type='application/json'):
    origin = origin if origin is not None else base
    req = urllib.request.Request(base + path, data=data, method='POST')
    req.add_header('Host', host or f'127.0.0.1:{port}')
    req.add_header('Origin', origin)
    req.add_header('Content-Type', content_type)
    if token is not None:
        req.add_header('X-Jarvis-Token', token)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_guard_matrix_rejects_every_bad_request_before_the_backend(guarded_server):
    base, port, calls = guarded_server
    valid = json.dumps({'message': 'สวัสดี', 'confirmed': True,
                        'client_id': uid(), 'request_id': uid()}).encode()

    cases = {
        'wrong_token': post(base, port, '/api/run', valid, token='wrong'),
        'missing_token': post(base, port, '/api/run', valid, token=None),
        'wrong_origin': post(base, port, '/api/run', valid, origin='https://evil.example'),
        'wrong_host': post(base, port, '/api/run', valid, host=f'evil.example:{port}'),
        'wrong_mime': post(base, port, '/api/run', valid, content_type='text/plain'),
        'oversize': post(base, port, '/api/run', b'x' * (17 * 1024)),
        'empty_body': post(base, port, '/api/run', b''),
        'bad_json': post(base, port, '/api/run', b'{not json'),
        'not_object': post(base, port, '/api/run', b'[]'),
        'unconfirmed': post(base, port, '/api/run', json.dumps(
            {'message': 'hi', 'confirmed': False, 'client_id': uid(), 'request_id': uid()}).encode()),
        'slash_command': post(base, port, '/api/run', json.dumps(
            {'message': '/tools', 'confirmed': True, 'client_id': uid(), 'request_id': uid()}).encode()),
        'bad_uuid': post(base, port, '/api/run', json.dumps(
            {'message': 'hi', 'confirmed': True, 'client_id': 'nope', 'request_id': uid()}).encode()),
        'unknown_endpoint': post(base, port, '/api/execute', valid),
        'jobs_disabled': post(base, port, '/api/jobs', b'{}'),
    }
    assert cases['wrong_token'][0] == 401
    assert cases['missing_token'][0] == 401
    assert cases['wrong_origin'][0] == 403
    assert cases['wrong_host'][0] == 403
    assert cases['wrong_mime'][0] == 415
    assert cases['oversize'][0] == 413
    assert cases['empty_body'][0] == 400
    assert cases['bad_json'][0] == 400
    assert cases['not_object'][0] == 400
    assert cases['unconfirmed'][0] == 400
    assert cases['slash_command'][0] == 400
    assert cases['bad_uuid'][0] == 400
    assert cases['unknown_endpoint'][0] == 404
    assert cases['jobs_disabled'][0] == 503

    # Chunked framing is rejected before any body is parsed.
    import socket
    raw = (f'POST /api/run HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
           f'Origin: {base}\r\nX-Jarvis-Token: phase4-token\r\n'
           'Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n'
           'Connection: close\r\n\r\n2\r\n{}\r\n0\r\n\r\n').encode()
    with socket.create_connection(('127.0.0.1', port), timeout=5) as conn:
        conn.sendall(raw)
        first_line = conn.recv(64)
    assert first_line.startswith(b'HTTP/1.1 400 '), first_line

    assert calls == {'listen': 0, 'speak': 0, 'run': 0}, (
        f'backend was reached by a rejected request: {calls}')


def test_status_never_leaks_token_or_key_material(guarded_server):
    base, port, calls = guarded_server
    body = urllib.request.urlopen(base + '/api/status', timeout=5).read().decode()
    assert 'phase4-token' not in body
    assert 'sk-' not in body and 'Bearer' not in body
    # Rejected run bodies stay generic and secret-free too.
    status, payload = post(base, port, '/api/run', b'[]')
    assert status == 400
    assert b'phase4-token' not in payload
    assert calls['run'] == 0


# --------------------------------------------- honest provider failures ------
def test_tts_timeout_is_reported_as_error_never_as_success(guarded_server, monkeypatch):
    base, port, calls = guarded_server

    def timing_out(text):
        calls['speak'] += 1
        raise TimeoutError('edge did not answer in time')

    monkeypatch.setattr(server.voice, 'speak', timing_out)
    status, body = post(base, port, '/api/speak', json.dumps({'text': 'ทดสอบ'}).encode())
    assert status == 503
    payload = json.loads(body)
    assert payload['error'] and 'audio/mpeg' not in str(payload)
    assert b'ID3' not in body, 'a timeout must not return fabricated audio'


def test_stt_busy_is_reported_as_error_never_as_transcript(guarded_server, monkeypatch):
    base, port, calls = guarded_server

    def busy(raw, mime):
        calls['listen'] += 1
        raise RuntimeError('transcription worker busy')

    monkeypatch.setattr(server.voice, 'transcribe', busy)
    status, body = post(base, port, '/api/listen', b'\x1aE\xdf\xa3fake',
                        content_type='audio/webm')
    assert status == 503
    payload = json.loads(body)
    assert payload['text'] == '' and payload['error']
    assert calls['listen'] == 1


def test_stt_timeout_of_an_oversized_or_empty_audio_never_calls_the_backend(guarded_server):
    base, port, calls = guarded_server
    status, _ = post(base, port, '/api/listen', b'', content_type='audio/webm')
    assert status == 400
    assert calls['listen'] == 0


# ------------------------------------------------------- temp retention ------
def test_managed_chat_probe_leaves_no_scratch_leftovers_and_touches_no_stores(monkeypatch):
    """Real managed Hermes worker (readiness proof only, zero inference).

    The managed child inherits pytest's ``PYTEST_CURRENT_TEST`` marker and
    Hermes's own hermetic-CI guard then refuses to open the real auth store.
    The pilot itself never runs under pytest, so the marker is dropped for this
    one child; the assertions below prove that neither auth store nor config
    was modified by the probe.
    """
    def count_entries():
        return len(os.listdir(SCRATCH)) if os.path.isdir(SCRATCH) else 0

    auth_path = os.path.expanduser('~/.hermes/auth.json')
    auth_before = sha256(auth_path) if os.path.exists(auth_path) else None
    config_before = sha256(CONFIG_PATH)
    before = count_entries()
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)
    bridge = agent_bridge.AgentBridge()          # real managed subprocess proof
    assert bridge.ready is True
    assert agent_bridge.proof_is_safe(bridge.readiness_proof)
    after = count_entries()
    assert after <= before, f'scratch grew by {after - before} entries after the probe'
    assert sha256(CONFIG_PATH) == config_before
    if auth_before is not None:
        assert sha256(auth_path) == auth_before, 'managed probe modified auth.json'
