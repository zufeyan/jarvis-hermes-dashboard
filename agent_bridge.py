"""Bounded, process-isolated chat bridge. No arbitrary command or tool API."""
from __future__ import annotations
import json
import hmac
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import tools_readonly  # noqa: E402

HERMES_SOURCE = Path.home() / '.hermes' / 'hermes-agent'
SCRATCH = Path.home() / '.hermes' / 'cache' / 'scratch'
UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
MAX_CLIENTS, MAX_TURNS, RUN_TTL = 16, 10, 120
DENIED_TOOLS = ['terminal', 'write_file', 'browser_exec', 'delegate_task', 'memory', 'execute_code']

class BridgeError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)

def load_proof(value):
    if not isinstance(value, dict) or value.get('mock_mode') is not False:
        raise ValueError('invalid readiness proof')
    return value

def proof_is_safe(proof):
    if (not isinstance(proof, dict) or proof.get('constructed') is not True
            or proof.get('capability_denials') != DENIED_TOOLS
            or proof.get('mock_mode') is not False):
        return False
    if proof.get('scope') == 'chat-only':
        return proof.get('tools') == [] and proof.get('valid_tool_names') == []
    if proof.get('scope') == 'read-only-after-approval':
        expected = sorted(tools_readonly.ALLOWED_NAMES)
        return (sorted(proof.get('tools') or []) == expected
                and sorted(proof.get('valid_tool_names') or []) == expected
                and proof.get('execution') == 'never_in_worker'
                and tools_readonly.load_gate_config().get('enabled') is True)
    return False

def validate_ids(payload, fields):
    if not isinstance(payload, dict) or set(payload) != set(fields):
        raise ValueError('invalid request schema')
    for name in fields:
        if not isinstance(payload[name], str) or not UUID_RE.fullmatch(payload[name]):
            raise ValueError('invalid identifier')
    return tuple(payload[name] for name in fields)

def validate_run(payload):
    if not isinstance(payload, dict) or set(payload) != {'message', 'confirmed', 'client_id', 'request_id'}:
        raise ValueError('invalid request schema')
    msg = payload['message']
    if payload['confirmed'] is not True or not isinstance(msg, str) or not 1 <= len(msg.strip()) <= 4000:
        raise ValueError('confirmation and 1-4000 characters required')
    client, request = validate_ids({k: payload[k] for k in ('client_id', 'request_id')}, ('client_id', 'request_id'))
    if msg.lstrip().startswith(('/', '!')):
        raise ValueError('slash and shell commands not supported')
    return msg, client, request

class Client:
    def __init__(self):
        self.history = deque(maxlen=MAX_TURNS * 2)
        self.generation = 0
        self.active = None
        self.session_id = 'jarvis-chat-' + uuid.uuid4().hex

class BridgeState:
    def __init__(self):
        self.clients = OrderedDict()
        self.lock = threading.RLock()
        self.run_lock = threading.Lock()
        self.used_requests = deque(maxlen=512)
        self.active = None
        self.pending = None

    def client(self, cid):
        if cid not in self.clients:
            if len(self.clients) >= MAX_CLIENTS:
                self.clients.popitem(last=False)
            self.clients[cid] = Client()
        self.clients.move_to_end(cid)
        return self.clients[cid]

    def reserve(self, cid, rid):
        with self.lock:
            if rid in self.used_requests:
                raise BridgeError(409, 'request already used')
            self.used_requests.append(rid)
            c = self.client(cid)
            c.active = rid
            return c.generation

    def history(self, cid):
        with self.lock:
            c = self.clients.get(cid)
            return list(c.history) if c else []

    def append_turn(self, cid, user, reply, generation=None):
        with self.lock:
            c = self.clients.get(cid)
            if c and (generation is None or c.generation == generation):
                c.history.extend(({'role': 'user', 'content': user}, {'role': 'assistant', 'content': reply}))

    def reset(self, cid):
        with self.lock:
            c = self.clients.get(cid)
            if c:
                c.generation += 1
                c.history.clear()
                c.active = None
                c.session_id = 'jarvis-chat-' + uuid.uuid4().hex

