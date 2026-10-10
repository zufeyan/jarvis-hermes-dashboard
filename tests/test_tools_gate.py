"""Layer A: read-only tools behind per-action approval (see docs/TOOLS_GATE_PLAN.md).

Everything here is deterministic: fixture agents/workers, a tmp allowlisted root
and the real validation/digest/approval code. No LLM, no microphone, no writes
outside pytest's tmp_path.
"""
import json
import os
import time
import uuid
from types import SimpleNamespace

import pytest

import agent_bridge
import agent_worker
import tools_readonly
import server


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def gate_cfg(tmp_path, monkeypatch):
    """Allowlist that points at a throwaway root."""
    root = tmp_path / 'allowed'
    (root / 'docs').mkdir(parents=True)
    (root / 'docs' / 'quickstart.md').write_text('# เริ่มใช้งาน\npython3 start-thai.py --no-browser\nport 8730\n', encoding='utf-8')
    (root / 'secret.bin').write_bytes(b'\x00\x01binary\xff')
    (root / 'big.txt').write_text('x' * 5000, encoding='utf-8')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'passwd.txt').write_text('must not be readable', encoding='utf-8')
    cfg = {'enabled': True, 'roots': [str(root)], 'max_bytes': 2000, 'max_results': 10}
    monkeypatch.setattr(tools_readonly, 'load_gate_config', lambda: cfg)
    return cfg, root, outside


def safe_proof():
    return {'constructed': True, 'tools': [], 'valid_tool_names': [],
            'capability_denials': agent_bridge.DENIED_TOOLS, 'scope': 'chat-only', 'mock_mode': False}


# ------------------------------------------------------------ validation -----
def test_paths_outside_the_allowlist_are_refused(gate_cfg):
    cfg, root, outside = gate_cfg
    for bad in (str(outside / 'passwd.txt'), '/etc/passwd', 'docs/quickstart.md',
                str(root / '..' / 'outside' / 'passwd.txt')):
        with pytest.raises(tools_readonly.ToolRefusal):
            tools_readonly.validate('read_text_file', {'path': bad}, cfg)
    with pytest.raises(tools_readonly.ToolRefusal):
        tools_readonly.validate('read_text_file', {'path': str(root / 'missing.md')}, cfg)


def test_symlink_escaping_the_root_is_refused(gate_cfg, tmp_path):
    cfg, root, outside = gate_cfg
    link = root / 'escape.md'
    link.symlink_to(outside / 'passwd.txt')
    with pytest.raises(tools_readonly.ToolRefusal):
        tools_readonly.validate('read_text_file', {'path': str(link)}, cfg)


def test_size_and_binary_bounds(gate_cfg):
    cfg, root, _ = gate_cfg
    with pytest.raises(tools_readonly.ToolRefusal) as big:
        tools_readonly.validate('read_text_file', {'path': str(root / 'big.txt')}, cfg)
    assert big.value.status == 413
    # Binary files pass size validation but are refused at execution time.
    with pytest.raises(tools_readonly.ToolRefusal) as binary:
        tools_readonly.execute('read_text_file', {'path': str(root / 'secret.bin')}, cfg)
    assert binary.value.status == 415


def test_search_bounds_and_pattern_validation(gate_cfg):
    cfg, root, outside = gate_cfg
    ok = tools_readonly.validate('search_text', {'pattern': 'start-thai', 'root': str(root)}, cfg)
    assert ok['action'] == 'search_text'
    for bad in ({'pattern': '(', 'root': str(root)},
                {'pattern': 'x' * 300, 'root': str(root)},
                {'pattern': 'x', 'root': str(outside)},
                {'pattern': 'x', 'root': str(root), 'limit': 0},
                {'pattern': 'x', 'root': str(root), 'limit': 999},
                {'pattern': 'x', 'root': str(root), 'glob': '../../**'}):
        with pytest.raises(tools_readonly.ToolRefusal):
            tools_readonly.validate('search_text', bad, cfg)


def test_execute_returns_real_content_only_inside_the_root(gate_cfg):
    cfg, root, _ = gate_cfg
    result = tools_readonly.execute('read_text_file', {'path': str(root / 'docs' / 'quickstart.md')}, cfg)
    assert 'python3 start-thai.py' in result['output']
    hits = tools_readonly.execute('search_text', {'pattern': 'start-thai', 'root': str(root)}, cfg)
    assert hits['hits'] >= 1 and 'quickstart.md' in hits['output']


def test_disabled_gate_refuses_everything(gate_cfg, monkeypatch):
    cfg, root, _ = gate_cfg
    monkeypatch.setitem(cfg, 'enabled', False)
    with pytest.raises(tools_readonly.ToolRefusal):
        tools_readonly.validate('read_text_file', {'path': str(root / 'docs' / 'quickstart.md')}, cfg)


