#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID == 0 ]]; then
  echo 'Run ./install.sh as your desktop user. It requests sudo only for system installation.' >&2
  exit 1
fi
. /etc/os-release
if [[ "$ID" != ubuntu && "$ID" != debian && " ${ID_LIKE:-} " != *' debian '* ]]; then
  echo 'Only Ubuntu/Debian are supported.' >&2
  exit 1
fi
if [[ ! -x /usr/bin/mihomo && ! -x /usr/bin/verge-mihomo ]]; then
  echo 'Install /usr/bin/mihomo or /usr/bin/verge-mihomo first.' >&2
  exit 1
fi
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
controller_data="${XDG_DATA_HOME:-$HOME/.local/share}/minimal-mihomo"
controller_config="${XDG_CONFIG_HOME:-$HOME/.config}/minimal-mihomo"
controller_app="$HOME/.local/lib/minimal-mihomo"
umask 077
mkdir -p "$controller_data/runtime" "$controller_data/core" "$controller_config" "$controller_app" "$HOME/.local/bin" "$HOME/.local/share/applications"
if python3 -m venv "$controller_app/venv"; then
  "$controller_app/venv/bin/pip" install "$project_dir[gui]"
else
  # Ubuntu may provide pip but omit ensurepip/python3-venv.
  python3 -m venv --without-pip "$controller_app/venv"
  python3 -m pip --python "$controller_app/venv" install "$project_dir[gui]" || {
    echo 'Install python3-venv (or python3-pip >=22.3), then rerun.' >&2; exit 1;
  }
fi
ln -sfn "$controller_app/venv/bin/mmctl" "$HOME/.local/bin/mmctl"
ln -sfn "$controller_app/venv/bin/mmgui" "$HOME/.local/bin/mmgui"
install -m 700 "$project_dir/scripts/return-to-clash-verge.sh" "$HOME/.local/bin/mm-return-to-clash-verge"
"$controller_app/venv/bin/python" - "$controller_app" <<'PY'
from pathlib import Path
import sys
binary = str(Path(sys.argv[1]) / 'venv/bin/mmgui').replace('\\', '\\\\').replace('"', '\\"')
(Path.home() / '.local/share/applications/minimal-mihomo.desktop').write_text(
    '[Desktop Entry]\nType=Application\nName=Minimal Mihomo Controller\n'
    'Comment=Native controller with immutable source profiles\n'
    'Exec="' + binary + '"\n'
    'Icon=network-vpn\nTerminal=false\nCategories=Network;\n')
recovery = Path.home() / '.local/bin/mm-return-to-clash-verge'
(Path.home() / '.local/share/applications/minimal-mihomo-recovery.desktop').write_text(
    '[Desktop Entry]\nType=Application\nName=Return to Clash Verge\n'
    f'Exec=konsole --hold -e "{recovery}"\nIcon=network-vpn\nTerminal=false\nCategories=Network;\n')
PY
# Reuse only public geodata assets, never Verge profiles or configuration.
"$controller_app/venv/bin/python" - "$controller_data" <<'PY'
from pathlib import Path
import shutil
import sys
source = Path.home() / '.local/share/io.github.clash-verge-rev.clash-verge-rev'
destination = Path(sys.argv[1]) / 'core'
for name in ('Country.mmdb', 'geoip.dat', 'geosite.dat'):
    if (source / name).is_file() and not (destination / name).exists():
        shutil.copyfile(source / name, destination / name)
        (destination / name).chmod(0o600)
PY
sudo /usr/bin/python3 "$project_dir/scripts/install_system.py" "$(id -un)" "$controller_data"
echo 'Installed. Add ~/.local/bin to PATH if needed. Run mmctl --help.'
echo 'Clash Verge and all source profiles have been left intact.'