class AgentBridge:
    def __init__(self, worker=None):
        self.state = BridgeState()
        self.worker = worker or self._managed_worker
        self.readiness_proof = None
        self.ready = self._readiness()

    def _managed_command(self, code):
        if str(HERMES_SOURCE) not in sys.path:
            sys.path.insert(0, str(HERMES_SOURCE))
        from hermes_cli._launchers import runtime_command
        return runtime_command(HERMES_SOURCE, code=code)

    def _managed_worker(self, payload, on_process=None):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        code = 'import runpy;runpy.run_path(' + repr(str(ROOT / 'agent_worker.py')) + ',run_name="__main__")'
        env = dict(os.environ, TMPDIR=str(SCRATCH))
        # Prevent inherited orchestrator/task transport from binding this pilot to a task.
        for key in list(env):
            if key.startswith(('HERMES_KANBAN_', 'HERMES_DELEGATE_', 'HERMES_PARENT_')):
                env.pop(key)
        proc = subprocess.Popen(self._managed_command(code), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
        try:
            if on_process:
                on_process(proc)
            out, _ = proc.communicate(json.dumps(payload), timeout=RUN_TTL)
        except subprocess.TimeoutExpired:
            self._kill(proc)
            proc.communicate()
            raise BridgeError(504, 'chat timed out') from None
        finally:
            if proc.poll() is None:
                self._kill(proc)
        if proc.returncode:
            raise BridgeError(503, 'chat unavailable or tool call refused')
        try:
            response = json.loads(out)
        except (TypeError, ValueError):
            raise BridgeError(503, 'chat unavailable') from None
        if not isinstance(response, dict):
            raise BridgeError(503, 'chat unavailable')
        status = response.get('error_status')
        if isinstance(status, int) and 400 <= status <= 599:
            raise BridgeError(status, str(response.get('error') or 'chat unavailable')[:600])
        if response.get('error'):
            raise BridgeError(503, 'chat unavailable')
        return response

    @staticmethod
    def _kill(proc):
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=2)

    def _readiness(self):
        try:
            self.readiness_proof = load_proof(self.worker({'op': 'proof'}))
            return proof_is_safe(self.readiness_proof)
        except Exception:
            return False

    def run(self, message, client_id, request_id):
        if not self.ready:
            raise BridgeError(503, 'chat unavailable')
        if not self.state.run_lock.acquire(blocking=False):
            raise BridgeError(409, 'chat busy')
        active = None
        try:
            with self.state.lock:
                generation = self.state.reserve(client_id, request_id)
                active = {'client': client_id, 'request': request_id, 'proc': None,
                          'cancelled': False, 'deadline': time.monotonic() + RUN_TTL}
                self.state.active = active
                session_id = self.state.clients[client_id].session_id
            def started(proc):
                with self.state.lock:
                    active['proc'] = proc
                    cancelled = active['cancelled']
                if cancelled:
                    self._kill(proc)
            response = self.worker({'op': 'run', 'message': message, 'history': self.state.history(client_id)}, on_process=started)
            with self.state.lock:
                c = self.state.clients.get(client_id)
                if active['cancelled'] or time.monotonic() > active['deadline'] or not c or c.generation != generation:
                    raise BridgeError(409, 'run cancelled or expired')
                status = response.get('error_status')
                if isinstance(status, int) and 400 <= status <= 599:
                    raise BridgeError(status, str(response.get('error') or 'chat unavailable')[:600])
                if response.get('needs_approval') is True:
                    approval = response.get('approval')
                    if not isinstance(approval, dict):
                        raise BridgeError(503, 'chat unavailable')
                    # The UI/server bind the approval to the CHAT request id
                    # (strict UUID); the digest still covers the worker's own id.
                    approval = dict(approval)
                    approval['request_id'] = request_id
                    self.state.pending = {
                        'client_id': client_id, 'request_id': request_id,
                        'message': message, 'history': self.state.history(client_id),
                        'generation': generation, 'action': approval.get('action'),
                        'args': approval.get('args'), 'digest': approval.get('digest'),
                        'expires_at': int(approval.get('expires_at') or 0)}
                    return {'needs_approval': True, 'approval': approval, 'request_id': request_id}
                if response.get('tools_before') != 0 or response.get('tools_after') != 0 or not proof_is_safe(response.get('proof')):
                    self.ready = False
                    raise BridgeError(503, 'chat safety proof failed')
                reply = response.get('reply')
                if not isinstance(reply, str) or not reply.strip():
                    raise BridgeError(503, 'chat unavailable')
                self.state.append_turn(client_id, message, reply, generation)
                return {'reply': reply, 'session_id': session_id, 'request_id': request_id}
        finally:
            with self.state.lock:
                if active is not None and self.state.active is active:
                    self.state.active = None
                    c = self.state.clients.get(client_id)
                    if c and c.active == request_id:
                        c.active = None
            self.state.run_lock.release()

    def cancel(self, client_id, request_id):
        with self.state.lock:
            active = self.state.active
            if not active or (active['client'], active['request']) != (client_id, request_id):
                raise BridgeError(404, 'run not found for this client')
            active['cancelled'] = True
            self.state.clients[client_id].generation += 1
            proc = active['proc']
        if proc is not None:
            self._kill(proc)
        return {'stopped': proc is not None and proc.poll() is not None,
                'cancel_requested': True, 'provider_rollback': False}

    def new(self, client_id):
        with self.state.lock:
            active = self.state.active
            own = active and active['client'] == client_id
            if own:
                active['cancelled'] = True
            proc = active['proc'] if own else None
            if self.state.pending and self.state.pending['client_id'] == client_id:
                self.state.pending = None
            self.state.reset(client_id)
        if proc is not None:
            self._kill(proc)
        return {'ok': True, 'stopped': proc is not None and proc.poll() is not None}

    def _store_pending(self, client_id, request_id, message, history, generation, approval):
        self.state.pending = {
            'client_id': client_id, 'request_id': request_id, 'message': message,
            'history': history, 'generation': generation, 'action': approval.get('action'),
            'args': approval.get('args'), 'digest': approval.get('digest'),
            'expires_at': int(approval.get('expires_at') or 0)}

    def _claim_pending(self, client_id, request_id, digest):
        """One-shot, owner-bound, digest-bound and expiring."""
        with self.state.lock:
            pending = self.state.pending
            if not pending or (pending['client_id'], pending['request_id']) != (client_id, request_id):
                raise BridgeError(404, 'approval request not found for this client')
            if time.time() > pending['expires_at']:
                self.state.pending = None
                raise BridgeError(410, 'หมดอายุการยืนยัน — ต้องส่งคำขอใหม่')
            if not isinstance(digest, str) or not hmac.compare_digest(digest, str(pending['digest'])):
                raise BridgeError(403, 'digest ไม่ตรงกับคำขอที่แสดง — ปฏิเสธ')
            self.state.pending = None      # consumed exactly once
            return pending

    def approve(self, client_id, request_id, digest):
        if not self.ready:
            raise BridgeError(503, 'chat unavailable')
        pending = self._claim_pending(client_id, request_id, digest)
        cfg = tools_readonly.load_gate_config()
        try:
            outcome = tools_readonly.execute(pending['action'], pending['args'], cfg)
        except tools_readonly.ToolRefusal as refusal:
            raise BridgeError(refusal.status, str(refusal))
        if not self.state.run_lock.acquire(blocking=False):
            raise BridgeError(409, 'chat busy')
        try:
            response = self.worker({
                'op': 'answer_after_action', 'message': pending['message'],
                'history': pending['history'], 'action': pending['action'],
                'action_args': pending['args'], 'output': outcome.get('output', '')})
            with self.state.lock:
                c = self.state.clients.get(client_id)
                if not c or c.generation != pending['generation']:
                    raise BridgeError(409, 'conversation was reset')
            status = response.get('error_status')
            if isinstance(status, int) and 400 <= status <= 599:
                raise BridgeError(status, str(response.get('error') or 'chat unavailable')[:600])
            if response.get('needs_approval') is True:
                approval = response.get('approval')
                if not isinstance(approval, dict):
                    raise BridgeError(503, 'chat unavailable')
                approval = dict(approval)
                approval['request_id'] = request_id
                self._store_pending(client_id, request_id, pending['message'],
                                    pending['history'], pending['generation'], approval)
                return {'needs_approval': True, 'approval': approval, 'request_id': request_id}
            if response.get('tools_before') != 0 or response.get('tools_after') != 0 or not proof_is_safe(response.get('proof')):
                self.ready = False
                raise BridgeError(503, 'chat safety proof failed')
            reply = response.get('reply')
            if not isinstance(reply, str) or not reply.strip():
                raise BridgeError(503, 'chat unavailable')
            with self.state.lock:
                c = self.state.clients.get(client_id)
                if c and c.generation == pending['generation']:
                    self.state.append_turn(client_id, pending['message'], reply, pending['generation'])
                session_id = c.session_id if c else 'jarvis-chat-unknown'
            return {'reply': reply, 'session_id': session_id, 'request_id': request_id,
                    'action_executed': pending['action'], 'output_bytes': len(outcome.get('output', ''))}
        finally:
            self.state.run_lock.release()

    def deny(self, client_id, request_id):
        with self.state.lock:
            pending = self.state.pending
            if not pending or (pending['client_id'], pending['request_id']) != (client_id, request_id):
                raise BridgeError(404, 'approval request not found for this client')
            self.state.pending = None
        return {'ok': True, 'denied': True, 'executed': False}

CHAT_BRIDGE = AgentBridge()
