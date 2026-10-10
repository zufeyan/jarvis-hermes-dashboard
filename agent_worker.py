"""Process-isolated Hermes conversation. No native registry dispatch, ever.

Two modes, selected by tools_gate.json (see docs/TOOLS_GATE_PLAN.md):
  * disabled -> chat-only: final tools == [] and every tool attempt aborts
  * enabled  -> read-only layer: the model may SEE exactly two pilot-owned
    read-only tools. Nothing executes in this process: a tool call stops the
    turn and comes back as a digest-bound approval request. Execution happens
    in the server process only after the user approves that exact action.
"""
import contextlib
import io
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tools_readonly  # noqa: E402
import tools_layer_b  # noqa: E402

APPROVAL_TTL = 90


class ToolAttempt(BaseException):
    """Abort even if the native loop catches normal tool exceptions."""


class ApprovalCapture(BaseException):
    """Model asked for an allowlisted tool — stop the turn, execute nothing."""

    def __init__(self, action, arguments):
        self.action = action
        # NOTE: BaseException.__init__ overwrites ``args`` with a tuple, so the
        # parsed tool arguments live under ``arguments``.
        self.arguments = arguments
        super().__init__(action)


def deny_tools(*args, **kwargs):
    raise ToolAttempt('tools disabled in this conversation')


def make_capture_gate(allowed):
    # Installed as an INSTANCE attribute (not a bound method), so the first
    # argument is the assistant message exactly as the native call site passes it.
    def gate(assistant_message, messages=None, task_id=None, api_call_count=0):
        calls = list(getattr(assistant_message, 'tool_calls', None) or [])
        if not calls:
            raise ToolAttempt('gate reached without tool calls')
        first = None
        for call in calls:
            name = getattr(getattr(call, 'function', None), 'name', None)
            raw = getattr(getattr(call, 'function', None), 'arguments', None) or '{}'
            if name not in allowed:
                raise ToolAttempt(f'tool not allowlisted: {name}')
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
            except ValueError:
                raise ToolAttempt('tool arguments are not valid JSON')
            if first is None:
                first = (name, args)
        raise ApprovalCapture(first[0], first[1])
    return gate


def _route_override(cfg):
    """Pilot-scoped provider/model override from the launcher environment.
    Never taken from a client request and never written to config.yaml."""
    provider = os.environ.get('JARVIS_CHAT_PROVIDER', '').strip()
    model = os.environ.get('JARVIS_CHAT_MODEL', '').strip()
    if not provider and not model:
        return '', ''
    if not provider or not model:
        raise RuntimeError('chat provider override requires both JARVIS_CHAT_PROVIDER and JARVIS_CHAT_MODEL')
    return provider, model


def active_surface():
    """Read-only layer and opt-in layer B, each behind its own checked-in gate.
    Nothing is attached unless a gate file enables it."""
    ro = tools_readonly.load_gate_config()
    lb = tools_layer_b.load_config()
    schemas = []
    if ro.get('enabled'):
        schemas += [json.loads(json.dumps(s)) for s in tools_readonly.SCHEMAS]
    if lb.get('enabled'):
        schemas += [json.loads(json.dumps(s)) for s in tools_layer_b.SCHEMAS]
    names = tuple(s.get('function', {}).get('name') for s in schemas)
    scope = ('layered-after-approval' if lb.get('enabled') else
             'read-only-after-approval' if ro.get('enabled') else 'chat-only')
    return {'ro': ro, 'lb': lb, 'schemas': schemas, 'names': names, 'scope': scope}


def assert_surface(agent, gate=None):
    """Fail closed unless the tool surface is exactly what the gates promise."""
    surface = active_surface()
    if not surface['names']:
        if agent.tools != [] or agent.valid_tool_names != set():
            raise RuntimeError('final constructed tool surface is not empty')
        return
    names = sorted(t.get('function', {}).get('name') for t in agent.tools)
    expected = sorted(surface['names'])
    if names != expected or agent.valid_tool_names != set(surface['names']):
        raise RuntimeError('tool surface drifted from the approved allowlist')


