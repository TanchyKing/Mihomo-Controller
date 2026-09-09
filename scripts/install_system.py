#!/usr/bin/env python3
"""Root-only installer of a fixed unit and narrowly scoped polkit rule."""
import argparse
import os
from pathlib import Path
import pwd
import re
import subprocess

p = argparse.ArgumentParser()
p.add_argument('user')
p.add_argument('data')
p.add_argument('--print-unit', action='store_true', help='Render for inspection without installing')
a = p.parse_args()
if os.geteuid() != 0 and not a.print_unit:
    raise SystemExit('Run this helper using sudo from install.sh.')
account = pwd.getpwnam(a.user)
if account.pw_uid == 0 or not re.fullmatch(r'[a-z_][a-z0-9_-]*', a.user):
    raise SystemExit('A regular desktop user is required.')
data = Path(a.data).resolve()
if not data.is_relative_to(Path(account.pw_dir).resolve()) or any(c in str(data) for c in '\n\r%"\\'):
    raise SystemExit('Data directory must be under the desktop user home, without systemd specifiers.')
binary = next((p for p in ('/usr/bin/mihomo', '/usr/bin/verge-mihomo') if Path(p).is_file()), None)
if not binary:
    raise SystemExit('Mihomo binary missing.')
unit = f'''[Unit]
Description=Minimal Mihomo Controller core
After=network-online.target
Wants=network-online.target
ConditionPathExists={data}/runtime/runtime.yaml

[Service]
Type=simple
User={a.user}
Group={account.pw_gid}
ExecStart={binary} -d "{data}/core" -f "{data}/runtime/runtime.yaml"
Restart=on-failure
RestartSec=3
TimeoutStopSec=20
KillMode=control-group
UMask=0077
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths="{data}"
ProtectHome=read-only
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
StandardOutput=journal
StandardError=journal
LogRateLimitIntervalSec=30s
LogRateLimitBurst=100

[Install]
WantedBy=multi-user.target
'''
if a.print_unit:
    print(unit)
    raise SystemExit(0)
Path('/etc/systemd/system/minimal-mihomo.service').write_text(unit)
rule = f'''// Installed by Minimal Mihomo Controller. No access to other units.
polkit.addRule(function(action, subject) {{
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") == "minimal-mihomo.service" &&
        ["start", "stop", "restart"].indexOf(action.lookup("verb")) >= 0 &&
        subject.user == "{a.user}" && subject.local && subject.active) {{
        return polkit.Result.YES;
    }}
}});
'''
rulepath = Path('/etc/polkit-1/rules.d/49-minimal-mihomo.rules')
rulepath.parent.mkdir(parents=True, exist_ok=True)
rulepath.write_text(rule)
subprocess.run(['systemctl', 'daemon-reload'], check=True)
subprocess.run(['systemctl', 'enable', 'minimal-mihomo.service'], check=True)
print('Service installed and enabled for automatic startup once a runtime profile exists.')
