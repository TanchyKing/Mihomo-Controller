#!/usr/bin/python3
"""Route systemd-resolved through Mihomo for exactly the service lifetime."""
import subprocess
import sys
import time
from pathlib import Path
import re

LINK = 'Mihomo'
SERVER = '127.0.0.1:1053'


def run(*args, check=True):
    return subprocess.run(args, check=check, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=10)


def resolved_is_active():
    return run('systemctl', 'is-active', '--quiet', 'systemd-resolved',
               check=False).returncode == 0


def tun_enabled(config):
    text = Path(config).read_text(errors='replace')
    match = re.search(r'(?m)^tun:\s*$([\s\S]*?)(?=^[^ \t]|\Z)', text)
    return bool(match and re.search(r'(?m)^\s+enable:\s+true\s*$', match.group(1)))


def apply(config):
    if not resolved_is_active():
        return
    if not tun_enabled(config):
        return
    for _ in range(100):
        if Path('/sys/class/net', LINK).exists():
            break
        time.sleep(0.1)
    else:
        raise SystemExit('Mihomo interface did not appear; refusing to leave system DNS unchanged')
    run('resolvectl', 'dns', LINK, SERVER)
    run('resolvectl', 'domain', LINK, '~.')
    run('resolvectl', 'default-route', LINK, 'yes')
    run('resolvectl', 'flush-caches')


def restore():
    if resolved_is_active():
        # The physical link was never changed. Reverting/removing this virtual
        # link therefore exposes its original DNS settings again.
        run('resolvectl', 'revert', LINK, check=False)
        run('resolvectl', 'flush-caches', check=False)


def main(argv):
    if len(argv) < 2 or argv[1] not in ('apply', 'restore'):
        raise SystemExit('usage: resolved_dns.py apply CONFIG|restore')
    if argv[1] == 'apply':
        if len(argv) != 3:
            raise SystemExit('apply requires the runtime configuration path')
        apply(argv[2])
    else:
        restore()


if __name__ == '__main__':
    main(sys.argv)