# --------------------------------------------------------------- digest -----
def test_digest_binds_action_arguments_and_request_id(gate_cfg):
    cfg, root, _ = gate_cfg
    args = {'path': str(root / 'docs' / 'quickstart.md')}
    plan = tools_readonly.validate('read_text_file', args, cfg)
    base = tools_readonly.digest_of('req-1', 'read_text_file', plan['args'])
    assert base == tools_readonly.digest_of('req-1', 'read_text_file', dict(plan['args']))
    assert base != tools_readonly.digest_of('req-2', 'read_text_file', plan['args'])
    assert base != tools_readonly.digest_of('req-1', 'search_text', plan['args'])
    tampered = dict(plan['args']); tampered['path'] = str(root / 'docs' / '..' / 'docs' / 'quickstart.md')
    assert base != tools_readonly.digest_of('req-1', 'read_text_file', tampered)


# ----------------------------------------------------------- worker gates ---
def test_worker_surface_is_exactly_the_allowlist_and_never_executes(gate_cfg):
    cfg, root, _ = gate_cfg
    fake = SimpleNamespace(tools=[], valid_tool_names=set(), session_id='fixture')
    evidence = agent_worker.proof(fake, cfg)
    assert evidence['tools'] == list(tools_readonly.ALLOWED_NAMES)
    assert evidence['execution'] == 'never_in_worker'
    assert evidence['capability_denials'] == agent_bridge.DENIED_TOOLS
    for method in ('_execute_tool_calls_sequential', '_execute_tool_calls_concurrent',
                   '_invoke_tool', '_dispatch_delegate_task'):
        with pytest.raises(agent_worker.ToolAttempt):
            getattr(fake, method)('forged')
    call = SimpleNamespace(id='x', type='function',
                           function=SimpleNamespace(name='terminal', arguments='{}'))
    with pytest.raises(agent_worker.ToolAttempt):
        fake._execute_tool_calls(SimpleNamespace(tool_calls=[call]), [], 'fixture', 0)
    allowed = SimpleNamespace(id='y', type='function', function=SimpleNamespace(
        name='read_text_file', arguments=json.dumps({'path': str(root / 'docs' / 'quickstart.md')})))
    with pytest.raises(agent_worker.ApprovalCapture) as capture:
        fake._execute_tool_calls(SimpleNamespace(tool_calls=[allowed]), [], 'fixture', 0)
    assert capture.value.action == 'read_text_file'
    assert agent_bridge.proof_is_safe(evidence) is True


def test_worker_proof_with_gate_disabled_stays_chat_only(monkeypatch):
    monkeypatch.setattr(tools_readonly, 'load_gate_config',
                        lambda: {'enabled': False, 'roots': [], 'max_bytes': 10, 'max_results': 1})
    fake = SimpleNamespace(tools=[], valid_tool_names=set(), session_id='fixture')
    evidence = agent_worker.proof(fake, {'enabled': False, 'roots': [], 'max_bytes': 10, 'max_results': 1})
    assert evidence['tools'] == [] and evidence['scope'] == 'chat-only'
    assert agent_bridge.proof_is_safe(evidence) is True


# ----------------------------------------------------------- bridge flow ----
class FixtureBridgeWorker:
    """Phase A returns an approval request, phase B answers."""

    def __init__(self, target_path, second_step_needs_approval=False):
        self.target_path = target_path
        self.second = second_step_needs_approval
        self.calls = []

    def __call__(self, payload, on_process=None):
        self.calls.append(payload['op'])
        if payload['op'] == 'proof':
            return safe_proof()
        if payload['op'] == 'run':
            args = {'path': self.target_path}
            digest = tools_readonly.digest_of('fixture-req', 'read_text_file', args)
            return {'needs_approval': True, 'approval': {
                'action': 'read_text_file', 'args': args, 'request_id': 'fixture-req',
                'digest': digest, 'expires_at': int(time.time()) + 90, 'ttl_seconds': 90}}
        if payload['op'] == 'answer_after_action':
            if self.second:
                return {'needs_approval': True, 'approval': {
                    'action': 'search_text', 'args': {'pattern': 'x', 'root': os.path.dirname(self.target_path)},
                    'request_id': 'fixture-req-2', 'digest': 'b' * 64,
                    'expires_at': int(time.time()) + 90, 'ttl_seconds': 90}}
            return {'reply': 'อ่านไฟล์แล้ว: ' + payload['output'][:40], 'tools_before': 0,
                    'tools_after': 0, 'proof': safe_proof()}
        raise AssertionError('unexpected op ' + payload['op'])


def make_bridge(worker):
    bridge = agent_bridge.AgentBridge(worker)
    assert bridge.ready is True
    return bridge


def test_approval_flow_executes_exactly_once_and_answers(gate_cfg):
    cfg, root, _ = gate_cfg
    worker = FixtureBridgeWorker(str(root / 'docs' / 'quickstart.md'))
    bridge = make_bridge(worker)
    cid, rid = uid(), uid()
    result = bridge.run('อ่าน quickstart ให้หน่อย', cid, rid)
    assert result['needs_approval'] is True
    approval = result['approval']
    assert approval['action'] == 'read_text_file'
    answer = bridge.approve(cid, rid, approval['digest'])
    assert answer['action_executed'] == 'read_text_file'
    assert 'อ่านไฟล์แล้ว' in answer['reply']
    assert 'answer_after_action' in worker.calls
    assert len(bridge.state.history(cid)) == 2, 'the approved turn must be recorded'
    # one-shot: the same approval cannot be replayed
    with pytest.raises(agent_bridge.BridgeError) as again:
        bridge.approve(cid, rid, approval['digest'])
    assert again.value.status == 404


