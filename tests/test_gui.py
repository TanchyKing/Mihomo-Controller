import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from minimal_mihomo.app import Window, Job
from minimal_mihomo.models import Settings
from minimal_mihomo.storage import Paths
from dataclasses import asdict


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    window = Window(Paths(tmp_path / 'data', tmp_path / 'config'), auto_refresh=False)
    yield window
    window.close()


def snapshot(**overrides):
    report = {'service': {'ActiveState': 'inactive', 'MainPID': '0'},
              'desired_tun': True, 'runtime_tun': 'unknown', 'desired_mode': 'source',
              'other_processes': [], 'proxy_groups': {}}
    report.update(overrides)
    return {'settings': asdict(Settings()), 'profiles': {'a': {'name': 'Local A', 'kind': 'local', 'path': '/tmp/source.yaml'}},
            'report': report, 'logs': ''}


def test_gui_never_claims_unknown_tun_enabled(window):
    window.render(snapshot())
    assert 'Desired TUN: ON' in window.summary.text()
    assert 'Runtime TUN: UNKNOWN' in window.summary.text()


def test_mismatch_overrides_coexistence_banner(window):
    window.render(snapshot(state_mismatch=True, error='DESIRED / RUNTIME STATE MISMATCH',
                           other_processes=[{'pid': 1, 'name': 'clash-verge'}]))
    assert 'MISMATCH' in window.banner.text()


def test_missing_tun_interface_is_failure(window):
    window.render(snapshot(service={'ActiveState': 'active', 'MainPID': '123'}, applied_tun=True, tun_interface_exists=False))
    assert 'TUN FAILURE' in window.banner.text()


def test_refresh_preserves_user_node_choice(window):
    data = snapshot(proxy_groups={'Group': {'all': ['A', 'B'], 'now': 'A'}})
    window.render(data)
    window.nodes.setCurrentText('B')
    window.render(data)
    assert window.nodes.currentText() == 'B'


def test_editor_preserves_pending_changes(window):
    window.render(snapshot())
    window.inputs['tun'].setChecked(False)
    window.render(snapshot())
    assert not window.desired_values()['tun']


def test_mode_selector_exposes_runtime_modes(window):
    assert [window.inputs['mode'].itemText(i) for i in range(window.inputs['mode'].count())] == [
        'rule', 'global', 'direct'
    ]


def test_worker_errors_do_not_expose_raw_exception(app, tmp_path):
    paths = Paths(tmp_path / 'data', tmp_path / 'config')
    def operation(_):
        raise ValueError('password=NEVER_SHOW')
    results = []
    job = Job(paths, operation)
    job.result.connect(results.append)
    job.start()
    assert job.wait(5000)
    app.processEvents()
    assert results and not results[0][0]
    assert 'NEVER_SHOW' not in results[0][1]