def construct_agent(gate=None):
    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from run_agent import AIAgent
    gate = gate if gate is not None else tools_readonly.load_gate_config()
    cfg = load_config()
    model_cfg = cfg.get('model', {})
    if isinstance(model_cfg, str):
        model_cfg = {'default': model_cfg}
    default = model_cfg.get('default') or model_cfg.get('model') or ''
    nested = default if isinstance(default, dict) else {}
    model = nested.get('model') or nested.get('name') or (default if isinstance(default, str) else '')
    provider = nested.get('provider') or model_cfg.get('provider') or 'auto'
    override_provider, override_model = _route_override(cfg)
    explicit_provider = override_provider or ''
    if override_model:
        model = override_model
    if override_provider:
        provider = override_provider
    from hermes_cli.model_switch import resolve_startup_model_route
    route = resolve_startup_model_route(model, explicit_provider=explicit_provider, current_provider=provider,
                                       user_providers=cfg.get('providers'), custom_providers=cfg.get('custom_providers'))
    route_kwargs = {}
    if route is not None:
        model, provider = route.model, route.provider
        route_kwargs = {'explicit_base_url': route.base_url or None, 'explicit_api_key': route.api_key or None}
    runtime = resolve_runtime_provider(requested=provider, target_model=model, **route_kwargs)
    kwargs = {k: runtime[k] for k in ('base_url', 'api_key', 'provider', 'api_mode', 'acp_command', 'acp_args', 'command', 'args') if k in runtime}
    # External agents can have their own tools outside this Python registry.
    if runtime.get('api_mode') in ('acp', 'codex_app_server') or runtime.get('command') or runtime.get('acp_command'):
        raise RuntimeError('external tool-owning runtime is not supported')
    agent = AIAgent(model=model, requested_provider=provider, enabled_toolsets=[],
                    skip_memory=True, skip_background_review=True, skip_context_files=True,
                    load_soul_identity=False, session_db=None, side_agent=True,
                    session_id='jarvis-chat-' + uuid.uuid4().hex, max_iterations=2,
                    max_tokens=1000, run_budget_seconds=100, quiet_mode=True,
                    save_trajectories=False, verbose_logging=False, checkpoints_enabled=False,
                    **kwargs)
    agent._persist_disabled = True
    agent._end_session_on_close = False
    # The surface is asserted AFTER install_gates(): when the read-only layer is
    # on, the allowlisted schemas are attached there, not here.
    return agent


def install_gates(agent, gate):
    """Instance-local and fail-closed. This process never executes a tool."""
    agent._execute_tool_calls_sequential = deny_tools
    agent._execute_tool_calls_concurrent = deny_tools
    agent._invoke_tool = deny_tools
    agent._dispatch_delegate_task = deny_tools
    surface = active_surface()
    if surface['names']:
        agent.tools = surface['schemas']
        agent.valid_tool_names = set(surface['names'])
        agent._execute_tool_calls = make_capture_gate(set(surface['names']))
    else:
        agent._execute_tool_calls = deny_tools
    assert_surface(agent, gate)


def proof(agent, gate=None):
    """Exercise the fail-closed gates on the actual constructed agent."""
    gate = gate if gate is not None else tools_readonly.load_gate_config()
    install_gates(agent, gate)
    tested = []
    for name in ('terminal', 'write_file', 'browser_exec', 'delegate_task', 'memory', 'execute_code'):
        call = SimpleNamespace(id='denial-' + name, type='function',
                               function=SimpleNamespace(name=name, arguments='{}'))
        messages = []
        try:
            agent._execute_tool_calls(SimpleNamespace(tool_calls=[call]), messages, agent.session_id, 0)
        except ToolAttempt:
            pass
        else:
            raise RuntimeError('capability gate failed')
        if messages:
            raise RuntimeError('capability gate failed')
        tested.append(name)
    surface = active_surface()
    if surface['names']:
        # An allowlisted forged call is CAPTURED, never executed.
        first = surface['names'][0]
        probe_args = ('{"path":"/etc/hosts"}' if first == 'read_text_file'
                      else '{"path":"escape/../probe","content":"x"}')
        call = SimpleNamespace(id='capture-probe', type='function',
                               function=SimpleNamespace(name=first, arguments=probe_args))
        try:
            agent._execute_tool_calls(SimpleNamespace(tool_calls=[call]), [], agent.session_id, 0)
        except ApprovalCapture as capture:
            if capture.action != first:
                raise RuntimeError('capture gate returned the wrong action')
        else:
            raise RuntimeError('capture gate did not stop the turn')
        assert_surface(agent, gate)
        return {'constructed': True, 'tools': list(surface['names']),
                'valid_tool_names': sorted(agent.valid_tool_names),
                'capability_denials': tested, 'execution': 'never_in_worker',
                'scope': surface['scope'], 'mock_mode': False}
    assert_surface(agent, gate)
    return {'constructed': True, 'tools': [], 'valid_tool_names': [],
            'capability_denials': tested, 'scope': 'chat-only', 'mock_mode': False}


