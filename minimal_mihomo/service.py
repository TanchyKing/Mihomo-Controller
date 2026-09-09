import subprocess
from pathlib import Path
from .storage import ControllerError

UNIT = 'minimal-mihomo.service'


def detect_binary():
    for path in ('/usr/bin/mihomo', '/usr/bin/verge-mihomo'):
        if Path(path).is_file():
            return path
    raise ControllerError('Install /usr/bin/mihomo or /usr/bin/verge-mihomo first.')


class Service:
    def status(self):
        result = subprocess.run(['systemctl', 'show', UNIT, '--property=ActiveState,SubState,MainPID,LoadState'],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise ControllerError('Cannot query systemd service status.')
        return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)

    def action(self, action):
        if action not in ('start', 'stop', 'restart'):
            raise ValueError(action)
        result = subprocess.run(['systemctl', action, UNIT], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ControllerError(f'systemctl {action} {UNIT} failed. Install the service and authenticate through polkit; inspect journalctl for details.')

    def enabled(self):
        result = subprocess.run(['systemctl', 'is-enabled', UNIT], capture_output=True, text=True, timeout=10)
        return result.stdout.strip() == 'enabled'

    def validate(self, candidate, core_dir):
        result = subprocess.run([detect_binary(), '-t', '-d', str(core_dir), '-f', str(candidate)],
                                capture_output=True, timeout=60)
        if result.returncode:
            raise ControllerError('Mihomo rejected the candidate; current runtime is unchanged. Raw validator output is withheld because it may contain credentials.')

    def logs(self, lines=100):
        result = subprocess.run(['journalctl', '-u', UNIT, '-n', str(lines), '--no-pager', '-o', 'cat'],
                                capture_output=True, text=True, timeout=10)
        return result.stdout
