"""LAN exposure scope for the JARVIS pilot — opt-in, loopback-only by default.

Nothing here runs unless JARVIS_LAN=1 is set explicitly by the launcher
(`start-thai.py --lan`). The default keeps every check exactly as strict as the
loopback-only pilot: a LAN client is rejected before the token check.
"""
import ipaddress
import os
import socket


def lan_enabled(env=None):
    env = os.environ if env is None else env
    return str(env.get('JARVIS_LAN', '')).strip() == '1'


def parse_allowlist(value):
    """Strict IP literals only. Anything that is not an IP address is dropped,
    never silently widened."""
    allowed = set()
    for item in str(value or '').split(','):
        item = item.strip()
        if not item:
            continue
        try:
            allowed.add(str(ipaddress.ip_address(item)))
        except ValueError:
            continue
    return frozenset(allowed)


def machine_ips():
    """Addresses this host answers on, injected-in tests via allowed_hosts()."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            try:
                ips.add(str(ipaddress.ip_address(ip)))
            except ValueError:
                continue
    except OSError:
        pass
    ips.discard('0.0.0.0')
    return frozenset(ips)


def allowed_hosts(port, lan, local_ips=()):
    """Host header values accepted. Without LAN only loopback names qualify."""
    hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    if lan:
        hosts |= {f'{ip}:{port}' for ip in local_ips}
    return hosts


def allowed_origins(port, lan, local_ips=()):
    return {f'http://{host}' for host in allowed_hosts(port, lan, local_ips)}


def client_allowed(ip_value, allowlist):
    """Empty allowlist inside LAN mode = whole LAN (explicitly opted in)."""
    if str(ip_value) in ('127.0.0.1', '::1', '::ffff:127.0.0.1'):
        return True  # the operator on this machine is never locked out
    if not allowlist:
        return True
    try:
        return str(ipaddress.ip_address(str(ip_value))) in allowlist
    except ValueError:
        return False
