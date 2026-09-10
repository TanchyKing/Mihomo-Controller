#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID == 0 ]]; then echo 'Run as the desktop user.' >&2; exit 1; fi
sudo systemctl disable --now minimal-mihomo.service
sudo rm -f /etc/systemd/system/minimal-mihomo.service /etc/polkit-1/rules.d/49-minimal-mihomo.rules /usr/libexec/minimal-mihomo-resolved-dns
sudo systemctl daemon-reload
rm -f "$HOME/.local/bin/mmctl" "$HOME/.local/share/applications/minimal-mihomo.desktop"
rm -f "$HOME/.local/bin/mmgui" "$HOME/.local/bin/mm-return-to-clash-verge" "$HOME/.local/share/applications/minimal-mihomo-recovery.desktop"
rm -rf "$HOME/.local/lib/minimal-mihomo/venv"
echo 'Uninstalled. Controller data, subscriptions, settings and source profiles were preserved.'