def redact(text):
    """Never leak keys/tokens through a user-facing provider message."""
    text = re.sub(r'sk-[A-Za-z0-9_\-]+', '[REDACTED]', str(text))
    text = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._\-]+', r'\1[REDACTED]', text)
    text = re.sub(r'[A-Za-z0-9_\-]{40,}', '[REDACTED]', text)
    return ' '.join(text.split())[:600]


def classify_failure(result):
    """Map a provider/turn failure to an honest status + Thai-facing message."""
    raw_error = result.get('error') if isinstance(result, dict) else None
    if not raw_error:
        return None, None
    detail = redact(result.get('final_response') or raw_error)
    haystack = f'{raw_error} {detail}'
    if re.search(r'(?i)\b429\b|usage limit|rate[\s_-]?limit|quota', haystack):
        message = 'โหมดโมเดลติดโควตา (HTTP 429) — ยังสนทนาต่อไม่ได้ชั่วคราว'
        if detail:
            message += f' · {detail}'
        return 429, message
    if re.search(r'(?i)\b40[123]\b|credential|unauthorized|forbidden|billing|payment|402', haystack):
        return 503, 'ผู้ให้บริการโมเดลยืนยันตัวตนหรือการเรียกเก็บเงินไม่ผ่าน — ยังสนทนาไม่ได้ชั่วคราว'
    message = 'สนทนากับโมเดลไม่สำเร็จชั่วคราว (HTTP 503)'
    if detail:
        message += f' · {detail}'
    return 503, message


SYSTEM_CHAT = ('คุณคือจาร์วิส ตอบภาษาไทยอย่างกระชับในโหมดสนทนาเท่านั้น ห้ามอ้างว่าได้อ่านไฟล์ รันคำสั่ง ค้นเว็บ '
               'หรือเปลี่ยนระบบจริง ยกเว้นข้อความที่มีหัวข้อ [ผลการอนุมัติ read-only] ซึ่งเป็นผลจริงที่ระบบอ่านให้แล้ว')

SYSTEM_TOOLS = ('คุณคือจาร์วิส ตอบภาษาไทยอย่างกระชับ มีเครื่องมือ read-only สองตัว (read_text_file, search_text) '
                'ที่ใช้ได้เฉพาะเมื่อผู้ใช้อนุมัติ และอยู่ในโฟลเดอร์ที่อนุญาตเท่านั้น ถ้าคำตอบต้องอิงไฟล์จริง '
                'ให้เรียกเครื่องมือหนึ่งครั้งที่จำเป็นที่สุด แล้วรอผล ห้ามเรียก terminal เขียนไฟล์ หรือค้นเว็บ')

SYSTEM_TOOLS_B = ('คุณคือจาร์วิส ตอบภาษาไทยอย่างกระชับ มีเครื่องมือสี่ตัว: read_text_file, search_text '
                  '(อ่านในโฟลเดอร์ที่อนุญาต) และ write_text_file, run_command (ทำงานใน sandbox ชั้น B เท่านั้น) '
                  'ทุกตัวใช้ได้เฉพาะเมื่อผู้ใช้อนุมัติรายครั้ง ห้ามแกล้งอ้างว่าอ่านหรือเขียนไฟล์โดยไม่ได้รับผลจริง '
                  'ห้ามเรียกเครื่องมือที่ไม่อยู่ในรายการ')

