from dataclasses import asdict
from pathlib import Path
import socket
from unittest.mock import Mock
import pytest
from minimal_mihomo.controller import Controller
from minimal_mihomo.mihomo_api import API
from minimal_mihomo.models import Settings
from minimal_mihomo.network import preflight
from minimal_mihomo.profile_store import ProfileStore
from minimal_mihomo.redaction import redact, collect_secrets
from minimal_mihomo.runtime_builder import build, encode, parse
from minimal_mihomo.storage import ControllerError, Paths, read_json, write_json, atomic_write

SOURCE = b'''# preserve this comment and whitespace\nmode: rule\nproxies: []\nproxy-groups:\n  - name: Any group\n    type: select\n    proxies: [DIRECT]\nrules: [MATCH,DIRECT]\ntun: {enable: false}\ndns: {enable: true, ipv6: false}\n'''


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path / 'data', tmp_path / 'config')


def test_source_immutability_and_tun_override(tmp_path):
    source = tmp_path / 'source.yaml'
    source.write_bytes(SOURCE)
    result = build(source.read_bytes(), Settings())
    assert result['tun']['enable'] is True
    assert source.read_bytes() == SOURCE
    assert result['dns']['enable'] == parse(SOURCE)['dns']['enable']
    assert result['dns']['ipv6'] == parse(SOURCE)['dns']['ipv6']
    assert result['rules'] == parse(SOURCE)['rules']
    assert result['mixed-port'] == 17890
    assert result['external-controller'] == '127.0.0.1:19099'
    assert result['dns']['respect-rules'] is True
    assert all(server.startswith(('https://', 'tls://'))
               for key in ('nameserver', 'default-nameserver', 'proxy-server-nameserver', 'direct-nameserver')
               for server in result['dns'][key])
    assert '223.5.5.5' not in str(result['dns'])
    assert all(server.endswith('#GLOBAL') for server in result['dns']['nameserver'])


def test_secure_dns_can_preserve_source_when_disabled():
    result = build(SOURCE, Settings(secure_dns=False))
    assert result['dns'] == parse(SOURCE)['dns']


def test_listeners_and_source_port_are_isolated():
    result = build(SOURCE + b'port: 7890\nexternal-controller-unix: /tmp/core.sock\nlisteners: [{name: other}]\n', Settings())
    assert result['port'] == 0
    assert 'listeners' not in result and 'external-controller-unix' not in result


def test_provider_relative_path(tmp_path):
    result = build(SOURCE + b'proxy-providers: {local: {type: file, path: nodes.yaml}}\n', Settings(), tmp_path)
    assert result['proxy-providers']['local']['path'] == str(tmp_path / 'nodes.yaml')


def test_invalid_subscription_keeps_snapshot(paths, monkeypatch):
    store = ProfileStore(paths)
    monkeypatch.setattr('minimal_mihomo.profile_store.download', lambda _: SOURCE)
    profile_id = store.add_subscription('https://example.com/secret?token=hidden', lambda *_: None)
    original = store.get(profile_id)
    def invalid(_):
        return parse(b'<html>404 Not Found</html>')
    monkeypatch.setattr('minimal_mihomo.profile_store.download', invalid)
    with pytest.raises(ControllerError):
        store.refresh(profile_id, lambda *_: None)
    assert store.get(profile_id) == original
    assert Path(original['path']).read_bytes() == SOURCE


def test_config_rejected_subscription_keeps_snapshot(paths, monkeypatch):
    store = ProfileStore(paths)
    monkeypatch.setattr('minimal_mihomo.profile_store.download', lambda _: SOURCE)
    profile_id = store.add_subscription('https://example.com/sub', lambda *_: None)
    original = store.get(profile_id)
    def reject(*_):
        raise ControllerError('bad config')
    with pytest.raises(ControllerError):
        store.refresh(profile_id, reject)
    assert store.get(profile_id) == original


class FakeService:
    def __init__(self):
        self.active = False
        self.calls = []
        self.reject = False

    def status(self):
        return {'LoadState': 'loaded', 'ActiveState': 'active' if self.active else 'inactive',
                'MainPID': '123' if self.active else '0'}

    def action(self, action):
        self.calls.append(action)
        self.active = action != 'stop'

    def validate(self, *_):
        if self.reject:
            raise ControllerError('Invalid config')


