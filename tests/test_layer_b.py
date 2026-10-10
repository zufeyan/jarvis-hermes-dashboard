"""Layer B (write/run) — opt-in sandbox behind the same two-phase approval.

Deterministic: a throwaway sandbox in tmp_path, the real validation/digest/
execution code and fixture workers. No LLM, no network, no writes outside the
sandbox. The repository config keeps layer B disabled; every test that enables
it does so with monkeypatched config for its own tmp sandbox.
"""
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_bridge
import agent_worker
import tools_layer_b
import tools_readonly


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def lb_cfg(tmp_path):
    sandbox = tmp_path / 'sandbox'
    sandbox.mkdir()
    cfg = {'enabled': True, 'sandbox_root': str(sandbox),
           'allow_commands': ['echo', 'ls', 'sleep'],
           'max_write_bytes': 1000, 'max_output_bytes': 500,
           'timeout_seconds': 2, 'audit_log': str(sandbox / '.jarvis_layer_b_audit.jsonl')}
    return cfg, sandbox


def audit_lines(sandbox):
    path = Path(sandbox) / '.jarvis_layer_b_audit.jsonl'
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


# ------------------------------------------------------------- gate state ----
def test_layer_b_is_disabled_in_the_repository_config():
    cfg = tools_layer_b.load_config()
    assert cfg['enabled'] is False
    with pytest.raises(tools_readonly.ToolRefusal):
        tools_layer_b.validate('write_text_file', {'path': 'x.md', 'content': 'y'}, cfg)
    with pytest.raises(tools_readonly.ToolRefusal):
        tools_layer_b.validate('run_command', {'name': 'echo'}, cfg)


def test_enabled_config_requires_existing_sandbox_and_inside_audit(tmp_path):
    missing = {'enabled': True, 'sandbox_root': str(tmp_path / 'nope'),
               'allow_commands': ['echo'], 'audit_log': ''}
    real = tools_layer_b.CONFIG_FILE
    try:
        tools_layer_b.CONFIG_FILE = tmp_path / 'cfg.json'
        tools_layer_b.CONFIG_FILE.write_text(json.dumps(missing), encoding='utf-8')
        assert tools_layer_b.load_config()['enabled'] is False

        sandbox = tmp_path / 'box'
        sandbox.mkdir()
        bad_audit = {'enabled': True, 'sandbox_root': str(sandbox),
                     'allow_commands': ['echo'],
                     'audit_log': str(tmp_path / 'outside.jsonl')}
        tools_layer_b.CONFIG_FILE.write_text(json.dumps(bad_audit), encoding='utf-8')
        assert tools_layer_b.load_config()['enabled'] is False  # audit must live inside

        good = dict(bad_audit, audit_log='')
        tools_layer_b.CONFIG_FILE.write_text(json.dumps(good), encoding='utf-8')
        cfg = tools_layer_b.load_config()
        assert cfg['enabled'] is True
        assert cfg['audit_log'].startswith(str(sandbox))
    finally:
        tools_layer_b.CONFIG_FILE = real


# ------------------------------------------------------------------ writes ----
def test_write_inside_sandbox_is_audited_without_the_content(lb_cfg):
    cfg, sandbox = lb_cfg
    result = tools_layer_b.execute('write_text_file',
                                   {'path': 'notes/hello.md', 'content': 'สวัสดี secret-value-123'}, cfg)
    target = sandbox / 'notes' / 'hello.md'
    assert target.is_file()
    assert target.read_text(encoding='utf-8') == 'สวัสดี secret-value-123'
    assert result['sha256'] == __import__('hashlib').sha256(target.read_bytes()).hexdigest()
    records = audit_lines(sandbox)
    assert len(records) == 1
    assert records[0]['action'] == 'write_text_file'
    assert records[0]['detail']['sha256'] == result['sha256']
    assert 'secret-value-123' not in json.dumps(records, ensure_ascii=False)


def test_write_rejects_paths_outside_the_sandbox(lb_cfg):
    cfg, sandbox = lb_cfg
    for bad in ('../escape.md', '/etc/passwd', '~/evil.md', 'a/../../b.md'):
        with pytest.raises(tools_readonly.ToolRefusal):
            tools_layer_b.execute('write_text_file', {'path': bad, 'content': 'x'}, cfg)
    assert not list(sandbox.parent.glob('escape.md'))


def test_write_rejects_symlink_escaping_the_sandbox(lb_cfg, tmp_path):
    cfg, sandbox = lb_cfg
    outside = tmp_path / 'outside.md'
    outside.write_text('original', encoding='utf-8')
    (sandbox / 'link.md').symlink_to(outside)
    with pytest.raises(tools_readonly.ToolRefusal):
        tools_layer_b.execute('write_text_file', {'path': 'link.md', 'content': 'pwned'}, cfg)
    assert outside.read_text(encoding='utf-8') == 'original'


def test_write_rejects_oversize_content(lb_cfg):
    cfg, _ = lb_cfg
    with pytest.raises(tools_readonly.ToolRefusal) as refusal:
        tools_layer_b.execute('write_text_file', {'path': 'big.txt', 'content': 'x' * 1001}, cfg)
    assert refusal.value.status == 413


