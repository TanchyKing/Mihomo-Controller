#!/usr/bin/env bash
# Offline recovery: no internet access or chat session required.
set -euo pipefail
systemctl stop minimal-mihomo.service
controller_state=$(systemctl show minimal-mihomo.service -p ActiveState --value)
controller_pid=$(systemctl show minimal-mihomo.service -p MainPID --value)
if [[ "$controller_pid" != 0 || ( "$controller_state" != inactive && "$controller_state" != failed ) ]]; then
  echo 'Our core has not stopped. Do not launch another TUN core yet.' >&2
  exit 1
fi
nohup /usr/bin/clash-verge >/dev/null 2>&1 &
echo 'Our core is stopped. Clash Verge is opening; enable your previous profile/proxy mode there.'
