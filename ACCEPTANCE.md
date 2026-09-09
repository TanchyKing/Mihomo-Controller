# Acceptance status

## Completed in this development session

- Confirmed Ubuntu environment with systemd PID 1, `/dev/net/tun`,
  `/usr/bin/verge-mihomo`, and default physical interface `wlp0s20f3`.
- Core reports Mihomo Meta v1.19.29, Linux amd64, with gVisor support.
- Found Clash Verge PID 20768 and verge-mihomo PID 27707; reported PID 27707
  owning TCP/UDP 127.0.0.1:7890. Neither process was stopped.
- Ports 17890 and 19099 had no listeners during inspection.
- Neither the Mihomo TUN interface nor a global IPv6 address was present at
  inspection time. This differs from the earlier network snapshot in the brief;
  current diagnostics report observations, not assumed historical state.
- 29 pytest cases passed. Coverage includes immutable sources, TUN override,
  A→B→A, rejected subscriptions, rollback, interrupted commit recovery, port
  conflicts, state mismatch, redaction, locking, and permissions.
- Real-core integration test started an isolated unprivileged core with TUN OFF,
  verified authenticated API state and dynamic group selection, rejected a wrong
  API token, and stopped that exact subprocess. This was not a TUN routing test.
- Rendered systemd unit passed `systemd-analyze verify` without warnings.
- Shell syntax, Python compilation, and Git whitespace checks passed.
- Both existing GCP profiles passed `verge-mihomo -t` with generated TUN=true
  candidates. Validation used temporary private runtime files and read-only links
  to existing geodata assets. Source hashes were unchanged:

| Source file in Verge's `profiles/` directory | SHA-256 |
| --- | --- |
| `LeW2jGWmJ73G.yaml` | `b23105705b98e74aa9c66aa9bcbf5e13449a5f851f76bbbc49515f61a4e81d16` |
| `LO8LlMF28cD6.yaml` | `79cc63c389c5d309accff0fc07d9a48488b335a93facc7cc91e03290bca9686a` |

Registered metadata only: GCP ID `a349fdb677d9`, second GCP profile ID
`c2485836cb3d`. No active profile or last-known-good runtime has been claimed.

## Pending live gate

`sudo -n true` requires a password, and `minimal-mihomo.service` is not installed.
The user must run the installer from a terminal where sudo can authenticate.
The installer supports the host's pip fallback when ensurepip is absent and copies
only public geodata assets from the existing Verge data directory, when available.
It does not start or stop either proxy application.

```sh
./install.sh
export PATH="$HOME/.local/bin:$PATH"
```

Then exit Clash Verge through its normal controls and confirm its core has stopped.
Do not indiscriminately kill processes. `mmctl diagnostics` shows remaining PIDs.
For these GCP test profiles, apply explicit controller settings and run:

```sh
mmctl settings set tun true
mmctl settings set interface wlp0s20f3
mmctl settings set route_exclude_address '["34.105.112.255/32"]'
mmctl profile use a349fdb677d9 --health-url https://www.gstatic.com/generate_204
mmctl verify
systemctl show minimal-mihomo.service -p ActiveState -p MainPID
ip -br addr
mmctl groups
mmctl external-ip
```

Check that the selected group node is GCP, TUN is enabled both in the API and OS,
and IPv4 exits via 34.105.112.255 / Oregon / US. An external-IP response alone does
not establish that domestic destinations follow DIRECT rules: check a domestic
request and its matching rule/outbound in the core's connection/log information.

Then test profile switching and restart:

```sh
mmctl profile use c2485836cb3d
mmctl profile use a349fdb677d9
mmctl restart
mmctl verify
```

Recompute both source SHA-256 hashes and compare with the table above. Only after
these live checks pass, consider disabling Clash Verge autostart and enabling
`minimal-mihomo.service` at boot. Reboot testing is a separate explicit step;
verify TUN and source hashes again afterward.

Update: the user requested a GUI before live testing. The PySide6 GUI is now
implemented and visually checked, with 37 tests passing across backend, real-core
API, GUI state display and recovery ordering. Live TUN routing remains unverified.
The system service is now installed and inactive; Clash Verge remains running.
See RECOVERY.md for the offline switch-back procedure.

## Port handover regression fix

The original availability probe could reject a free TCP port while old connections
remained in TIME_WAIT after closing Verge. Reproduced on real loopback sockets,
then fixed by using server-style SO_REUSEADDR and listen checks. Live TCP and UDP
listeners still block activation; error messages now include protocol and distinguish
non-address-in-use failures. All 40 tests pass. No live proxy process was stopped
while testing this fix.