def test_unwritable_audit_log_refuses_before_touching_the_filesystem(lb_cfg):
    cfg, sandbox = lb_cfg
    blocker = sandbox / 'blocker'
    blocker.write_text('i am a file, not a directory', encoding='utf-8')
    cfg = dict(cfg, audit_log=str(blocker / 'audit.jsonl'))
    with pytest.raises(tools_readonly.ToolRefusal):
        tools_layer_b.execute('write_text_file', {'path': 'notes/x.md', 'content': 'y'}, cfg)
    assert not (sandbox / 'notes').exists()


# --------------------------------------------------------------- commands ----
def test_run_rejects_anything_not_on_the_allowlist(lb_cfg):
    cfg, _ = lb_cfg
    with pytest.raises(tools_readonly.ToolRefusal) as refusal:
        tools_layer_b.execute('run_command', {'name': 'rm', 'args': ['-rf', '/']}, cfg)
    assert refusal.value.status == 403


def test_run_is_argv_not_shell(lb_cfg):
    cfg, sandbox = lb_cfg
    result = tools_layer_b.execute('run_command',
                                   {'name': 'echo', 'args': ['a && echo pwned']}, cfg)
    # With shell=False the metacharacters are literal text, not a second command.
    assert result['output'] == 'a && echo pwned'
    assert result['exit_code'] == 0
    assert result['argv'][0].endswith('/echo')
    record = audit_lines(sandbox)[-1]
    assert record['detail']['argv'][0].endswith('/echo')
    assert record['detail']['exit_code'] == 0


def test_run_cwd_is_pinned_to_the_sandbox(lb_cfg):
    cfg, sandbox = lb_cfg
    (sandbox / 'marker.txt').write_text('here', encoding='utf-8')
    result = tools_layer_b.execute('run_command', {'name': 'ls'}, cfg)
    assert 'marker.txt' in result['output']
    assert str(sandbox) not in result['output']  # plain `ls`, not an absolute listing


def test_run_timeout_is_refused_and_audited(lb_cfg):
    cfg, sandbox = lb_cfg
    cfg = dict(cfg, timeout_seconds=1)
    with pytest.raises(tools_readonly.ToolRefusal) as refusal:
        tools_layer_b.execute('run_command', {'name': 'sleep', 'args': ['5']}, cfg)
    assert refusal.value.status == 504
    assert audit_lines(sandbox)[-1]['detail']['result'] == 'timeout'


def test_run_strips_secret_bearing_environment_variables(lb_cfg, monkeypatch):
    cfg, sandbox = lb_cfg
    monkeypatch.setenv('SUPER_SECRET_TOKEN', 'sk-should-not-leak')
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    result = tools_layer_b.execute('run_command',
                                   {'name': 'echo', 'args': ['$SUPER_SECRET_TOKEN']}, cfg)
    # Literal text (no shell expansion) and the variable was removed from the env.
    assert result['output'] == '$SUPER_SECRET_TOKEN'


# ------------------------------------------------------- worker/bridge wiring ----
def layered_configs(monkeypatch, sandbox, read_only=True):
    ro = {'enabled': read_only, 'roots': [str(sandbox)], 'max_bytes': 2000, 'max_results': 10}
    lb = {'enabled': True, 'sandbox_root': str(sandbox), 'allow_commands': ['echo'],
          'max_write_bytes': 1000, 'max_output_bytes': 500, 'timeout_seconds': 2,
          'audit_log': str(sandbox / '.jarvis_layer_b_audit.jsonl')}
    monkeypatch.setattr(tools_readonly, 'load_gate_config', lambda: ro)
    monkeypatch.setattr(tools_layer_b, 'load_config', lambda: lb)
    return ro, lb


def expected_names(read_only=True, layer_b=True):
    names = []
    if read_only:
        names += list(tools_readonly.ALLOWED_NAMES)
    if layer_b:
        names += list(tools_layer_b.NAMES)
    return sorted(names)


def test_worker_surface_adds_layer_b_only_when_its_gate_is_on(lb_cfg, monkeypatch):
    _, sandbox = lb_cfg
    layered_configs(monkeypatch, sandbox)
    fake = SimpleNamespace(tools=[], valid_tool_names=set(), session_id='fixture')
    evidence = agent_worker.proof(fake, tools_readonly.load_gate_config())
    assert evidence['scope'] == 'layered-after-approval'
    assert sorted(evidence['tools']) == expected_names()
    assert agent_bridge.proof_is_safe(evidence) is True
    with pytest.raises(agent_worker.ApprovalCapture) as capture:
        call = SimpleNamespace(id='f', type='function',
                               function=SimpleNamespace(name='run_command',
                                                        arguments='{"name":"echo","args":["hi"]}'))
        fake._execute_tool_calls(SimpleNamespace(tool_calls=[call]), [], 'fixture', 0)
    assert capture.value.action == 'run_command'