@pytest.fixture
def controller(paths, monkeypatch):
    monkeypatch.setattr('minimal_mihomo.controller.processes', lambda *_: [])
    monkeypatch.setattr('minimal_mihomo.controller.preflight', lambda *_: None)
    monkeypatch.setattr('minimal_mihomo.controller.network_status', lambda: {'tun_interface_exists': True})
    api = Mock()
    return Controller(paths, FakeService(), lambda _: api)


def add(controller, tmp_path, name, data=SOURCE):
    source = tmp_path / (name + '.yaml')
    source.write_bytes(data)
    return controller.profiles.add_yaml(source), source


def test_switch_A_B_A(controller, tmp_path):
    a, apath = add(controller, tmp_path, 'a')
    b, bpath = add(controller, tmp_path, 'b', SOURCE.replace(b'mode: rule', b'mode: direct'))
    for profile_id in (a, b, a):
        controller.apply(profile_id)
    assert apath.read_bytes() == SOURCE
    assert bpath.read_bytes() == SOURCE.replace(b'mode: rule', b'mode: direct')
    assert read_json(controller.paths.state, {})['profile_id'] == a


def test_bad_runtime_rolls_back(controller, tmp_path):
    a, _ = add(controller, tmp_path, 'a')
    b, _ = add(controller, tmp_path, 'b', SOURCE.replace(b'mode: rule', b'mode: direct'))
    controller.apply(a)
    good = controller.paths.good.read_bytes()
    def factory(config):
        api = Mock()
        if config['mode'] == 'direct':
            api.wait.side_effect = ControllerError('RUNTIME MISMATCH')
        return api
    controller.api_factory = factory
    with pytest.raises(ControllerError, match='Previous configuration restored'):
        controller.apply(b)
    assert controller.paths.runtime.read_bytes() == good
    assert controller.paths.good.read_bytes() == good
    assert read_json(controller.paths.state, {})['profile_id'] == a
    assert controller.service.active
    assert not controller.paths.transaction.exists()


def test_validation_failure_does_not_stop_service(controller, tmp_path):
    a, _ = add(controller, tmp_path, 'a')
    controller.apply(a)
    before = controller.paths.runtime.read_bytes()
    controller.service.calls.clear()
    controller.service.reject = True
    with pytest.raises(ControllerError):
        controller.apply(a)
    assert controller.paths.runtime.read_bytes() == before
    assert controller.service.calls == []


def test_first_launch_failure_leaves_stopped(controller, tmp_path):
    a, _ = add(controller, tmp_path, 'a')
    controller.api_factory = lambda _: Mock(wait=Mock(side_effect=ControllerError('fail')))
    with pytest.raises(ControllerError):
        controller.apply(a)
    assert not controller.service.active
    assert not controller.paths.good.exists()
    assert not controller.paths.runtime.exists()


def test_recovery_failure_retains_journal(controller, tmp_path):
    a, _ = add(controller, tmp_path, 'a')
    controller.apply(a)
    controller.api_factory = lambda _: Mock(wait=Mock(side_effect=ControllerError('fail')))
    with pytest.raises(ControllerError, match='AND rollback failed'):
        controller.apply(a)
    assert controller.paths.transaction.exists()
    with pytest.raises(ControllerError, match='interrupted'):
        controller.start()
    controller.api_factory = lambda _: Mock()
    controller.recover()
    assert not controller.paths.transaction.exists()


def test_port_conflict(monkeypatch):
    monkeypatch.setattr('minimal_mihomo.network.processes', lambda *_: [])
    monkeypatch.setattr('minimal_mihomo.network.port_owners', lambda ports: [{'port': ports[0], 'pid': 456, 'name': 'test'}])
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        settings = Settings(mixed_port=listener.getsockname()[1])
        with pytest.raises(ControllerError, match='PID 456 / test'):
            preflight(build(SOURCE, settings))


def test_hidden_owner_is_accepted_for_running_owned_core(monkeypatch):
    monkeypatch.setattr('minimal_mihomo.network.processes', lambda *_: [])
    monkeypatch.setattr('minimal_mihomo.network.port_owners', lambda ports: [
        {'port': port, 'pid': 0, 'name': 'unavailable (permissions)', 'protocol': protocol}
        for port in ports for protocol in (('tcp', 'udp') if port == ports[0] else ('tcp',))
    ])
    preflight(build(SOURCE, Settings()), owned_pid=123)