ACTION_PREFIX = ('\n\n[ผลการอนุมัติ read-only]\nคำสั่งที่ผู้ใช้อนุมัติ: {action} {args}\nผลลัพธ์:\n{output}\n[/ผลการอนุมัติ read-only]\n\n'
                 'ตอบโดยอิงผลลัพธ์ข้างต้นเท่านั้น ถ้าข้อมูลไม่พอให้บอกตรง ๆ ห้ามเดา และห้ามแกล้งอ้างว่าอ่านไฟล์อื่น')


def run_turn(agent, message, history, gate):
    surface = active_surface()
    system = (SYSTEM_TOOLS_B if surface['scope'] == 'layered-after-approval' else
              SYSTEM_TOOLS if surface['scope'] == 'read-only-after-approval' else SYSTEM_CHAT)
    result = agent.run_conversation(message, conversation_history=history, system_message=system)
    assert_surface(agent, gate)
    return result


def build_approval(action, args):
    """Validate BEFORE showing anything to the user, then bind digest+expiry."""
    cfg = {}
    try:
        if action in tools_readonly.ALLOWED_NAMES:
            cfg = tools_readonly.load_gate_config()
            plan = tools_readonly.validate(action, args, cfg)
        elif action in tools_layer_b.NAMES:
            cfg = tools_layer_b.load_config()
            plan = tools_layer_b.validate(action, args, cfg)
        else:
            raise tools_readonly.ToolRefusal(403, 'เครื่องมือนี้ไม่อยู่ใน allowlist')
    except tools_readonly.ToolRefusal as refusal:
        # The guard working is NOT a failed conversation: report it as a
        # refusal the UI can explain, and never create an approval request.
        roots = []
        if action in tools_readonly.ALLOWED_NAMES:
            roots = [str(Path(r).name) or str(r) for r in cfg.get('roots', [])]
        elif action in tools_layer_b.NAMES:
            roots = [Path(cfg.get('sandbox_root') or '-').name] if cfg.get('enabled') else []
        return {'refused': True, 'action': action, 'reason': str(refusal),
                'status': refusal.status, 'allowed_roots': roots}
    clean_args = plan['args']
    request_id = uuid.uuid4().hex
    return {'needs_approval': True, 'approval': {
        'action': plan['action'], 'args': clean_args, 'request_id': request_id,
        'digest': tools_readonly.digest_of(request_id, plan['action'], clean_args),
        'expires_at': int(time.time()) + APPROVAL_TTL, 'ttl_seconds': APPROVAL_TTL}}


def handle(payload):
    gate = tools_readonly.load_gate_config()
    agent = construct_agent(gate)
    try:
        evidence = proof(agent, gate)
        if payload.get('op') == 'proof':
            return evidence
        if payload.get('op') not in ('run', 'answer_after_action'):
            raise ValueError('invalid operation')
        history = payload.get('history', [])
        if not isinstance(history, list) or len(history) > 20:
            raise ValueError('invalid history')
        if payload.get('op') == 'answer_after_action':
            message = str(payload.get('message', '')) + ACTION_PREFIX.format(
                action=str(payload.get('action', '')),
                args=json.dumps(payload.get('action_args', {}), ensure_ascii=False),
                output=str(payload.get('output', ''))[:60000])
        else:
            message = payload['message']
        try:
            result = run_turn(agent, message, history, gate)
        except ApprovalCapture as capture:
            return build_approval(capture.action, capture.arguments)
        status, message_text = classify_failure(result)
        if status:
            return {'error': message_text, 'error_status': status}
        reply = result.get('final_response')
        if not isinstance(reply, str) or not reply.strip():
            raise RuntimeError('Hermes did not return a successful answer')
        return {'reply': reply, 'session_id': agent.session_id,
                'tools_before': 0, 'tools_after': 0, 'proof': evidence}
    finally:
        agent.close()


if __name__ == '__main__':
    try:
        payload = json.load(sys.stdin)
        # Native diagnostics must not corrupt the JSON protocol or reveal secrets.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            response = handle(payload)
        print(json.dumps(response, ensure_ascii=False))
    except ToolAttempt as error:
        print(json.dumps({'error': 'tool call refused', 'detail': str(error)[:200], 'error_status': 403}))
        sys.exit(3)
    except Exception as error:
        print(json.dumps({'error': 'chat worker unavailable', 'error_type': type(error).__name__}))
        sys.exit(2)
