"""Post-v1 opt-in switches (LAN exposure + autostart). Default is always OFF.

Deterministic: pure scope helpers, a throwaway LaunchAgent directory and the
real plist renderer. No network sockets, no launchctl, no writes outside
pytest's tmp_path.
"""
import plistlib

import autostart
import net_scope
import server


# --------------------------------------------------------------- LAN scope ----
def test_lan_is_off_unless_explicitly_enabled():
    assert net_scope.lan_enabled({}) is False
    assert net_scope.lan_enabled({'JARVIS_LAN': ''}) is False
    assert net_scope.lan_enabled({'JARVIS_LAN': '0'}) is False
    assert net_scope.lan_enabled({'JARVIS_LAN': 'no'}) is False
    assert net_scope.lan_enabled({'JARVIS_LAN': '1'}) is True


def test_loopback_hosts_only_without_lan():
    hosts = net_scope.allowed_hosts(8730, False, ('192.168.1.5',))
    assert hosts == {'127.0.0.1:8730', 'localhost:8730'}
    assert net_scope.allowed_origins(8730, False, ('192.168.1.5',)) == {
        'http://127.0.0.1:8730', 'http://localhost:8730'}


def test_lan_hosts_add_machine_addresses():
    hosts = net_scope.allowed_hosts(8730, True, ('192.168.1.5', 'fe80::1'))
    assert '192.168.1.5:8730' in hosts
    assert 'fe80::1:8730' in hosts
    assert '127.0.0.1:8730' in hosts
    origins = net_scope.allowed_origins(8730, True, ('192.168.1.5',))
    assert 'http://192.168.1.5:8730' in origins


def test_allowlist_accepts_ip_literals_and_drops_everything_else():
    parsed = net_scope.parse_allowlist('192.168.1.10, 192.168.1.10 ,nope,999.1.1.1, ,10.0.0.7')
    assert parsed == frozenset({'192.168.1.10', '10.0.0.7'})


def test_client_allowlist_is_exact():
    allow = net_scope.parse_allowlist('192.168.1.10')
    assert net_scope.client_allowed('192.168.1.10', allow) is True
    assert net_scope.client_allowed('192.168.1.11', allow) is False
    assert net_scope.client_allowed('192.168.1.100', allow) is False  # no prefix matching
    assert net_scope.client_allowed('not-an-ip', allow) is False
    assert net_scope.client_allowed('192.168.1.11', frozenset()) is True  # whole-LAN opt-in
    # The operator on the machine itself is never locked out of their own pilot.
    assert net_scope.client_allowed('127.0.0.1', allow) is True
    assert net_scope.client_allowed('::1', allow) is True


def test_server_module_defaults_stay_loopback():
    assert server.LAN_MODE == net_scope.lan_enabled()
    if not server.LAN_MODE:
        hosts = net_scope.allowed_hosts(8730, server.LAN_MODE, server.LOCAL_IPS)
        assert hosts == {'127.0.0.1:8730', 'localhost:8730'}
        assert server.LAN_ALLOW == frozenset()


# -------------------------------------------------------------- autostart ----
def test_autostart_is_not_installed_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv('JARVIS_LAUNCH_AGENT_DIR', str(tmp_path / 'agents'))
    status = autostart.status()
    assert status['installed'] is False


def test_install_status_uninstall_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv('JARVIS_LAUNCH_AGENT_DIR', str(tmp_path / 'agents'))
    installed = autostart.install(8741, load=False)
    assert installed['installed'] is True
    status = autostart.status()
    assert status['installed'] is True and status['valid'] is True
    assert status['port'] == '8741' and status['run_at_load'] is True
    removed = autostart.uninstall(load=False)
    assert removed['removed'] is True
    assert autostart.status()['installed'] is False


def test_plist_is_wellformed_and_carries_no_secret(tmp_path):
    data = autostart.render_plist(8730)
    payload = plistlib.loads(data)
    assert payload['Label'] == autostart.LABEL
    assert payload['ProgramArguments'][-2:] == ['--port', '8730']
    text = data.decode('utf-8', errors='replace')
    assert 'jarvis-token' not in text
    assert 'sk-' not in text
    assert 'X-Jarvis-Token' not in text
    # Arguments are a list — never a shell string.
    assert all(isinstance(arg, str) for arg in payload['ProgramArguments'])


def test_autostart_needs_an_explicit_command():
    assert autostart.main([]) == 2  # prints help, changes nothing
