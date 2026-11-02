# Telemt integration notes

The integration follows the official `telemt/telemt` release and configuration
layout, but does not execute its remote installer.

- Source: <https://github.com/telemt/telemt>
- Binary: pinned `3.5.16` GNU archive selected by architecture.
- Integrity: the SHA-256 published beside each official release archive is
  embedded in `install.sh` and checked before extraction.
- Account: isolated `xraydo-telemt` system user without a login shell.
- Binary: `/usr/local/bin/telemt`.
- Configuration: `/usr/local/etc/xraydo/cores/telemt/telemt.toml`, mode `0640`.
- Runtime data: `/var/lib/xraydo/telemt`.
- Logs: `/var/log/xraydo/telemt`.
- Service: `xraydo-telemt.service` for systemd.
- Capabilities: only `CAP_NET_BIND_SERVICE` in the systemd unit.
- Control API: loopback-only, CIDR-whitelisted, and protected by a random
  `Authorization` value.

`xraydo core telemt` writes a temporary configuration, starts the pinned binary
on temporary ports, and invokes Telemt's own `healthcheck ... --mode liveness`.
Only a successful configuration is atomically moved into place. The previous
configuration is retained as `telemt.toml.previous`.

Local lifecycle commands use the unit installed by `install.sh`:

```sh
xraydo telemt version
xraydo telemt status
xraydo telemt logs --lines 100
sudo xraydo telemt start
sudo xraydo telemt stop
sudo xraydo telemt restart
```

They use systemd without invoking a shell. `status`, `version`, and `logs` have
stable JSON output through the global `--json` option.

Telemt 3.5.16 is required because upstream versions after 3.5.14 fixed stalled
middle-proxy relays and unflushed adaptive relay writes. The old behavior could
complete the client and upstream handshake while carrying no useful traffic.

Advanced configuration exposes only fields verified against Telemt 3.5.16:

```sh
xraydo core telemt --user bob --server proxy.example.com \
  --ipv6 --prefer-ip 6 --listen-ip 0.0.0.0 --listen-ip :: \
  --no-middle-proxy --first-byte-timeout 300 \
  --handshake-timeout 60 --keepalive 15 --ack-timeout 90
```

At least one IP family must remain enabled. Listener values are parsed as IP
addresses, timeout values are bounded to 1–86400 seconds, and the generated
configuration is still accepted only after Telemt's native liveness check.
