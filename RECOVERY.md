# Switching safely and returning to Clash Verge

The GUI may be opened, profiles added, subscriptions refreshed and settings edited
while Clash Verge remains running. Do not activate our TUN service until Clash
Verge and its core have stopped. Closing its window is not necessarily quitting it.

Before the first test, keep the **Return to Clash Verge** desktop launcher or a
Konsole window available. The launcher and script work without internet access or
this chat. Our first launch has no known-good runtime of its own, so it cannot
promise automatic restoration of the existing Clash connection.

To test:

1. Open Minimal Mihomo Controller. Add the desired YAML or subscription.
2. Review the TUN settings and select the profile. Enable the optional health check.
3. Quit Clash Verge using its normal controls and confirm its core has stopped.
4. Click **Apply selected profile**. Check desired/runtime TUN, service status and
   **External IP (IPv4 + IPv6)**. Do not infer network success from TUN=true alone.

If the test fails, click **Return to Clash Verge** in our GUI, use the corresponding
application-menu launcher, or run this command in Konsole:

```sh
~/.local/bin/mm-return-to-clash-verge
```

The recovery action stops only `minimal-mihomo.service`, verifies its PID is zero
and its state is stopped, then opens Clash Verge. If stopping fails, it refuses to
start a competing core. Enable the previous profile/proxy mode in Clash Verge and
verify connectivity there. Nothing requires a response from the assistant.

The existing Clash Verge installation and source YAML are preserved. This is a
recovery path, not a guarantee of uninterrupted connectivity: external outages or
Clash Verge's original GUI/core problems can still prevent reconnection.

Closing our GUI leaves its systemd service running. Use **Stop our core** or the
recovery action to stop it. No automatic switch or unattended timeout is armed.
