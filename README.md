# Minimal Mihomo Controller

Ubuntu CLI/backend for Mihomo, with immutable source profiles, systemd ownership,
explicit desired/runtime verification, and automatic failed-apply recovery.
**CLI/backend plus a native PySide6 GUI for testing. Live TUN acceptance is still pending.**

## Install

Requires Python 3.10+, `python3-venv`, systemd/polkit, `iproute2`, curl, and
`/usr/bin/mihomo` or `/usr/bin/verge-mihomo`. Run as the desktop user:

```sh
./install.sh
export PATH="$HOME/.local/bin:$PATH"
mmctl --help
```

The installer requests sudo for the fixed system unit and polkit rule. It reuses
available public geodata assets from Verge in its own core directory. It leaves
Clash Verge installed and enables this service at boot (it only starts after a
runtime profile has been applied). Disable Clash Verge autostart to avoid two TUN cores.
This is a single-user installation. Run `./uninstall.sh` to remove it; data remains.
The desktop launcher opens the GUI; `mmgui` also opens it. A separate **Return to
Clash Verge** launcher provides offline recovery. See [RECOVERY.md](RECOVERY.md).

## Use

```sh
mmctl profile add-yaml /absolute/path/to/gcp-oregon.yaml --name 'GCP Oregon'
mmctl profile list
mmctl settings set route_exclude_address '["34.105.112.255/32"]'
mmctl settings set interface wlp0s20f3
# Exit Clash Verge and stop its core through its normal controls first.
mmctl profile use PROFILE_ID --health-url https://www.gstatic.com/generate_204
mmctl status
mmctl verify
mmctl groups
mmctl select 'GROUP NAME' 'NODE NAME'
mmctl latency 'NODE NAME'
mmctl external-ip
mmctl logs --lines 100
mmctl diagnostics
mmctl stop
mmctl start
mmctl restart
```

The example exclusion is specific to that profile's proxy endpoint, not a default.
Settings are global desired overrides and apply only on `profile use`; `restart`
restarts the applied config. `mode` defaults to `source`, preserving Rule mode.
Use `settings show` for TUN stack, route switches, interface, DNS hijack, secure DNS, MTU,
exclusions, IPv6, mixed port (17890), and API port (19099). Values accept JSON:

```sh
mmctl settings set tun true
mmctl settings set mode rule
mmctl settings set ipv6 false
mmctl settings set dns_hijack '["any:53"]'
mmctl settings set secure_dns true
```

Mihomo IPv6 false does **not** disable Ubuntu IPv6. Inspect both external families;
this controller does not claim all traffic is proxied or infer domestic routing
intent from geolocation. Source DNS behavior is retained where possible, but resolver
upstreams are replaced with encrypted DNS when `secure_dns` is enabled. Rules are retained.

Subscriptions support standard Clash/Mihomo YAML only:

```sh
mmctl profile add-subscription 'https://provider.example/subscription' --name 'Subscription A'
mmctl profile refresh PROFILE_ID
mmctl profile duplicate PROFILE_ID --name 'Test metadata'
mmctl profile remove PROFILE_ID
```

Avoid putting sensitive URLs in shell history (e.g. enter them with `read -rs` into
a temporary variable). CLI arguments may be visible to other local processes.
Refresh validates but does not activate a new snapshot; use the profile explicitly.
Snapshots remain retained, with no automatic pruning in v1.

## Browser DNS troubleshooting

If GitHub works but Chrome times out on Google or ChatGPT, while `curl` can reach
those sites, the proxy transport may be healthy and Chrome's resolver path may be
the problem. First restore `chrome://flags/#enable-quic` to **Default**. Then open
`chrome://settings/security`, enable **Use secure DNS**, and select **Cloudflare
(1.1.1.1)** instead of the current service provider. If Chrome asks for a custom
resolver URL, use:

```text
https://cloudflare-dns.com/dns-query
```

This browser-level setting continues working when the controller is off and avoids
changing DNS for every Wi-Fi or hotspot connection. When TUN is active, the encrypted
DNS connection follows the active Mihomo route. A DNS-leak result and an HTTPS exit-IP
result measure different paths: Global mode controls the latter but does not replace
a browser's independently selected encrypted resolver.

## Recovery and coexistence

A failed apply automatically restores the previous verified configuration. Run
`mmctl rollback` to reapply last-good, or `mmctl profile use OLD_ID` to switch back
to another source. For an interrupted transaction run `mmctl recover` before a new
apply. If recovery itself fails, the journal remains; resolve the reported port or
process conflict and retry. No known-good config on first launch means failure
leaves this service stopped; it never claims a working fallback exists.

```sh
systemctl status minimal-mihomo.service
journalctl -u minimal-mihomo.service -n 100
mmctl diagnostics
```

Raw journalctl output is not redacted; use `mmctl logs` before sharing. Do not share
runtime YAML, settings.json, subscription snapshots or profile registry: they may
contain credentials. To return to Clash Verge, stop this controller's service first,
then launch Verge. Never run two TUN controllers simultaneously.

Boot startup is enabled by the installer and can also be changed in the GUI. To
disable it from a terminal:

```sh
sudo systemctl disable minimal-mihomo.service
```

A systemd boot start cannot perform the interactive CLI conflict check; keep Clash
Verge autostart off before opting in. After boot, run `mmctl verify`.

## Development and acceptance

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

See [architecture.md](architecture.md) and [ACCEPTANCE.md](ACCEPTANCE.md). Unit tests
use fake systemd/API boundaries; they do not change the host network. Live acceptance
must demonstrate single owned PID, TUN API=true, Mihomo interface, Oregon exit for
the GCP profile, domestic rule routing, A→B→A source hashes, service restart and
boot persistence before considering the controller verified for daily use. The GUI
was added early at the user’s request to make this testing easier.

API behavior follows the [official Mihomo API documentation](https://wiki.metacubex.one/en/api/).

## GUI

Run `mmgui`. Add YAML or subscriptions from the left panel; subscription URL entry
is masked. Clicking a profile applies it, and right-clicking offers apply, rename,
settings, duplicate, refresh, and remove actions. The Overview tab has an ON/OFF
proxy switch and prominent Rule/Global/Direct modes. The Proxies tab can test one
node or every available node. The IP check compares direct and forced-proxy routes.
Service, group/node, TUN/mode, logs, external-IP and diagnostic controls use the
same backend and process lock.
Operations run in a worker thread so the window remains responsive.

Save desired settings records them without activating a runtime; Apply selected
profile saves the editor settings and applies them transactionally. Start/restart
use the already applied runtime. Closing the window leaves the service running.
