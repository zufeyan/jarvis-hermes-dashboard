import subprocess
import sys
import threading
import uuid
from types import SimpleNamespace
import pytest
import agent_bridge
import agent_worker


def uid():
    return str(uuid.uuid4())


def safe_proof():
    return {'constructed': True, 'tools': [], 'valid_tool_names': [],
            'capability_denials': agent_bridge.DENIED_TOOLS, 'scope': 'chat-only', 'mock_mode': False}


def test_readiness_requires_final_tools_and_exercised_gates():
    assert agent_bridge.proof_is_safe(safe_proof())
    for changes in ({'tools': [{'name': 'x'}]}, {'valid_tool_names': ['x']},
                    {'constructed': False}, {'capability_denials': []}, {'mock_mode': True}):
        assert not agent_bridge.proof_is_safe({**safe_proof(), **changes})
    with pytest.raises(ValueError):
        agent_bridge.load_proof({**safe_proof(), 'mock_mode': True})


def test_request_confirmation_uuid_command_and_message_guards():
    good = {'message': ' hello ', 'confirmed': True, 'client_id': uid(), 'request_id': uid()}
    assert agent_bridge.validate_run(good)[0] == ' hello '
    for changes in ({'confirmed': False}, {'client_id': 'x'}, {'request_id': 'x'},
                    {'message': '/new'}, {'message': '!ls'}, {'message': 'x' * 4001}, {'message': ''}):
        with pytest.raises(ValueError):
            agent_bridge.validate_run({**good, **changes})


def test_gate_refuses_before_original_executor_even_for_forged_tool_calls():
    invoked = []
    fake = SimpleNamespace(tools=[], valid_tool_names=set(), session_id='fixture',
                           _execute_tool_calls=lambda *a: invoked.append(a))
    evidence = agent_worker.proof(fake)
    import tools_readonly
    assert evidence['capability_denials'] == agent_bridge.DENIED_TOOLS
    assert evidence['mock_mode'] is False
    if tools_readonly.load_gate_config().get('enabled'):
        assert evidence['scope'] == 'read-only-after-approval'
        assert evidence['tools'] == list(tools_readonly.ALLOWED_NAMES)
    else:
        assert evidence == safe_proof()
    assert invoked == []
    for method in ('_execute_tool_calls', '_execute_tool_calls_sequential',
                   '_execute_tool_calls_concurrent', '_invoke_tool', '_dispatch_delegate_task'):
        with pytest.raises(agent_worker.ToolAttempt):
            getattr(fake, method)('forged call')


def test_actual_managed_hermes_constructs_empty_and_gates_all_tools():
    # No inference: real installed Hermes construction and actual gate exercise.
    bridge = agent_bridge.CHAT_BRIDGE
    assert bridge.ready
    assert agent_bridge.proof_is_safe(bridge.readiness_proof)


def test_isolated_history_replay_and_reset_generation():
    state = agent_bridge.BridgeState()
    a, b, rid = uid(), uid(), uid()
    generation = state.reserve(a, rid)
    state.append_turn(a, 'question', 'answer', generation)
    assert len(state.history(a)) == 2
    assert state.history(b) == []
    with pytest.raises(agent_bridge.BridgeError) as ex:
        state.reserve(b, rid)
    assert ex.value.status == 409
    state.reset(a)
    state.append_turn(a, 'stale', 'stale', generation)
    assert state.history(a) == []


def test_duplicate_run_does_not_leak_global_lock():
    def worker(payload, on_process=None):
        if payload['op'] == 'proof':
            return safe_proof()
        return {'reply': 'fixture', 'tools_before': 0, 'tools_after': 0, 'proof': safe_proof()}
    bridge = agent_bridge.AgentBridge(worker)
    cid, rid = uid(), uid()
    bridge.run('first', cid, rid)
    with pytest.raises(agent_bridge.BridgeError):
        bridge.run('replay', cid, rid)
    assert bridge.run('next', cid, uid())['reply'] == 'fixture'


def test_wrong_owner_cannot_cancel_and_real_process_stops_without_stale_history():
    started, finish = threading.Event(), threading.Event()
    results = []
    def worker(payload, on_process=None):
        if payload['op'] == 'proof':
            return safe_proof()
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'], start_new_session=True)
        on_process(proc)
        started.set()
        proc.wait(timeout=5)
        finish.wait(timeout=2)
        return {'reply': 'stale', 'tools_before': 0, 'tools_after': 0, 'proof': safe_proof()}
    bridge = agent_bridge.AgentBridge(worker)
    cid, other, rid = uid(), uid(), uid()
    def run():
        try:
            bridge.run('fixture', cid, rid)
        except agent_bridge.BridgeError as error:
            results.append(error.status)
    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(timeout=3)
    with pytest.raises(agent_bridge.BridgeError) as error:
        bridge.cancel(other, rid)
    assert error.value.status == 404
    assert bridge.new(other)['stopped'] is False
    receipt = bridge.cancel(cid, rid)
    assert receipt['stopped'] is True
    assert receipt['provider_rollback'] is False
    finish.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert bridge.state.history(cid) == []
    assert results == [409]


def test_safety_drift_disables_chat():
    def worker(payload, on_process=None):
        if payload['op'] == 'proof':
            return safe_proof()
        return {'reply': 'unsafe', 'tools_before': 1, 'tools_after': 0, 'proof': safe_proof()}
    bridge = agent_bridge.AgentBridge(worker)
    with pytest.raises(agent_bridge.BridgeError):
        bridge.run('fixture', uid(), uid())
    assert not bridge.ready
    assert not bridge.state.clients[next(iter(bridge.state.clients))].history


def test_provider_failures_map_to_honest_status_and_redact_secrets():
    assert agent_worker.classify_failure({'error': ''}) == (None, None)
    status, message = agent_worker.classify_failure({
        'error': 'HTTP 429: The usage limit has been reached',
        'final_response': 'usage limit resets in 93h sk-abcdefghijklmnop12345678901234567890'})
    assert status == 429
    assert 'ติดโควตา' in message and 'sk-' not in message
    status, message = agent_worker.classify_failure({'error': 'HTTP 403 credential rejected'})
    assert status == 503 and 'ยืนยันตัวตน' in message
    status, message = agent_worker.classify_failure({'error': 'boom', 'final_response': 'x' * 900})
    assert status == 503 and len(message) <= 640


def test_scope_provider_override_never_comes_from_client_and_needs_both(monkeypatch):
    monkeypatch.delenv('JARVIS_CHAT_PROVIDER', raising=False)
    monkeypatch.delenv('JARVIS_CHAT_MODEL', raising=False)
    assert agent_worker._route_override({}) == ('', '')
    monkeypatch.setenv('JARVIS_CHAT_PROVIDER', 'xiaomi')
    with pytest.raises(RuntimeError):
        agent_worker._route_override({})
    monkeypatch.setenv('JARVIS_CHAT_MODEL', 'mimo-v2.6-flash')
    assert agent_worker._route_override({}) == ('xiaomi', 'mimo-v2.6-flash')


def test_worker_provider_error_status_becomes_http_status_not_generic_503():
    def worker(payload, on_process=None):
        if payload['op'] == 'proof':
            return safe_proof()
        return {'error': 'โหมดโมเดลติดโควตา (HTTP 429) — ยังสนทนาต่อไม่ได้ชั่วคราว', 'error_status': 429}
    bridge = agent_bridge.AgentBridge(worker)
    with pytest.raises(agent_bridge.BridgeError) as ex:
        bridge.run('fixture', uid(), uid())
    assert ex.value.status == 429
    assert 'ติดโควตา' in str(ex.value)
