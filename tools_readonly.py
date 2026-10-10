"""Read-only tools approved for the JARVIS pilot (layer A of TOOLS_GATE_PLAN).

These are pilot-owned tools, NOT Hermes native tools: the model only ever sees
these two schemas, the native Hermes dispatcher is never reached (the worker's
instance gate aborts before it), and nothing executes until the user approves
the exact action+arguments shown in the UI (digest-bound, one-shot, expiring).
"""
import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GATE_FILE = ROOT / 'tools_gate.json'
MAX_PATH_CHARS = 400
MAX_PATTERN_CHARS = 200

SCHEMAS = [
    {'type': 'function', 'function': {
        'name': 'read_text_file',
        'description': 'อ่านไฟล์ข้อความ (plain text/Markdown/โค้ด) จากโฟลเดอร์ที่อนุญาตเท่านั้น',
        'parameters': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'path เต็มของไฟล์ที่ต้องการอ่าน'}},
            'required': ['path'], 'additionalProperties': False}}},
    {'type': 'function', 'function': {
        'name': 'search_text',
        'description': 'ค้นข้อความด้วย regular expression ในโฟลเดอร์ที่อนุญาตเท่านั้น',
        'parameters': {'type': 'object', 'properties': {
            'pattern': {'type': 'string', 'description': 'regex ที่ต้องการค้น'},
            'root': {'type': 'string', 'description': 'โฟลเดอร์เริ่มต้นของการค้นหา (ต้องอยู่ใน allowlist)'},
            'glob': {'type': 'string', 'description': 'จำกัดชนิดไฟล์ เช่น *.md (ไม่บังคับ)'},
            'limit': {'type': 'integer', 'description': 'จำนวนผลลัพธ์สูงสุด 1-200'}},
            'required': ['pattern', 'root'], 'additionalProperties': False}}},
]
ALLOWED_NAMES = tuple(s['function']['name'] for s in SCHEMAS)


class ToolRefusal(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def load_gate_config():
    """Disabled unless an explicit, checked-in gate file enables it."""
    if not GATE_FILE.is_file():
        return {'enabled': False, 'roots': [], 'max_bytes': 200_000, 'max_results': 100}
    try:
        raw = json.loads(GATE_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'enabled': False, 'roots': [], 'max_bytes': 200_000, 'max_results': 100}
    roots = [str(Path(r).expanduser().resolve()) for r in raw.get('roots', []) if isinstance(r, str)]
    return {'enabled': bool(raw.get('enabled')) and bool(roots), 'roots': roots,
            'max_bytes': int(raw.get('max_bytes', 200_000)),
            'max_results': max(1, min(int(raw.get('max_results', 100)), 200))}


def _resolve_inside(path_value, cfg, must_be_file):
    if not isinstance(path_value, str) or not path_value.strip() or len(path_value) > MAX_PATH_CHARS:
        raise ToolRefusal(400, 'path ต้องเป็นข้อความสั้น ๆ ที่ไม่ว่าง')
    candidate = Path(path_value).expanduser()
    if not candidate.is_absolute():
        raise ToolRefusal(400, 'ต้องใช้ path เต็ม (absolute path) เท่านั้น')
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        raise ToolRefusal(400, 'path ไม่สามารถตรวจสอบได้')
    if not any(resolved.is_relative_to(Path(root)) for root in cfg['roots']):
        raise ToolRefusal(403, 'path อยู่นอกโฟลเดอร์ที่อนุญาต')
    if must_be_file and not resolved.is_file():
        raise ToolRefusal(404, 'ไม่พบไฟล์นี้')
    if not must_be_file and not resolved.is_dir():
        raise ToolRefusal(404, 'ไม่พบโฟลเดอร์นี้')
    return resolved


def validate(action, args, cfg):
    """Pure validation — same code path runs before showing the approval and
    again before executing it, so the approved action cannot drift."""
    if not cfg.get('enabled'):
        raise ToolRefusal(403, 'ชั้นเครื่องมือถูกปิดอยู่')
    if action not in ALLOWED_NAMES:
        raise ToolRefusal(403, 'เครื่องมือนี้ไม่อยู่ใน allowlist')
    if not isinstance(args, dict):
        raise ToolRefusal(400, 'arguments ต้องเป็น object')
    if action == 'read_text_file':
        target = _resolve_inside(args.get('path'), cfg, must_be_file=True)
        size = target.stat().st_size
        if size > cfg['max_bytes']:
            raise ToolRefusal(413, f'ไฟล์ใหญ่เกิน {cfg["max_bytes"]} bytes')
        return {'action': action, 'args': {'path': str(target)}, '_size': size}
    pattern = args.get('pattern')
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_CHARS:
        raise ToolRefusal(400, 'pattern ต้องเป็น regex สั้น ๆ ที่ไม่ว่าง')
    try:
        re.compile(pattern)
    except re.error:
        raise ToolRefusal(400, 'pattern ไม่ใช่ regex ที่ถูกต้อง')
    root = _resolve_inside(args.get('root'), cfg, must_be_file=False)
    glob = args.get('glob') or '**/*'
    if not isinstance(glob, str) or len(glob) > 100 or '..' in glob:
        raise ToolRefusal(400, 'glob ไม่ถูกต้อง')
    limit = args.get('limit', cfg['max_results'])
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= cfg['max_results']:
        raise ToolRefusal(400, f'limit ต้องอยู่ระหว่าง 1-{cfg["max_results"]}')
    return {'action': action, 'args': {'pattern': pattern, 'root': str(root), 'glob': glob, 'limit': limit}}


def digest_of(request_id, action, args):
    """SHA-256 over the exact action+arguments+request id that the user saw."""
    payload = json.dumps({'request_id': request_id, 'action': action, 'args': args},
                         ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _looks_binary(sample):
    return b'\x00' in sample


def execute(action, args, cfg):
    """Execute ONLY after the caller verified digest+owner+expiry+one-shot.
    Re-validates here so a tampered argument can never reach the filesystem."""
    plan = validate(action, args, cfg)
    if action == 'read_text_file':
        target = Path(plan['args']['path'])
        with open(target, 'rb') as handle:
            data = handle.read(cfg['max_bytes'] + 1)
        if _looks_binary(data[:4096]):
            raise ToolRefusal(415, 'ไฟล์นี้เป็นไฟล์ไบนารี อ่านเป็นข้อความไม่ได้')
        text = data.decode('utf-8', errors='replace')
        if len(data) > cfg['max_bytes']:
            raise ToolRefusal(413, 'ไฟล์ใหญ่เกินขีดจำกัด')
        return {'output': text, 'bytes': len(data), 'path': str(target)}
    rx = re.compile(plan['args']['pattern'])
    root = Path(plan['args']['root'])
    hits, scanned = [], 0
    for path in sorted(root.glob(plan['args']['glob'])):
        if not path.is_file() or path.stat().st_size > cfg['max_bytes']:
            continue
        scanned += 1
        if scanned > 5000:
            break
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as handle:
                for number, line in enumerate(handle, 1):
                    if rx.search(line):
                        hits.append(f'{path}:{number}: {line.strip()[:200]}')
                        if len(hits) >= plan['args']['limit']:
                            return {'output': '\n'.join(hits), 'hits': len(hits),
                                    'root': str(root), 'truncated': True}
        except OSError:
            continue
    return {'output': '\n'.join(hits) if hits else '(ไม่พบผลลัพธ์)', 'hits': len(hits),
            'root': str(root), 'truncated': False}
