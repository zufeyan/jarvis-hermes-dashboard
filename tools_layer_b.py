"""Layer B (write file / run command) — opt-in, sandboxed, audited.

Disabled unless tools_layer_b.json sets enabled=true with a sandbox_root
(see docs/TOOLS_GATE_PLAN.md §4b). Even when enabled:

  * nothing executes in the worker process — a tool call still stops the turn
    and becomes a digest-bound approval request handled by the same two-phase
    gate as the read-only layer;
  * writes must be RELATIVE paths that resolve inside the sandbox after
    symlink resolution (absolute paths, `..`, and escaping symlinks refuse);
  * commands run argv-style with shell=False against an exact allowlist,
    cwd pinned to the sandbox, a timeout and an output cap;
  * every execution appends one JSON line (action, argv/path, bytes, sha256,
    exit code) to an audit log that must live inside the sandbox. File
    CONTENT is never written to the audit log, and an unwritable audit log
    refuses the action (fail-closed).
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time

from tools_readonly import ToolRefusal, digest_of

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / 'tools_layer_b.json'
MAX_PATH_CHARS = 400
MAX_ARG_CHARS = 400
MAX_ARGV_CHARS = 2000

SCHEMAS = [
    {'type': 'function', 'function': {
        'name': 'write_text_file',
        'description': 'เขียนไฟล์ข้อความลงใน sandbox ที่อนุญาตเท่านั้น (ต้องได้รับอนุมัติทุกครั้ง)',
        'parameters': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'path สัมพัทธ์ภายใน sandbox เช่น notes/idea.md'},
            'content': {'type': 'string', 'description': 'เนื้อหาข้อความที่จะเขียน'}},
            'required': ['path', 'content'], 'additionalProperties': False}}},
    {'type': 'function', 'function': {
        'name': 'run_command',
        'description': 'รันคำสั่งที่อยู่ใน allowlist เท่านั้น แบบ argv (ไม่ผ่าน shell) ใน sandbox (ต้องได้รับอนุมัติทุกครั้ง)',
        'parameters': {'type': 'object', 'properties': {
            'name': {'type': 'string', 'description': 'ชื่อคำสั่งที่อยู่ใน allow_commands'},
            'args': {'type': 'array', 'items': {'type': 'string'},
                     'description': 'อาร์กิวเมนต์แยกเป็นรายการ (ไม่ใช่ string เดียวผ่าน shell)'}},
            'required': ['name'], 'additionalProperties': False}}},
]
NAMES = tuple(s['function']['name'] for s in SCHEMAS)


def _defaults():
    return {'enabled': False, 'sandbox_root': '', 'allow_commands': [],
            'max_write_bytes': 100_000, 'max_output_bytes': 20_000,
            'timeout_seconds': 20, 'audit_log': ''}


def load_config():
    """Disabled unless the checked-in file enables it AND a sandbox exists."""
    cfg = _defaults()
    if not CONFIG_FILE.is_file():
        return cfg
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return cfg
    if not isinstance(raw, dict) or not raw.get('enabled'):
        return cfg
    sandbox = str(raw.get('sandbox_root') or '').strip()
    if not sandbox:
        return cfg
    try:
        sandbox_path = Path(sandbox).expanduser().resolve()
    except (OSError, RuntimeError):
        return cfg
    if not sandbox_path.is_dir():
        return cfg
    audit = str(raw.get('audit_log') or '').strip() or str(sandbox_path / '.jarvis_layer_b_audit.jsonl')
    try:
        audit_path = Path(audit).expanduser().resolve()
    except (OSError, RuntimeError):
        return cfg
    if not audit_path.is_relative_to(sandbox_path):
        return cfg  # the audit trail must not live outside the sandbox
    commands = []
    for entry in raw.get('allow_commands', []) if isinstance(raw.get('allow_commands'), list) else []:
        if isinstance(entry, str) and entry.strip() and len(entry) <= 300:
            commands.append(entry.strip())
    cfg.update({
        'enabled': True,
        'sandbox_root': str(sandbox_path),
        'allow_commands': commands,
        'max_write_bytes': max(1, min(int(raw.get('max_write_bytes', 100_000)), 1_000_000)),
        'max_output_bytes': max(1, min(int(raw.get('max_output_bytes', 20_000)), 200_000)),
        'timeout_seconds': max(1, min(int(raw.get('timeout_seconds', 20)), 60)),
        'audit_log': str(audit_path),
    })
    return cfg


def _sandbox(cfg):
    return Path(cfg['sandbox_root'])


def _resolve_inside(cfg, relative):
    if not isinstance(relative, str) or not relative.strip() or len(relative) > MAX_PATH_CHARS:
        raise ToolRefusal(400, 'path ต้องเป็นข้อความสั้น ๆ ที่ไม่ว่าง')
    rel = relative.strip()
    if rel.startswith('/') or rel.startswith('~'):
        raise ToolRefusal(400, 'ชั้น B ใช้ path สัมพัทธ์ภายใน sandbox เท่านั้น')
    parts = Path(rel).parts
    if not parts or '..' in parts or any(p.startswith('/') for p in parts):
        raise ToolRefusal(400, 'path ต้องไม่มี .. และอยู่ภายใน sandbox')
    base = _sandbox(cfg).resolve()
    target = (base / rel)
    # resolve(strict=False) follows symlinks already on disk, so an escaping
    # symlink cannot smuggle the write outside the sandbox.
    resolved = target.resolve()
    if not resolved.is_relative_to(base):
        raise ToolRefusal(403, 'path ชนขอบ sandbox (symlink หรือ ..) — ปฏิเสธ')
    return resolved


def _audit_must_be_writable(cfg):
    audit = Path(cfg['audit_log'])
    try:
        audit.parent.mkdir(parents=True, exist_ok=True)
        with open(audit, 'a', encoding='utf-8'):
            pass
    except OSError:
        raise ToolRefusal(500, 'audit log เขียนไม่ได้ — ยังไม่ดำเนินการใด ๆ')


def _audit(cfg, action, detail):
    record = {'ts': int(time.time()), 'iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'action': action, 'detail': detail, 'audit_id': secrets.token_hex(8)}
    try:
        with open(cfg['audit_log'], 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError as error:
        raise ToolRefusal(500, f'บันทึก audit ไม่ได้: {error.__class__.__name__}')


def validate(action, args, cfg):
    """Pure validation — the same path runs before showing the approval and
    again right before executing, so the approved action cannot drift."""
    if not cfg.get('enabled'):
        raise ToolRefusal(403, 'ชั้น B ถูกปิดอยู่ (tools_layer_b.json)')
    if action not in NAMES:
        raise ToolRefusal(403, 'เครื่องมือนี้ไม่อยู่ใน allowlist ของชั้น B')
    if not isinstance(args, dict):
        raise ToolRefusal(400, 'arguments ต้องเป็น object')
    if action == 'write_text_file':
        target = _resolve_inside(cfg, args.get('path'))
        content = args.get('content')
        if not isinstance(content, str):
            raise ToolRefusal(400, 'content ต้องเป็นข้อความ')
        size = len(content.encode('utf-8'))
        if size > cfg['max_write_bytes']:
            raise ToolRefusal(413, f'เนื้อหาใหญ่เกิน {cfg["max_write_bytes"]} bytes')
        return {'action': action, 'args': {'path': str(target), 'content': content}, '_size': size}
    name = args.get('name')
    if not isinstance(name, str) or not name or len(name) > 300:
        raise ToolRefusal(400, 'name ต้องเป็นชื่อคำสั่งสั้น ๆ')
    if name not in cfg['allow_commands']:
        raise ToolRefusal(403, 'คำสั่งนี้ไม่อยู่ใน allow_commands')
    argv_args = args.get('args', [])
    if argv_args is None:
        argv_args = []
    if not isinstance(argv_args, list) or not all(isinstance(a, str) for a in argv_args):
        raise ToolRefusal(400, 'args ต้องเป็นรายการข้อความ')
    if len(argv_args) > 32 or sum(len(a) for a in argv_args) > MAX_ARGV_CHARS:
        raise ToolRefusal(400, 'อาร์กิวเมนต์ยาวเกินไป')
    if any(len(a) > MAX_ARG_CHARS for a in argv_args):
        raise ToolRefusal(400, 'อาร์กิวเมนต์ชิ้นเดียวยาวเกินไป')
    return {'action': action, 'args': {'name': name, 'args': list(argv_args)}}


def _clean_env():
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if any(marker in upper for marker in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'AUTH')):
            env.pop(key, None)
    return env


def execute(action, args, cfg):
    """Execute ONLY after the caller verified digest+owner+expiry+one-shot."""
    plan = validate(action, args, cfg)
    _audit_must_be_writable(cfg)
    if plan['action'] == 'write_text_file':
        target = Path(plan['args']['path'])
        data = plan['args']['content'].encode('utf-8')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        _audit(cfg, 'write_text_file', {'path': str(target), 'bytes': len(data), 'sha256': sha})
        return {'output': f'เขียนไฟล์แล้ว: {target.name} ({len(data)} bytes, sha256 {sha[:16]}…)',
                'path': str(target), 'bytes': len(data), 'sha256': sha}
    name, cmd_args = plan['args']['name'], plan['args']['args']
    executable = name if os.path.sep in name else shutil.which(name)
    if not executable or not os.path.isfile(executable):
        raise ToolRefusal(404, 'ไม่พบ executable ของคำสั่งนี้')
    resolved = str(Path(executable).resolve())
    if name in cfg['allow_commands'] and os.path.sep in name and resolved != str(Path(name).resolve()):
        raise ToolRefusal(403, 'path ของคำสั่งไม่ตรงกับ allowlist')
    argv = [resolved, *cmd_args]
    try:
        run = subprocess.run(argv, cwd=cfg['sandbox_root'], env=_clean_env(), shell=False,
                             capture_output=True, timeout=cfg['timeout_seconds'])
    except subprocess.TimeoutExpired:
        _audit(cfg, 'run_command', {'argv': argv, 'result': 'timeout'})
        raise ToolRefusal(504, f'คำสั่งทำงานเกิน {cfg["timeout_seconds"]} วินาที')
    except OSError:
        raise ToolRefusal(500, 'รันคำสั่งไม่ได้')
    cap = cfg['max_output_bytes']
    stdout = run.stdout[:cap].decode('utf-8', errors='replace')
    stderr = run.stderr[:cap].decode('utf-8', errors='replace')
    combined = (stdout + ('\n' if stdout and stderr else '') + stderr).strip() or '(ไม่มีผลลัพธ์)'
    sha = hashlib.sha256(combined.encode('utf-8')).hexdigest()
    _audit(cfg, 'run_command', {'argv': argv, 'exit_code': run.returncode,
                                'output_bytes': len(combined), 'sha256': sha})
    return {'output': combined, 'exit_code': run.returncode, 'argv': argv, 'sha256': sha,
            'truncated': bool(len(run.stdout) + len(run.stderr) > cap)}


__all__ = ['SCHEMAS', 'NAMES', 'load_config', 'validate', 'execute',
           'ToolRefusal', 'digest_of']
