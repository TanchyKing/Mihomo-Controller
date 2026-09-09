"""Real installed core, isolated directory, loopback ports and TUN disabled."""
import shutil
import socket
import subprocess
import time
import pytest
from minimal_mihomo.mihomo_api import API
from minimal_mihomo.models import Settings
from minimal_mihomo.runtime_builder import build, encode
from minimal_mihomo.storage import atomic_write, ControllerError


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_real_core_api_and_selection(tmp_path):
    binary = shutil.which('mihomo') or shutil.which('verge-mihomo')
    if not binary:
        pytest.skip('No installed Mihomo core')
    mixed, api_port = free_port(), free_port()
    while api_port == mixed:
        api_port = free_port()
    config = build(b'''mode: rule
proxies: []
proxy-groups:
  - name: Test / Group
    type: select
    proxies: [DIRECT, REJECT]
rules: ['MATCH,DIRECT']
''', Settings(tun=False, mixed_port=mixed, api_port=api_port))
    path = tmp_path / 'runtime.yaml'
    atomic_write(path, encode(config))
    process = subprocess.Popen([binary, '-d', str(tmp_path), '-f', str(path)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        api = API(config)
        api.wait(config, timeout=10)
        assert 'Test / Group' in api.groups()
        api.select('Test / Group', 'REJECT')
        assert api.groups()['Test / Group']['now'] == 'REJECT'
        wrong = dict(config, secret='incorrect-token')
        with pytest.raises(ControllerError):
            API(wrong).request('/configs')
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