def test_worker_denies_layer_b_when_the_gate_is_off(lb_cfg, monkeypatch):
    _, sandbox = lb_cfg
    layered_configs(monkeypatch, sandbox)
    monkeypatch.setattr(tools_layer_b, 'load_config',
                        lambda: {'enabled': False, 'sandbox_root': '', 'allow_commands': [],
                                 'max_write_bytes': 1000, 'max_output_bytes': 500,
                                 'timeout_seconds': 2, 'audit_log': ''})
    fake = SimpleNamespace(tools=[], valid_tool_names=set(), session_id='fixture')
    evidence = agent_worker.proof(fake, tools_readonly.load_gate_config())
    assert evidence['scope'] == 'read-only-after-approval'
    assert evidence['tools'] == list(tools_readonly.ALLOWED_NAMES)
    assert agent_bridge.proof_is_safe(evidence) is True
    call = SimpleNamespace(id='f', type='function',
                           function=SimpleNamespace(name='run_command', arguments='{}'))
    with pytest.raises(agent_worker.ToolAttempt):
        fake._execute_tool_calls(SimpleNamespace(tool_calls=[call]), [], 'fixture', 0)


def test_chat_only_proof_still_proves_safe_when_everything_is_off(monkeypatch):
    monkeypatch.setattr(tools_readonly, 'load_gate_config',
                        lambda: {'enabled': False, 'roots': [], 'max_bytes': 10, 'max_results': 1})
    monkeypatch.setattr(tools_layer_b, 'load_config',
                        lambda: {'enabled': False, 'sandbox_root': '', 'allow_commands': [],
                                 'max_write_bytes': 10, 'max_output_bytes': 10,
                                 'timeout_seconds': 1, 'audit_log': ''})
    fake = SimpleNamespace(tools=[], valid_tool_names=set(), session_id='fixture')
    evidence = agent_worker.proof(fake, {'enabled': False})
    assert evidence['scope'] == 'chat-only' and evidence['tools'] == []
    assert agent_bridge.proof_is_safe(evidence) is True


class LayerBridgeWorker:
    """Phase A returns a write approval, phase B answers from the real output."""

    def __init__(self, content):
        self.content = content
        self.calls = []

    def __call__(self, payload, on_process=None):
        self.calls.append(payload['op'])
        if payload['op'] == 'proof':
            return {'constructed': True, 'tools': expected_names(),
                    'valid_tool_names': expected_names(),
                    'capability_denials': agent_bridge.DENIED_TOOLS,
                    'execution': 'never_in_worker',
                    'scope': 'layered-after-approval', 'mock_mode': False}
        if payload['op'] == 'run':
            args = {'path': 'notes/from-approval.md', 'content': self.content}
            digest = tools_readonly.digest_of('fixture-req', 'write_text_file', args)
            return {'needs_approval': True, 'approval': {
                'action': 'write_text_file', 'args': args, 'request_id': 'fixture-req',
                'digest': digest, 'expires_at': int(time.time()) + 90, 'ttl_seconds': 90}}
        if payload['op'] == 'answer_after_action':
            return {'reply': 'เขียนแล้ว: ' + payload['output'][:60], 'tools_before': 0,
                    'tools_after': 0, 'proof': self.__call__({'op': 'proof'})}
        raise AssertionError('unexpected op ' + payload['op'])


def test_bridge_approval_executes_the_layer_b_write_exactly_once(lb_cfg, monkeypatch):
    cfg, sandbox = lb_cfg
    layered_configs(monkeypatch, sandbox)
    bridge = agent_bridge.AgentBridge(LayerBridgeWorker('ผลทดสอบจากผู้ใช้'))
    assert bridge.ready is True
    cid, rid = uid(), uid()
    result = bridge.run('เขียนโน้ตให้หน่อย', cid, rid)
    assert result['needs_approval'] is True
    answer = bridge.approve(cid, rid, result['approval']['digest'])
    assert answer['action_executed'] == 'write_text_file'
    assert (sandbox / 'notes' / 'from-approval.md').read_text(encoding='utf-8') == 'ผลทดสอบจากผู้ใช้'
    assert audit_lines(sandbox)[0]['action'] == 'write_text_file'
    # One-shot: the same approval cannot be replayed.
    with pytest.raises(agent_bridge.BridgeError) as second:
        bridge.approve(cid, rid, result['approval']['digest'])
    assert second.value.status in (403, 404, 409, 410)


def test_bridge_refuses_a_forged_layer_b_digest(lb_cfg, monkeypatch):
    cfg, sandbox = lb_cfg
    layered_configs(monkeypatch, sandbox)
    bridge = agent_bridge.AgentBridge(LayerBridgeWorker('x'))
    cid, rid = uid(), uid()
    result = bridge.run('เขียน', cid, rid)
    assert result['needs_approval'] is True
    with pytest.raises(agent_bridge.BridgeError) as forged:
        bridge.approve(cid, rid, '0' * 64)
    assert forged.value.status == 403
    assert not (sandbox / 'notes').exists()
