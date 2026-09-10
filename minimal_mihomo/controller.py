from pathlib import Path
import hashlib
import time
from .mihomo_api import API
from .models import Settings
from .network import preflight, processes, network_status
from .profile_store import ProfileStore, now
from .runtime_builder import build, encode, parse
from .service import Service
from .storage import ControllerError, atomic_write, read_json, write_json


class Controller:
    def __init__(self, paths, service=None, api_factory=API):
        self.paths = paths
        self.service = service or Service()
        self.api_factory = api_factory
        self.profiles = ProfileStore(paths)

    def config(self):
        if not self.paths.runtime.exists():
            raise ControllerError('No runtime configuration. Use a profile first.')
        return parse(self.paths.runtime.read_bytes())

    def candidate(self, data, source_dir):
        config = build(data, Settings.load(self.paths), source_dir)
        atomic_write(self.paths.candidate, encode(config))
        self.service.validate(self.paths.candidate, self.paths.core)
        return config

    def apply(self, profile_id, health_url=None):
        self.require_clean()
        profile = self.profiles.get(profile_id)
        source = Path(profile['path'])
        data = source.read_bytes()
        config = self.candidate(data, source.parent if profile['kind'] == 'local' else None)
        metadata = {'profile_id': profile_id, 'source_sha256': hashlib.sha256(data).hexdigest(), 'applied_at': now()}
        self._activate(config, metadata, health_url)
        if source.read_bytes() != data:
            raise ControllerError('Source changed externally while applying; controller never writes source files.')

    def require_clean(self):
        if self.paths.transaction.exists():
            raise ControllerError('An interrupted transaction needs recovery. Run mmctl recover before changing state.')

    def _activate(self, config, metadata, health_url=None):
        self.require_clean()
        status = self.service.status()
        owned = int(status.get('MainPID', 0))
        duplicates = processes(owned)
        if config['tun']['enable'] and duplicates:
            raise ControllerError('Another Mihomo/Clash instance is active; stop it explicitly first. See mmctl diagnostics for PIDs.')
        if status.get('LoadState') != 'loaded':
            raise ControllerError('minimal-mihomo.service is not installed; run ./install.sh first.')
        preflight(config, owned)
        # A durable journal + backup handles crashes between service stop and commit.
        backup = self.paths.runtime.parent / 'transaction.previous.yaml'
        previous = self.paths.good.read_bytes() if self.paths.good.exists() else None
        if previous is not None:
            atomic_write(backup, previous)
        journal = {'had_previous': previous is not None, 'was_active': status.get('ActiveState') == 'active',
                   'previous_state': read_json(self.paths.state, {})}
        write_json(self.paths.transaction, journal)
        try:
            self.service.action('stop')
            preflight(config)
            atomic_write(self.paths.runtime, encode(config))
            self.service.action('start')
            self._verify(config)
            if health_url:
                self._health(health_url, config)
            atomic_write(self.paths.good, encode(config))
            write_json(self.paths.state, metadata)
            self.paths.transaction.unlink()
        except (Exception, KeyboardInterrupt) as error:
            try:
                self.recover()
            except Exception:
                raise ControllerError('Apply failed AND rollback failed. Recovery journal retained; run mmctl recover. Inspect service diagnostics.') from None
            recovery = 'Previous configuration restored.' if journal['had_previous'] else 'No previous known-good configuration exists; service left stopped.'
            if isinstance(error, ControllerError):
                raise ControllerError(str(error) + ' ' + recovery) from None
            raise ControllerError('Apply interrupted or failed. ' + recovery) from None

    def _verify(self, config):
        try:
            self.api_factory(config).wait(config)
        except ControllerError:
            state = self.service.status()
            if state.get('ActiveState') == 'active':
                try:
                    self._health('https://www.gstatic.com/generate_204', config, 5)
                except ControllerError:
                    raise ControllerError('Current proxy node/configuration is unreachable; Mihomo API also did not respond.') from None
            raise
        state = self.service.status()
        if state.get('ActiveState') != 'active' or int(state.get('MainPID', 0)) <= 0:
            raise ControllerError('Owned systemd service is not active after API verification.')
        if config['tun']['enable'] and not network_status()['tun_interface_exists']:
            raise ControllerError('API claims TUN is enabled, but the Mihomo interface is absent.')

    @staticmethod
    def _health(url, config, timeout=15):
        import subprocess
        if not url.startswith('https://'):
            raise ControllerError('Health URL must use HTTPS.')
        result = subprocess.run(['curl', '--silent', '--fail', '--noproxy', '', '--proxy',
                                 f"http://127.0.0.1:{config['mixed-port']}", '--max-time', str(timeout), url],
                                capture_output=True, timeout=timeout + 5)
        if result.returncode:
            raise ControllerError(f'Current proxy node cannot connect within {timeout} seconds.')

    def monitor_or_stop(self, timeout=30):
        """Stop our TUN after a sustained outage so direct networking recovers."""
        if self.service.status().get('ActiveState') != 'active':
            return {'active': False, 'stopped': False}
        config = self.config()
        state = read_json(self.paths.state, {})
        profile_id = state.get('profile_id')
        try:
            profile = self.profiles.get(profile_id).get('name') if profile_id else None
        except ControllerError:
            profile = None
        node = None
        try:
            groups = self.api_factory(config).groups()
            if isinstance(groups, dict):
                for group_name in ('PROXY', 'GLOBAL'):
                    selected = groups.get(group_name, {}).get('now')
                    if selected and selected not in ('DIRECT', 'REJECT'):
                        node = selected
                        break
        except ControllerError:
            pass
        try:
            self._health('https://www.gstatic.com/generate_204', config, timeout)
            return {'active': True, 'stopped': False}
        except ControllerError:
            self.service.action('stop')
            return {'active': False, 'stopped': True, 'profile': profile or 'current profile',
                    'node': node, 'timeout': timeout}

    def recover(self):
        if not self.paths.transaction.exists():
            raise ControllerError('No interrupted transaction to recover.')
        journal = read_json(self.paths.transaction, {})
        self.service.action('stop')
        if journal['had_previous']:
            data = (self.paths.runtime.parent / 'transaction.previous.yaml').read_bytes()
            atomic_write(self.paths.runtime, data)
            atomic_write(self.paths.good, data)
            write_json(self.paths.state, journal['previous_state'])
            if journal['was_active']:
                config = parse(data)
                preflight(config)
                self.service.action('start')
                self._verify(config)
        else:
            self.paths.runtime.unlink(missing_ok=True)
            self.paths.good.unlink(missing_ok=True)
            self.paths.state.unlink(missing_ok=True)
        self.paths.transaction.unlink()

    def rollback(self):
        self.require_clean()
        if not self.paths.good.exists():
            raise ControllerError('No last-known-good runtime exists.')
        config = parse(self.paths.good.read_bytes())
        self.service.validate(self.paths.good, self.paths.core)
        self._activate(config, read_json(self.paths.state, {}))

    def start(self, restart=False):
        self.require_clean()
        config = self.config()
        # A start of an existing runtime is verified with the same transaction machinery.
        status = self.service.status()
        if status.get('ActiveState') == 'active' and not restart:
            self._verify(config)
            return
        self.service.validate(self.paths.runtime, self.paths.core)
        self._activate(config, read_json(self.paths.state, {}))
