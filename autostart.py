#!/usr/bin/env python3
"""Opt-in autostart for the JARVIS pilot on macOS (LaunchAgent).

Nothing installs itself: you run `python3 autostart.py install` deliberately and
`python3 autostart.py uninstall` to remove it again. The generated plist stores
only the port, the interpreter and the script path — the API token is generated
per launch by the server, so no secret is ever written to disk here.

JARVIS_LAUNCH_AGENT_DIR overrides the target directory (tests use it); pass
load=True / --load to also bootstrap the agent with launchctl.
"""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
LABEL = 'com.jarvis.thai-voice'


def launch_agent_dir(env=None):
    env = os.environ if env is None else env
    custom = str(env.get('JARVIS_LAUNCH_AGENT_DIR', '')).strip()
    if custom:
        return Path(custom).expanduser()
    return Path.home() / 'Library' / 'LaunchAgents'


def plist_path(directory):
    return Path(directory) / f'{LABEL}.plist'


def render_plist(port, python_exe=None, script=None, workdir=None):
    """PLIST-safe dict -> bytes. No tokens, no user data, no shell strings."""
    script = Path(script or (ROOT / 'start-thai.py')).resolve()
    workdir = Path(workdir or ROOT).resolve()
    python_exe = python_exe or sys.executable
    payload = {
        'Label': LABEL,
        'ProgramArguments': [str(python_exe), str(script), '--no-browser', '--port', str(int(port))],
        'WorkingDirectory': str(workdir),
        'RunAtLoad': True,
        'KeepAlive': False,
        'ProcessType': 'Background',
        'StandardOutPath': str(workdir / 'autostart.log'),
        'StandardErrorPath': str(workdir / 'autostart.log'),
        'EnvironmentVariables': {'JARVIS_OPEN': '0', 'JARVIS_PORT': str(int(port))},
    }
    return plistlib.dumps(payload)


def install(port, env=None, load=False, python_exe=None, script=None, workdir=None):
    env = os.environ if env is None else env
    directory = launch_agent_dir(env)
    directory.mkdir(parents=True, exist_ok=True)
    target = plist_path(directory)
    data = render_plist(port, python_exe=python_exe, script=script, workdir=workdir)
    # Round-trip through plistlib so we never write malformed XML.
    plistlib.loads(data)
    target.write_bytes(data)
    loaded = None
    if load and sys.platform == 'darwin':
        loaded = _launchctl(['bootstrap', f'gui/{os.getuid()}', str(target)])
    return {'installed': True, 'path': str(target), 'port': int(port), 'loaded': loaded}


def uninstall(env=None, load=False):
    env = os.environ if env is None else env
    directory = launch_agent_dir(env)
    target = plist_path(directory)
    existed = target.is_file()
    unloaded = None
    if load and sys.platform == 'darwin' and existed:
        unloaded = _launchctl(['bootout', f'gui/{os.getuid()}/{LABEL}'])
    if existed:
        target.unlink()
    return {'installed': False, 'path': str(target), 'removed': existed, 'unloaded': unloaded}


def status(env=None):
    env = os.environ if env is None else env
    target = plist_path(launch_agent_dir(env))
    if not target.is_file():
        return {'installed': False, 'path': str(target)}
    try:
        data = plistlib.loads(target.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return {'installed': True, 'path': str(target), 'valid': False}
    args = data.get('ProgramArguments') or []
    port = ''
    if '--port' in args:
        idx = args.index('--port')
        if idx + 1 < len(args):
            port = args[idx + 1]
    return {'installed': True, 'valid': True, 'path': str(target),
            'label': data.get('Label'), 'port': port,
            'run_at_load': bool(data.get('RunAtLoad')),
            'script': args[1] if len(args) > 1 else ''}


def _launchctl(arguments):
    """Best-effort: launchctl errors are reported, never fatal to install."""
    try:
        done = subprocess.run(['launchctl', *arguments], capture_output=True,
                              timeout=20, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='JARVIS pilot autostart (opt-in)')
    sub = parser.add_subparsers(dest='command')
    install_cmd = sub.add_parser('install', help='เขียน LaunchAgent (ต้องสั่งเองเท่านั้น)')
    install_cmd.add_argument('--port', type=int, default=8730)
    install_cmd.add_argument('--load', action='store_true', help='สั่ง launchctl bootstrap ด้วย')
    sub.add_parser('uninstall', help='ลบ LaunchAgent')
    sub.add_parser('status', help='ตรวจสถานะ')
    args = parser.parse_args(argv)
    if args.command == 'install':
        if not 1024 <= args.port <= 65535:
            parser.error('port must be between 1024 and 65535')
        print(json.dumps(install(args.port, load=args.load), ensure_ascii=False, indent=2))
    elif args.command == 'uninstall':
        print(json.dumps(uninstall(load=True), ensure_ascii=False, indent=2))
    elif args.command == 'status':
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    else:
        parser.print_help()
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
