import json
import errno
import re
import socket
import subprocess
from pathlib import Path
from .storage import ControllerError


def output(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    return result.stdout


def processes(owned_pid=0):
    found = []
    for path in Path('/proc').glob('[0-9]*/comm'):
        try:
            name = path.read_text().strip()
            pid = int(path.parent.name)
            if pid != owned_pid and name in ('mihomo', 'verge-mihomo', 'clash'):
                found.append({'pid': pid, 'name': name})
        except (OSError, ValueError):
            continue
    return found


def port_owners(ports):
    text = output(['ss', '-H', '-lntup'])
    found = []
    for line in text.splitlines():
        columns = line.split()
        if len(columns) < 5:
            continue
        try:
            port = int(columns[4].rsplit(':', 1)[1])
        except (ValueError, IndexError):
            continue
        if port in ports:
            matches = re.findall(r'\("([^\"]+)",pid=(\d+)', line)
            for name, pid in matches or [('unavailable (permissions)', '0')]:
                found.append({'port': port, 'pid': int(pid), 'name': name, 'protocol': columns[0]})
    return found


def preflight(config, owned_pid=0):
    duplicates = processes(owned_pid)
    if duplicates and config['tun']['enable']:
        summary = ', '.join(f"PID {p['pid']} / {p['name']}" for p in duplicates)
        raise ControllerError('Another Mihomo/Clash instance is active: ' + summary + '. Running two TUN controllers can break routing. Stop it explicitly first.')
    ports = [config['mixed-port'], int(config['external-controller'].rsplit(':', 1)[1])]
    # ss supplies owners; a server-style probe verifies actual availability.
    owners = port_owners(ports)
    for port in ports:
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM) if port == ports[0] else (socket.SOCK_STREAM,):
            protocol = 'tcp' if kind == socket.SOCK_STREAM else 'udp'
            entries = [p for p in owners if p['port'] == port and p.get('protocol', protocol) == protocol]
            # Unprivileged ss may report PID 0 for a listener owned by the
            # already-verified systemd core. With no duplicate Mihomo process,
            # the existing listener on its configured port is safe to replace.
            if owned_pid and entries and all(p['pid'] in (0, owned_pid) for p in entries):
                continue
            with socket.socket(socket.AF_INET, kind) as sock:
                try:
                    if kind == socket.SOCK_STREAM:
                        # Like Go's TCP listener, allow reuse after closed connections.
                        # Without this, TIME_WAIT produces false "owner unavailable"
                        # failures immediately after stopping the previous core.
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(('127.0.0.1', port))
                    if kind == socket.SOCK_STREAM:
                        sock.listen(1)
                except OSError as error:
                    if error.errno != errno.EADDRINUSE:
                        raise ControllerError(f'Cannot check {protocol.upper()} port {port}: {error.strerror or "socket error"}.') from None
                    # Refresh owners: another process may have started since ss ran.
                    entries = [p for p in port_owners([port])
                               if p['port'] == port and p.get('protocol', protocol) == protocol]
                    detail = ', '.join(f"PID {p['pid'] or 'unknown'} / {p['name']}" for p in entries)
                    if detail:
                        raise ControllerError(f'Port {port} ({protocol.upper()}) is already used by {detail}.') from None
                    raise ControllerError(f'Port {port} ({protocol.upper()}) cannot be bound, but no listener owner is visible. A socket may still be closing or ownership may be hidden; retry shortly and inspect ss -lntup.') from None


def network_status():
    routes = json.loads(output(['ip', '-j', 'route', 'show', 'default']) or '[]')
    addresses = json.loads(output(['ip', '-j', 'addr']) or '[]')
    return {'default_interfaces': [r.get('dev') for r in routes],
            'tun_interface_exists': any(a['ifname'] == 'Mihomo' for a in addresses),
            'global_ipv6_exists': any(i.get('family') == 'inet6' and i.get('scope') == 'global'
                                      for a in addresses for i in a.get('addr_info', []))}


def external_ip(config=None):
    result = {}
    routes = [('direct', None)]
    if config:
        routes.append(('proxy', f"http://127.0.0.1:{config['mixed-port']}"))
    for route, proxy in routes:
        result[route] = {}
        for version in ('4', '6'):
            try:
                args = ['curl', '-' + version, '--silent', '--show-error', '--fail', '--max-time', '15']
                args += ['--noproxy', '*'] if proxy is None else ['--noproxy', '', '--proxy', proxy]
                data = subprocess.run(args + ['https://ipinfo.io/json'],
                                      capture_output=True, text=True, timeout=20)
                if data.returncode:
                    raise ValueError()
                result[route]['ipv' + version] = {
                    k: v for k, v in json.loads(data.stdout).items()
                    if k in ('ip', 'country', 'region', 'city', 'org')
                }
            except (ValueError, subprocess.TimeoutExpired):
                result[route]['ipv' + version] = {'status': 'unavailable'}
    result['note'] = 'Direct is the physical OS route. Proxy is forced through Mihomo’s mixed port. With TUN active they may match; a mismatch proves the local proxy works, while rules decide which ordinary apps use it.'
    return result
