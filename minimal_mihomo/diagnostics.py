from dataclasses import asdict
from pathlib import Path
import subprocess
from .mihomo_api import API
from .models import Settings
from .network import network_status, port_owners, processes
from .redaction import redact, redact_url, collect_secrets
from .service import detect_binary
from .storage import ControllerError, read_json


def report(controller):
    paths = controller.paths
    settings = Settings.load(paths)
    service = controller.service.status()
    state = read_json(paths.state, {})
    profile = controller.profiles.all().get(state.get('profile_id'), {})
    result = {'core_binary': detect_binary(), 'service': service, 'runtime_path': str(paths.runtime),
              'active_profile_id': state.get('profile_id'), 'profile_name': profile.get('name'),
              'source': redact_url(profile['url']) if profile.get('url') else profile.get('path'),
              'desired_tun': settings.tun, 'desired_mode': settings.mode,
              'mixed_port': settings.mixed_port, 'api_port': settings.api_port,
              'interrupted_transaction': paths.transaction.exists(), **network_status(),
              'other_processes': processes(int(service.get('MainPID', 0))),
              'port_owners': port_owners([7890, 9090, settings.mixed_port, settings.api_port]),
              'ipv6_note': 'IPv6 presence is not a routing guarantee. Run external-ip to inspect both families; no global sysctl is changed.'}
    if profile.get('path') and Path(profile['path']).exists():
        result['source_mtime'] = Path(profile['path']).stat().st_mtime
    version = subprocess.run([detect_binary(), '-v'], capture_output=True, text=True, timeout=5)
    result['core_version'] = version.stdout.strip()
    if paths.runtime.exists():
        config = controller.config()
        result['applied_tun'] = config['tun']['enable']
        result['applied_mode'] = config['mode']
        result['settings_pending'] = any(config['tun'].get(k) != v for k, v in {
            'enable': settings.tun, 'stack': settings.stack, 'mtu': settings.mtu}.items()) or (settings.mode != 'source' and settings.mode != config['mode'])
        try:
            api = API(config)
            actual = api.request('/configs')
            tun = actual.get('tun')
            runtime_tun = tun.get('enable') if isinstance(tun, dict) else tun
            result.update(runtime_tun=runtime_tun, runtime_mode=actual.get('mode'), proxy_groups=api.groups())
            result['state_mismatch'] = (runtime_tun is not settings.tun or
                                       actual.get('mode') != (config['mode'] if settings.mode == 'source' else settings.mode))
            if result['state_mismatch']:
                result['error'] = 'DESIRED / RUNTIME STATE MISMATCH'
        except ControllerError as error:
            result['runtime_tun'] = 'unknown'
            result['error'] = str(error)
    else:
        result['runtime_tun'] = 'unknown'
    return result


def safe_logs(controller, lines):
    secrets = []
    from .runtime_builder import parse
    for path in controller.paths.runtime.parent.glob('*.yaml'):
        if path.exists():
            try:
                secrets.extend(collect_secrets(parse(path.read_bytes())))
            except ControllerError:
                pass
    return redact(controller.service.logs(lines), secrets)
