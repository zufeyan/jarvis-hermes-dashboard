#!/usr/bin/env python3
"""Start isolated voice + chat-only pilot; providers own their managed subprocess ABI."""
import argparse
import json
import os
from pathlib import Path
import runpy

parser = argparse.ArgumentParser(description='JARVIS Thai voice + chat-only pilot')
parser.add_argument('--no-browser', action='store_true')
parser.add_argument('--port', type=int, default=8730)
parser.add_argument('--stt-model', choices=['tiny', 'base', 'small', 'medium', 'large-v3'],
                    help='เลือกโมเดลถอดเสียงเฉพาะรอบนี้ (default: ใช้ค่าใน ~/.hermes/config.yaml)')
args = parser.parse_args()
if not 1024 <= args.port <= 65535:
    parser.error('port must be between 1024 and 65535')
os.environ['JARVIS_PORT'] = str(args.port)
os.environ['JARVIS_OPEN'] = '0' if args.no_browser else '1'
if args.stt_model:
    os.environ['JARVIS_STT_MODEL'] = args.stt_model
# Pilot-scoped chat route only (no keys, no ~/.hermes/config.yaml change).
# The configured Hermes default stays authoritative when this file is absent.
route_file = Path(__file__).resolve().with_name('chat_route.json')
if route_file.is_file() and 'JARVIS_CHAT_PROVIDER' not in os.environ:
    route = json.loads(route_file.read_text(encoding='utf-8'))
    provider, model = str(route.get('provider', '')), str(route.get('model', ''))
    if not provider or not model or len(provider + model) > 200:
        raise SystemExit('chat_route.json must contain non-empty provider and model')
    os.environ['JARVIS_CHAT_PROVIDER'] = provider
    os.environ['JARVIS_CHAT_MODEL'] = model
runpy.run_path(str(Path(__file__).resolve().with_name('server.py')), run_name='__main__')
