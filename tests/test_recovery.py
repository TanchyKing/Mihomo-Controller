from unittest.mock import Mock
import pytest
from minimal_mihomo.recovery import return_to_verge
from minimal_mihomo.storage import ControllerError


def test_recovery_stops_owned_core_before_launch(monkeypatch):
    monkeypatch.setattr('minimal_mihomo.recovery.Path.is_file', lambda _: True)
    service = Mock()
    service.status.return_value = {'ActiveState': 'inactive', 'MainPID': '0'}
    def launch(*args, **kwargs):
        service.action.assert_called_once_with('stop')
        service.status.assert_called_once()
        assert args[0] == ['/usr/bin/clash-verge']
    monkeypatch.setattr('minimal_mihomo.recovery.subprocess.Popen', launch)
    assert 'stopped' in return_to_verge(service)


def test_recovery_never_launches_second_core_if_stop_fails(monkeypatch):
    monkeypatch.setattr('minimal_mihomo.recovery.Path.is_file', lambda _: True)
    service = Mock()
    service.status.return_value = {'ActiveState': 'active', 'MainPID': '123'}
    launch = Mock()
    monkeypatch.setattr('minimal_mihomo.recovery.subprocess.Popen', launch)
    with pytest.raises(ControllerError, match='has not stopped'):
        return_to_verge(service)
    launch.assert_not_called()