def test_approval_rejects_wrong_digest_owner_and_expiry(gate_cfg):
    cfg, root, _ = gate_cfg
    worker = FixtureBridgeWorker(str(root / 'docs' / 'quickstart.md'))
    bridge = make_bridge(worker)
    cid, other, rid = uid(), uid(), uid()
    approval = bridge.run('อ่านไฟล์', cid, rid)['approval']
    with pytest.raises(agent_bridge.BridgeError) as wrong_owner:
        bridge.approve(other, rid, approval['digest'])
    assert wrong_owner.value.status == 404
    with pytest.raises(agent_bridge.BridgeError) as wrong_digest:
        bridge.approve(cid, rid, 'c' * 64)
    assert wrong_digest.value.status == 403
    bridge.state.pending['expires_at'] = int(time.time()) - 1
    with pytest.raises(agent_bridge.BridgeError) as expired:
        bridge.approve(cid, rid, approval['digest'])
    assert expired.value.status == 410
    assert worker.calls.count('answer_after_action') == 0


def test_second_approval_step_is_shown_not_executed_silently(gate_cfg):
    cfg, root, _ = gate_cfg
    worker = FixtureBridgeWorker(str(root / 'docs' / 'quickstart.md'), second_step_needs_approval=True)
    bridge = make_bridge(worker)
    cid, rid = uid(), uid()
    approval = bridge.run('อ่านไฟล์', cid, rid)['approval']
    second = bridge.approve(cid, rid, approval['digest'])
    assert second['needs_approval'] is True
    assert second['approval']['action'] == 'search_text'
    assert bridge.state.pending is not None


def test_deny_and_new_clear_the_pending_approval(gate_cfg):
    cfg, root, _ = gate_cfg
    bridge = make_bridge(FixtureBridgeWorker(str(root / 'docs' / 'quickstart.md')))
    cid, rid = uid(), uid()
    approval = bridge.run('อ่านไฟล์', cid, rid)['approval']
    assert bridge.deny(cid, rid) == {'ok': True, 'denied': True, 'executed': False}
    with pytest.raises(agent_bridge.BridgeError) as missing:
        bridge.approve(cid, rid, approval['digest'])
    assert missing.value.status == 404
    bridge.run('อีกครั้ง', cid, uid())
    bridge.new(cid)
    assert bridge.state.pending is None


# --------------------------------------------------------------- endpoints --
@pytest.fixture
def api(monkeypatch):
    class CountingBridge:
        ready = True
        def __init__(self):
            self.calls = []
        def approve(self, client, request, digest):
            self.calls.append(('approve', client, request, digest))
            return {'reply': 'ok', 'session_id': 's', 'request_id': request, 'action_executed': 'read_text_file'}
        def deny(self, client, request):
            self.calls.append(('deny', client, request))
            return {'ok': True, 'denied': True, 'executed': False}
        def run(self, *a):
            raise AssertionError('not used here')
    bridge = CountingBridge()
    monkeypatch.setattr(server, 'CHAT_BRIDGE', bridge)
    old = server.API_TOKEN
    server.API_TOKEN = 'gate-token'
    from http.server import ThreadingHTTPServer
    import threading
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{port}', port, bridge
    httpd.shutdown(); httpd.server_close(); server.API_TOKEN = old


def _post(base, port, path, payload):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(), method='POST',
                                 headers={'Origin': base, 'X-Jarvis-Token': 'gate-token',
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_approval_endpoints_validate_before_touching_the_bridge(api):
    base, port, bridge = api
    good = {'client_id': uid(), 'request_id': uid(), 'digest': 'a' * 64}
    status, _ = _post(base, port, '/api/approve', good)
    assert status == 200 and bridge.calls == [('approve', good['client_id'], good['request_id'], 'a' * 64)]
    for bad in ({'client_id': uid(), 'request_id': uid()},
                {'client_id': uid(), 'request_id': uid(), 'digest': 'zz'},
                {'client_id': 'nope', 'request_id': uid(), 'digest': 'a' * 64},
                {'client_id': uid(), 'request_id': uid(), 'digest': 'a' * 64, 'extra': 1}):
        status, body = _post(base, port, '/api/approve', bad)
        assert status == 400 and 'error' in body
    status, body = _post(base, port, '/api/deny', {'client_id': uid(), 'request_id': uid()})
    assert status == 200 and body['executed'] is False
    assert [c[0] for c in bridge.calls] == ['approve', 'deny'], 'invalid payloads must never reach the bridge'


def test_status_reports_the_read_only_layer(api, gate_cfg):
    base, _port, _bridge = api
    import urllib.request
    status = json.loads(urllib.request.urlopen(base + '/api/status', timeout=5).read())
    assert status['tools_enabled'] is False
    assert status['scope'] == 'chat-only'
    assert status['tools_read_only'] is True and status['tools_layer'] == 'read_only-after-approval'
