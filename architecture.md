# Minimal Mihomo Controller architecture

## Source boundary

A local profile record stores its absolute path. Reads do not normalize or write
that file. Subscription responses are bounded, parsed, built and tested with
Mihomo before a new uniquely named snapshot is published. Snapshots are mode
0400; refresh changes only the metadata pointer. Older snapshots remain available.
The profile registry and all generated credential-bearing files are mode 0600,
inside user-private directories. Local sources cannot reside in controller storage.

## Desired settings and generated configuration

`settings.json` is the only desired controller state. Runtime YAML is a derived
artifact, never a source for profile edits. Pending desired settings are distinct
from the last applied configuration and the running API state. The builder owns
TUN, local listeners and authenticated loopback API. It preserves rules and DNS.
`mode=source` preserves each profile's mode; explicit rule/global/direct overrides
are supported. `ipv6=null` preserves the profile. Turning Mihomo IPv6 off does
not disable host IPv6 and is never presented as an absence of IPv6 leaks.

Controller-owned listener fields intentionally replace imported listeners,
transparent ports, alternate API sockets and embedded UI settings. File providers
resolve relative paths against local source directories; HTTP provider cache paths
are removed so the core uses its own data directory. Full source profile bytes
are never changed. Provider downloads remain Mihomo's responsibility in v1.

## Apply and recovery

All CLI operations take one advisory process lock. A profile use reads source
bytes, builds `candidate.yaml`, and runs the installed core's `-t` test. Only
then does the controller record a durable transaction journal and previous-good
backup. It stops the single owned service, checks ports, atomically publishes the
candidate as `runtime.yaml`, and starts the service. The controller verifies the
API's TUN state, mode and expected group names, systemd state/PID, and OS TUN
interface. An optional HTTPS request through the mixed port gates success.

Only a verified launch advances `runtime.last-good.yaml` and state metadata.
Failure restores the previous good bytes and restarts them if previously active.
Rollback failure retains the recovery journal and reports both failures. An
interrupted transaction blocks further mutation until `mmctl recover`. The journal
contains the prior state so recovery also handles a crash during commit. `rollback`
reapplies the current last-good artifact; switching back to an older successful
profile uses `profile use ID`. No command automatically kills unrelated processes.

The atomicity boundary is controller runtime and metadata. Remote provider content,
network outages, power loss during systemd operations, and other routing managers
cannot be transactionally controlled. Recovery cannot guarantee connectivity when
an external conflict prevents the prior runtime from launching; it reports this.

## Privileged service and GUI/API boundary

The Python application always runs as the desktop user. A system unit runs Mihomo
as that same user with only ambient CAP_NET_ADMIN and CAP_NET_RAW. Root installs
the fixed unit and a polkit rule restricted to this local active user, this unit,
and start/stop/restart. No root Python daemon or generic privileged file writer is
exposed. The core has read-only access to the home directory except controller data,
a private temporary directory, bounded capabilities and no new privileges.

This is a single-user desktop service. The installer does not start it, enable it
at boot, stop Clash Verge, or change global firewall/sysctl/NetworkManager settings.
The service owns its process cgroup. Before activation, another Mihomo/Clash process
blocks TUN use, and configured ports must be available. This check cannot eliminate
races with unrelated applications starting simultaneously; API and service checks
provide a second validation stage.

The PySide6 GUI calls this backend and API as an ordinary user, using a worker
thread and the same process lock as the CLI. At the user’s request it was added
before live acceptance to make testing easier. It displays desired and actual
TUN separately and surfaces unknown state and divergence prominently. A local
recovery action verifies our service has stopped before opening Clash Verge;
it does not claim to verify or repair Clash Verge connectivity.

## Diagnostics and logging

REST requests bypass environment proxies, use loopback, require a random secret,
and have timeouts. Subscription URLs require HTTPS and are never logged verbatim.
Diagnostics omit config bodies. Core validation errors suppress raw output because
Mihomo may include secrets. Displayed journal output redacts known credentials,
credential fields, and URL paths/query strings. The original system journal is
managed by Mihomo/systemd; arbitrary core messages cannot be guaranteed secret-free.
Application logs contain command names and safe errors only and rotate at 256 KiB.

External-IP diagnostics run IPv4 and IPv6 independently over OS routing with curl
proxy inheritance disabled. They report available geolocation but cannot infer
arbitrary profile intent or prove that every route is proxied.