def test_duplicate_blocks_tun(monkeypatch):
    monkeypatch.setattr('minimal_mihomo.network.processes', lambda *_: [{'pid': 567, 'name': 'verge-mihomo'}])
    with pytest.raises(ControllerError, match='PID 567'):
        preflight(build(SOURCE, Settings()))


@pytest.mark.parametrize('tun', [False, None, {}, {'enable': False}])
def test_runtime_mismatch(tun):
    api = API(build(SOURCE, Settings()))
    api.request = lambda *_: {'tun': tun, 'mode': 'rule'}
    with pytest.raises(ControllerError, match='RUNTIME MISMATCH'):
        api.verify(build(SOURCE, Settings()))


def test_missing_group_is_failure():
    api = API(build(SOURCE, Settings()))
    api.request = lambda path: {'tun': {'enable': True}, 'mode': 'rule'} if path == '/configs' else {'proxies': {}}
    with pytest.raises(ControllerError, match='missing expected proxy groups'):
        api.verify(build(SOURCE, Settings()))


def test_redaction():
    config = {'proxies': [{'password': 'secret with spaces'}], 'secret': 'bearer-token'}
    text = 'proxy failed: secret with spaces\nAuthorization: Bearer bearer-token\nhttps://u:pass@example.com/pathsecret?token=querysecret\npassword: otherpassword'
    result = redact(text, collect_secrets(config))
    for secret in ('secret with spaces', 'bearer-token', 'pathsecret', 'querysecret', 'otherpassword', 'u:pass'):
        assert secret not in result


def test_atomic_permissions(paths):
    atomic_write(paths.runtime, SOURCE)
    assert paths.runtime.stat().st_mode & 0o777 == 0o600


def test_lock_excludes_second_controller(paths):
    with paths.lock():
        with pytest.raises(ControllerError, match='in progress'):
            with paths.lock():
                pass


@pytest.mark.parametrize('data', [b'hello', b'<html>oops</html>', b'password: [oops', b'- x'])
def test_invalid_profile(data):
    with pytest.raises(ControllerError):
        parse(data)


def test_crash_during_commit_restores_prior_metadata(controller, tmp_path):
    a, _ = add(controller, tmp_path, 'a')
    controller.apply(a)
    paths = controller.paths
    previous = paths.good.read_bytes()
    previous_state = read_json(paths.state, {})
    atomic_write(paths.runtime.parent / 'transaction.previous.yaml', previous)
    write_json(paths.transaction, {'had_previous': True, 'was_active': True, 'previous_state': previous_state})
    replacement = encode(build(SOURCE, Settings(mode='direct')))
    atomic_write(paths.runtime, replacement)
    atomic_write(paths.good, replacement)
    write_json(paths.state, {'profile_id': 'incomplete-commit'})
    controller.recover()
    assert paths.runtime.read_bytes() == previous
    assert paths.good.read_bytes() == previous
    assert read_json(paths.state, {}) == previous_state


def test_subscription_https_downgrade_rejected_before_request():
    from minimal_mihomo.profile_store import SecureRedirect
    from urllib.request import Request
    with pytest.raises(ControllerError, match='insecure HTTP'):
        SecureRedirect().redirect_request(Request('https://example.com'), None, 302, '', {}, 'http://example.com/secret')


def test_recursive_yaml_rejected():
    with pytest.raises(ControllerError, match='Recursive'):
        parse(b'proxies: &a [*a]')


def test_copy_profile_metadata_does_not_write_source(paths, tmp_path):
    store = ProfileStore(paths)
    source = tmp_path / 'source.yaml'
    source.write_bytes(SOURCE)
    original = store.add_yaml(source)
    duplicate = store.duplicate(original, 'Copy')
    store.remove(duplicate)
    assert source.read_bytes() == SOURCE
    assert store.get(original)['path'] == str(source)


def test_rename_profile_only_changes_metadata(paths, tmp_path):
    source = tmp_path / 'profile.yaml'
    source.write_bytes(SOURCE)
    store = ProfileStore(paths)
    profile_id = store.add_yaml(source)
    store.rename(profile_id, 'Oregon')
    assert store.get(profile_id)['name'] == 'Oregon'
    assert source.read_bytes() == SOURCE


def test_latency_all_keeps_failures():
    api = API(build(SOURCE, Settings()))
    api.latency = lambda node: {'delay': 42} if node == 'good' else (_ for _ in ()).throw(ControllerError('fail'))
    assert api.latencies(['good', 'bad', 'good']) == {'good': 42, 'bad': None}
