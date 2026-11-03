<p align="center">
  <a href="./README.md"><img alt="English" src="https://img.shields.io/badge/Language-English-2563eb?style=for-the-badge"></a>
  <a href="./README.ru.md"><img alt="Русский" src="https://img.shields.io/badge/Язык-Русский-64748b?style=for-the-badge"></a>
</p>

# xraydo

`xraydo` is an interactive control panel and command-line tool for installing
and managing Xray, Hysteria 2, and Telemt on supported Debian and Ubuntu Server
releases. It works on a single server or in a protected master → node setup.

## Highlights

- one-command quick installation and a configurable advanced mode;
- one menu for users, access keys, subscriptions, TLS, updates, and removal;
- Xray REALITY, Hysteria 2, and Telemt-based Telegram MTProxy;
- Let's Encrypt certificates for a domain or a supported public IP address;
- short seven-character user IDs plus convenient numeric aliases;
- subscription links, QR codes, audit logs, and backups;
- authenticated and replay-protected master → node control plane;
- English and Russian installer and CLI interfaces.

## Quick installation

Download the current installer directly from the `master` branch and run it:

```bash
curl -fsSL https://raw.githubusercontent.com/blyaxyz/xraydo/refs/heads/master/install.sh -o install.sh
sudo bash ./install.sh
```

To skip the language/profile quiz and start the fully automatic quick
installation immediately:

```bash
sudo bash ./install.sh --quick
```

Or use `wget`:

```bash
wget -O install.sh https://raw.githubusercontent.com/blyaxyz/xraydo/refs/heads/master/install.sh
sudo bash ./install.sh
```

The installer checks the operating system, installs the required dependencies
and selected cores, creates systemd services, and opens the `xraydo` menu.

## Installation modes

| Mode | Select or run | Best for |
|---|---|---|
| Quick installation | `--quick` | Start a fully automatic setup without a quiz |
| Advanced installation | `advanced` | Custom roles, cores, TLS, ports, and limits |
| Telegram Proxy only | `--telemt-only` | A server that only needs Telemt/MTProxy |

Quick installation is the recommended choice for most users. It does not ask
for additional settings: xraydo installs all three cores, configures REALITY,
runs Telemt with Fake TLS on port `8443`, obtains a Let's Encrypt certificate
for the detected public address when supported, and creates the unlimited
`default` user. The final screen contains the subscription URL, its QR code,
and all client links.

Choose advanced installation when you need a domain, a standalone/master/node
role, different cores, custom ports, TLS settings, or user limits.

## Telegram Proxy only

```bash
sudo bash ./install.sh --telemt-only
```

This setup asks only for the interface language, listening port, and Fake TLS
domain. It detects the public address and configures the `hello` user, service,
and autostart automatically. The result includes both link formats:

```text
tg://proxy?server=...&port=...&secret=...
https://t.me/proxy?server=...&port=...&secret=...
```

A private copy is stored with `0600` permissions under `/var/lib/xraydo`. If
the chosen port is already in use, the installer reports the conflict instead
of silently selecting another port.

## First steps

```bash
sudo xraydo                 # open the interactive menu
sudo xraydo status          # check component and protocol health
sudo xraydo system status   # inspect the installation
xraydo --help               # list all CLI commands
```

The interactive menu covers the same routine administration as the CLI:
users, keys, subscriptions, TLS, services, updates, uninstallation, and full
removal. The Users section immediately shows the user list. Selecting a user
opens a complete card with comments, subscription, client links, credentials,
and available actions.

## Users and access keys

```bash
sudo xraydo user create alice
sudo xraydo user list
sudo xraydo user show alice
```

`user show` is an administrative command. It displays the user's comment,
status, subscription contents, credentials, and client links. Its output
contains secrets and must not be pasted into public logs or chats.

Access keys can also be inspected directly:

```bash
sudo xraydo key list --user alice --full
sudo xraydo key show ACCESS_KEY_ID
sudo xraydo key show ACCESS_KEY_ID --redact  # hide credentials
```

When a key is created, xraydo detects the server's public IP over HTTPS,
cross-checks the network country with RDAP, caches the result, and adds the
location to the key name, for example `🇵🇱 Poland · phone`. The same label is
used as the client-link remark. A temporary GeoIP failure never blocks key
creation, and automatic location labels can be disabled in settings.

Check or refresh the detected location with:

```bash
sudo xraydo country location --refresh
```

Each user receives a seven-character ID and a numeric alias. Commands accept
the username, short ID, or numeric alias.

## Xray REALITY

Inspect a REALITY target and configure the service with:

```bash
sudo xraydo reality scan --target ya.ru --sni ya.ru
xraydo reality configure --help
```

## Subscriptions and QR codes

