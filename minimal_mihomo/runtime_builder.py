from copy import deepcopy
from pathlib import Path
import yaml
from .storage import ControllerError

MAX_PROFILE_BYTES = 10 * 1024 * 1024


def parse(data: bytes) -> dict:
    if len(data) > MAX_PROFILE_BYTES or data.lstrip().lower().startswith((b'<html', b'<!doctype')):
        raise ControllerError('Profile is too large or is an HTML error response.')
    try:
        document = yaml.safe_load(data)
    except (yaml.YAMLError, UnicodeError, RecursionError):
        raise ControllerError('Invalid YAML profile (details hidden to protect credentials).') from None
    if not isinstance(document, dict) or not any(k in document for k in ('proxies', 'proxy-providers', 'rules')):
        raise ControllerError('Expected a Clash/Mihomo YAML mapping with proxies, providers, or rules.')
    def inspect(value, ancestors=(), depth=0):
        if depth > 50 or id(value) in ancestors:
            raise ControllerError('Recursive or excessively nested YAML is unsupported.')
        if isinstance(value, (dict, list)):
            for item in value.values() if isinstance(value, dict) else value:
                inspect(item, (*ancestors, id(value)), depth + 1)
    inspect(document)
    for key in ('proxies', 'proxy-groups', 'rules'):
        if key in document and not isinstance(document[key], list):
            raise ControllerError(f'{key} must be a YAML list.')
    for key in ('proxies', 'proxy-groups'):
        if any(not isinstance(item, dict) or not isinstance(item.get('name'), str)
               for item in document.get(key, [])):
            raise ControllerError(f'{key} must contain named mappings.')
    for key in ('proxy-providers', 'rule-providers'):
        if key in document and (not isinstance(document[key], dict) or
                                any(not isinstance(p, dict) for p in document[key].values())):
            raise ControllerError(f'{key} must contain provider mappings.')
    return document


def build(data: bytes, settings, source_dir: Path | None = None) -> dict:
    settings.validate()
    result = deepcopy(parse(data))
    # Resolve relative provider resources against a local source without writing it.
    for kind in ('proxy-providers', 'rule-providers'):
        for provider in (result.get(kind) or {}).values():
            if provider.get('path'):
                path = Path(provider['path'])
                if provider.get('type') == 'file':
                    if not path.is_absolute():
                        if source_dir is None:
                            raise ControllerError('Subscription file providers require absolute paths.')
                        provider['path'] = str((source_dir / path).resolve())
                else:
                    # Downloaded provider caches must stay controller-owned.
                    provider.pop('path', None)
    # The application owns listeners. Do not inherit Verge's conflicting ports/API.
    for key in ('external-controller-tls', 'external-controller-unix', 'external-controller-pipe',
                'external-ui', 'external-ui-url', 'external-ui-name', 'listeners', 'tunnels',
                'ss-config', 'vmess-config', 'tuic-server', 'iptables', 'routing-mark'):
        result.pop(key, None)
    for key in ('port', 'socks-port', 'redir-port', 'tproxy-port'):
        result[key] = 0
    result.update({'mixed-port': settings.mixed_port, 'allow-lan': False,
                   'bind-address': '127.0.0.1', 'external-controller': f'127.0.0.1:{settings.api_port}',
                   'secret': settings.api_secret})
    result['tun'] = {
        'enable': settings.tun, 'stack': settings.stack, 'device': 'Mihomo',
        'dns-hijack': settings.dns_hijack, 'auto-route': settings.auto_route,
        'auto-redirect': settings.auto_redirect, 'strict-route': settings.strict_route,
        'auto-detect-interface': settings.auto_detect_interface and not bool(settings.interface),
        'mtu': settings.mtu, 'route-exclude-address': settings.route_exclude_address,
    }
    if settings.secure_dns:
        # Keep source fake-IP/filter behavior, but never forward captured DNS to
        # plaintext ISP resolvers. DNS traffic follows the active routing rules;
        # the node resolver is encrypted and avoids a hostname bootstrap loop.
        dns = result.get('dns') if isinstance(result.get('dns'), dict) else {}
        dns.update({
            'enable': True,
            # systemd-resolved is routed to this listener while the TUN service
            # is active (see scripts/resolved_dns.py).
            'listen': '127.0.0.1:1053',
            'respect-rules': True,
            'default-nameserver': ['tls://1.1.1.1', 'tls://8.8.8.8'],
            'nameserver': ['https://1.1.1.1/dns-query#GLOBAL',
                           'https://8.8.8.8/dns-query#GLOBAL'],
            'proxy-server-nameserver': ['tls://1.1.1.1', 'tls://8.8.8.8'],
            'direct-nameserver': ['https://1.1.1.1/dns-query', 'https://8.8.8.8/dns-query'],
        })
        result['dns'] = dns
    result.pop('interface-name', None)
    if settings.interface:
        result['interface-name'] = settings.interface
    if settings.mode != 'source':
        result['mode'] = settings.mode
    result.setdefault('mode', 'rule')
    if result['mode'] not in ('rule', 'global', 'direct'):
        raise ControllerError('Source mode must be rule, global, or direct.')
    if settings.ipv6 is not None:
        result['ipv6'] = settings.ipv6
    return result


def encode(config: dict) -> bytes:
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode()