```bash
sudo xraydo subscription create alice
sudo xraydo subscription show alice
sudo xraydo subscription qr alice
sudo xraydo subscription rotate alice
sudo xraydo subscription revoke alice
```

Secrets and bootstrap data are excluded from the shared journal. In
non-interactive mode, one-time credentials are written to a root-only file and
the installer prints its path. For the initial user, this file contains the
HTTPS subscription, full user card, and access keys.

## TLS and Let's Encrypt

The advanced setup quiz can request a certificate for a domain or supported
public IP address and asks for an email address. IP certificates require
Certbot 5.4 or newer; when the distribution package is too old, the installer
uses the current snap package.

```bash
sudo xraydo tls status
sudo xraydo tls acme-issue --identifier example.com --email admin@example.com
sudo xraydo tls acme-renew --identifier example.com
sudo xraydo tls node-list --node edge-1
sudo xraydo tls node-issue --node edge-1 --domain vpn.example.com
sudo xraydo tls node-assign CERTIFICATE_ID INBOUND_ID
```

The installer enables `xraydo-certbot-renew.timer` and a deploy hook. After a
successful renewal, xraydo imports the certificate and restarts dependent
services. Public TCP port 80 must be reachable for the ACME challenge.

## Services and components

```bash
sudo xraydo status
sudo xraydo telemt status
sudo xraydo hysteria status
sudo xraydo inbound list
sudo xraydo key list --user alice
sudo xraydo xray check-update --node all
sudo xraydo xray install --node all --canary 1 --max-unavailable 2
sudo xraydo inbound wss-tls --node edge-1 --port 443 \
  --public-address vpn.example.com --sni vpn.example.com \
  --http-host cdn.example.com --cert /path/server.crt --key /path/server.key
sudo xraydo inbound probe-port --node edge-1 --port 443 --transport tcp
sudo xraydo inbound set-port INBOUND_ID 8443 --apply --yes
sudo xraydo inbound set-sni INBOUND_ID cdn.example.com --apply --yes
sudo xraydo automation run --once
```

The subscription HTTPS server applies per-address and per-token rate limits and
temporarily bans repeated invalid-token attempts. REALITY rotations use
`inbound rotation-plan` followed by `inbound rotation-apply`; subscription links
change only after a healthy node ACK.

The interactive **Inbounds and REALITY** menu also exposes quick port and SNI
changes. Port switching applies to every managed Xray inbound, including SOCKS
and HTTP proxies. SNI switching is available for TLS and REALITY inbounds. A
remote port is probed by the authenticated agent first; configuration and
subscription links are committed only after the target reports healthy.

Use `--help` on any command group, for example `xraydo telemt --help`, to see
all available actions.

## Update and removal

You do not need the original `install.sh` after installation. The installed
`xraydo` command provides the complete lifecycle:

```bash
sudo xraydo system update --dry-run
sudo xraydo system update --yes
sudo xraydo system uninstall --yes  # keep configuration and state
sudo xraydo system purge --dry-run
sudo xraydo system purge --yes      # remove all xraydo data
```

> [!CAUTION]
> `purge` permanently removes users, keys, TLS data, logs, and state. Review
> the dry run and create a backup first.

## Master → node

Install the `master` profile on the management server and the `node` profile
on managed servers. The control plane uses mutual authentication, short-lived
tokens, replay protection, and an audit trail.

```bash
xraydo control --help
xraydo agent --help
xraydo node --help
xraydo sync --help
```

Do not expose the control endpoint to the whole internet. Restrict it with a
firewall to trusted master and node addresses.

## Diagnostics

```bash
sudo xraydo status
sudo xraydo audit list
systemctl status xraydo-xray xraydo-hysteria xraydo-telemt
journalctl -u xraydo-xray -n 100 --no-pager
systemctl status xraydo-certbot-renew.timer
```

Runtime data is stored in `/var/lib/xraydo`, configuration in `/etc/xraydo`,
and installation logs in `/var/log/xraydo`. Private keys, state, and one-time
credentials must remain accessible only to root.

## Security

- run administrative commands through `sudo`;
- never share passwords, access keys, or subscription URLs publicly;
- restrict management ports with a firewall;
- review `xraydo audit list` and test backups regularly;
- remove bootstrap files after importing their contents;
- review installer changes before running an update.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and
[docs/SUPPLY_CHAIN.md](docs/SUPPLY_CHAIN.md) for release signatures, SBOM, and
provenance verification.

## Development and tests

```bash
git clone https://github.com/blyaxyz/xraydo.git
cd xraydo
PYTHONPATH=src python3 -m unittest discover -s tests -v
bash tests/test_install.sh
```

- [Supported Debian and Ubuntu releases](docs/SUPPORT_MATRIX.md)
- [Roadmap and remaining work](TODO.md)
- [Changelog](CHANGELOG.md)
