# xraydo

`own-xray` is an early, terminal-only management panel for Xray services. Its
single entry point is `xraydo`. The current `0.1.0` milestone provides the safe
local foundation: English/Russian output, an interactive menu, user lifecycle
basics, atomic state storage, and a standalone Linux installer. The installer
downloads checksum-pinned Xray-core, Hysteria 2, and Telemt binaries for amd64
or arm64 and opens the `xraydo` menu after a successful interactive install.

## Quick start / Быстрый старт

Development / разработка:

```sh
cd own-xray
PYTHONPATH=src XRAYDO_DATA_DIR=/tmp/xraydo-dev python3 -m xraydo.cli --help
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Linux installation / установка на Linux:

```sh
# Download exactly one versioned file / Скачайте один версионированный файл
curl --fail --location --output install.sh \
  https://github.com/blyaxyz/xraydo/releases/download/v0.2.0/install.sh
chmod +x install.sh
./install.sh
```

The standalone installer supports apt, dnf, yum, zypper, and apk. It installs
missing Bash, CA certificates, curl, Python 3, tar, and checksum tooling using
the detected package manager. It then downloads a commit-pinned xraydo archive,
verifies its embedded SHA-256, installs it, and runs a smoke health check. It
also installs verified Xray-core, Hysteria 2, and Telemt release artifacts. It
never executes a remote script through a pipe.

Самодостаточный установщик поддерживает apt, dnf, yum, zypper и apk. Он ставит
недостающие Bash, CA-сертификаты, curl, Python 3, tar и утилиту SHA-256, затем
загружает архив закреплённого commit, проверяет встроенную контрольную сумму,
устанавливает xraydo и запускает smoke-проверку. Удалённые скрипты через pipe не
исполняются.

For automation / для автоматизации:

```sh
./install.sh --dry-run --non-interactive
sudo ./install.sh --non-interactive --yes --lang en
sudo ./install.sh --non-interactive --yes --answers answers.example
```

Profiles are `quick`, `master`, `node`, and `advanced`; roles are `standalone`,
`master`, and `node`. Non-interactive answer files use a strict `KEY=VALUE`
format and reject unknown keys instead of executing the file as shell code.

On the default `/usr/local` installation, each selected core receives its own
locked system account, configuration/data/log directories, and hardened
`xraydo-<core>` systemd or OpenRC unit. Units remain disabled until a validated
configuration exists. The installer never opens firewall ports automatically.

Main commands / основные команды:

```sh
xraydo                       # interactive menu; requires a TTY
xraydo status
xraydo user create alice
xraydo newUser alice         # compatibility alias
xraydo user list
xraydo user show alice
xraydo user set-enabled alice off
xraydo user rename alice bob
xraydo user set-note bob "priority customer"
xraydo user set-tags bob "team-a,paid"
xraydo user set-alias bob customer-01
xraydo user set-admin bob --contact bob@example.com --source partner \
  --external-id crm-42 --attribute plan=premium --actor operator
xraydo user search team-a
xraydo subscription status bob
xraydo subscription create bob   # for a revoked/missing subscription
xraydo subscription update bob   # safe endpoint-count summary
xraydo subscription show bob --format base64
xraydo subscription export bob --format base64 --output ./bob.txt
xraydo settings subscription-url https://subscriptions.example.com
chmod 600 ./bob-token
xraydo subscription qr bob --token-file ./bob-token
xraydo subscription qr bob --token-file - --ascii  # token from stdin
xraydo subscription serve --https-only \
  --listen 0.0.0.0 --port 8444 \
  --public-host subscriptions.example.com \
  --cert /etc/xraydo/subscription/server.crt \
  --key /etc/xraydo/subscription/server.key
xraydo tls init-ca
xraydo tls issue-self --domain subscriptions.example.com \
  --ip 203.0.113.10 --ip 2001:db8::10 --days 90 --renew-percent 67
xraydo tls status
xraydo tls renew
xraydo tls renew --force
xraydo tls export-ca --output ./xraydo-ca.crt
xraydo tls import \
  --cert /path/server.crt --key /path/server.key \
  --chain /path/intermediates.crt --ca /path/trust-anchor.crt \
  --domain subscriptions.example.com --ip 203.0.113.10
xraydo subscription rotate bob   # new token is shown once
xraydo subscription revoke bob
xraydo key create --user bob --device phone
xraydo key list --user bob
xraydo key show <keyID>                  # credentials redacted
xraydo key show <keyID> --show-secrets   # explicit secret display
xraydo key disable <keyID> --reason manual
xraydo key enable <keyID>
xraydo key revoke <keyID>
xraydo key set-limit <keyID> --traffic 100GB --upload 20GB \
  --download 80GB --duration 30d --starts immediate --ips 2
xraydo key set-limit <keyID> --expires-at 2030-01-01T00:00:00Z
xraydo key set-limit <keyID> --ips 2 --ip-window 5m \
  --ip-action temporary_block --trusted-network 10.0.0.0/8
xraydo key ips <keyID>       # explicit sensitive IP display
xraydo key clear-ips <keyID>
xraydo key extend <keyID> --duration 7d
xraydo key reset-traffic <keyID>
xraydo key record-usage <keyID> --source <nodeID> --event-id <uniqueID> \
  --upload-bytes 1024 --download-bytes 2048 --connected
xraydo key usage --key <keyID>
xraydo key usage --user bob
xraydo --json audit list --limit 100
xraydo settings language ru
xraydo --lang en status      # one-command override
xraydo --json user list
xraydo country detect "DE Frankfurt"
xraydo node add "Germany Frankfurt" --alias de-1
xraydo node rename de-1 "Turkey Istanbul" --country TR
xraydo node set-country de-1 auto
xraydo node set-remark de-1 "Premium Istanbul"
xraydo role show
xraydo role set master
xraydo node heartbeat de-1 --state healthy --latency-ms 12
xraydo node missed-heartbeat de-1
xraydo node maintenance de-1 on
xraydo node health-policy --failure-threshold 3 --recovery-threshold 2
xraydo node status de-1
xraydo node set-enabled de-1 off
xraydo sync list --node de-1
xraydo --json sync claim --node de-1 --include-payload  # node agent only
xraydo sync ack <operationID>
xraydo sync fail <operationID> --error "temporary outage"
xraydo node remove de-1 --yes
xraydo settings country-flags off
xraydo core reality --user bob --access-key <keyID> --target ya.ru --listen-port 443 --fingerprint firefox
xraydo core hysteria --user bob --server 203.0.113.10 --port 1935
xraydo core telemt --user bob --server 203.0.113.10 --port 8443
xraydo telemt status
xraydo telemt logs --lines 100
sudo xraydo telemt restart
xraydo hysteria status
sudo xraydo hysteria restart
```

The REALITY command resolves and blocks non-public targets, requires a trusted
TLS 1.3 + HTTP/2 handshake for the selected SNI, asks Xray itself to generate
X25519 keys, and validates the generated JSON with `xray run -test` before an
atomic replacement. Private keys are never returned in command output.
`firefox` is the default uTLS ClientHello profile; it is a compatibility profile,
not a randomly generated cryptographic fingerprint. Explicit randomizer profiles
remain available through `--fingerprint`.

The Hysteria command checks UDP and loopback API ports, creates an independent
userpass/obfuscation/stats secret set, verifies an HTTPS masquerade target, and
uses either a matching `--cert`/`--key` pair or a locally generated private CA.
The returned URI uses Hysteria's `pinSHA256` leaf-certificate pinning and never
enables `insecure`. Subscription rendering validates that pin against stored
endpoint metadata, restores it to a URI if needed, and exposes normalized
`tls.verification=leaf-sha256` metadata in structured JSON. A mismatch fails
closed instead of publishing a weaker connection string. Plain HTTPS
subscription URLs have no portable pin parameter; private-CA trust is therefore
distributed through the CA fingerprint and exported trust bundle.

The Telemt command enables only Fake TLS (`ee`), verifies the masking domain,
creates an independent 16-byte user secret, binds its authenticated Control API
to loopback, and runs Telemt's own liveness healthcheck before installing the
TOML configuration or showing `tg://proxy` and `https://t.me/proxy` links.

The local `telemt` and `hysteria` command groups control the hardened units
created by the installer on systemd and OpenRC hosts. Status and version output
support `--json`; service mutations require the administrator to invoke
`xraydo` with root privileges explicitly. Journal output is bounded by
`--lines` (1–10000).

The subscription token returned during user creation or rotation is shown once.
Only a salted scrypt verifier is stored; legacy SHA-256 verifiers migrate
without exposing or resetting their tokens. State is written atomically under `/var/lib/xraydo`
for root or `$XDG_STATE_HOME/xraydo` for an unprivileged administrator.
Administrative contact/source/external-ID/attribute fields stay inside the
local user record. Audit events record the actor, timestamp, object ID, action,
and changed field names, but never the field values or subscription token.
AccessKey list/show output redacts both protocol credentials and secret-bearing
endpoint fields unless `--show-secrets` is explicitly requested. Size limits
use binary multipliers for `B`, `KB`, `MB`, `GB`, and `TB`; durations accept
`s`, `m`, `h`, `d`, or `w`. Client IP observations are never included in
normal key/usage output; `key ips` is the explicit sensitive-data command.
Subscription content is assembled dynamically from active AccessKeys. Standard
URI entries are available as plain text or standard Base64; Telemt links remain
separate in JSON output. Export is atomic and always writes mode `0600`.
Terminal QR codes contain only the configured HTTPS subscription URL. Tokens
for repeated display are read from a mode-0600 file or stdin, never recovered
from state or passed to `qrencode` as a process argument. `NO_COLOR` and
`--ascii` select the plain fallback; wide terminals place the copyable URL next
to the QR code.

The built-in subscription listener is TLS-only and has no HTTP fallback. It
requires `--https-only`, validates certificate dates, SAN, certificate/key
pairing, and private-key permissions before binding. The intentionally narrow
HTTP implementation accepts only bounded `GET /sub/<token>` requests, limits
concurrency, disables access logging, sends `Cache-Control: no-store`, and
closes every connection after its response. Run it under the service manager;
automatic certificate issuance is a separate unfinished milestone.

The autonomous TLS manager creates its private CA once, stores CA and leaf keys
in separate mode-0700 version directories, and exposes stable `current` paths
through an atomic symlink switch. Leaf certificates support multiple DNS,
IPv4, and IPv6 SAN values. `tls renew` keeps the existing CA and replaces the
leaf only after two thirds of its configured lifetime; `--force` overrides the
threshold. The CA SHA-256 fingerprint is printed by `tls init-ca/status`, and
only the public CA certificate can be exported. The exporter prints a client
trust-store installation command. QR output explicitly warns about private-CA
trust and includes the CA fingerprint.

On systemd and OpenRC installations, `install.sh` enables the hardened
`xraydo-tls-renew` agent. It checks hourly with five minutes of jitter, waits
without failing until a managed leaf exists, and keeps the last working leaf if
renewal fails. The interval, jitter, certificate lifetime, and renewal percent
are configurable through `tls renew-daemon` and `tls issue-self`.

`tls import` validates private-key permissions, certificate/key pairing,
validity dates, every requested DNS/IP SAN, issuer chain, and `sslserver`
purpose before writing anything. A supplied intermediate chain is appended to
the served PEM. Only after all copied files pass validation does the atomic
`current` switch occur, so a failed import leaves the working certificate
untouched. Imported certificates are intentionally excluded from private-CA
automatic renewal.

Private-CA rotation is deliberately split into three operator-controlled
phases:

```sh
xraydo tls rotate-ca prepare --overlap-days 30
xraydo tls export-ca --output ./xraydo-ca-bundle.crt
# Install the two-CA bundle on every client before continuing.
xraydo tls rotate-ca activate
# After the overlap deadline and client verification:
xraydo tls rotate-ca finalize
```

`prepare` creates the new CA but keeps the old CA and leaf active. During the
prepared and active phases, `export-ca` exports a bundle containing both trust
anchors. `activate` atomically selects the new CA and issues a new leaf with the
same SAN and lifetime policy. `finalize` refuses to remove the old anchor from
the exported bundle until the configured overlap period ends; `--force` is an
explicit emergency override. Old version directories are retained for manual
recovery and are never deleted by rotation.

## Architecture / Архитектура

The intended architecture has three roles: `standalone`, `master`, and `node`.
The master owns global user identities and desired state; node agents will apply
versioned changes and report health over a mutually authenticated channel. The
present milestone implements the local application/service layer plus a
persistent desired-state queue with claim, retry, and acknowledgement. Network
transport and node authentication are not implemented yet; queue commands must
not be exposed as a remote API or assumed secure.

Планируемые роли: `standalone`, `master` и `node`. Master хранит глобальные
идентификаторы пользователей и желаемое состояние; агенты нод будут применять
версионированные изменения и возвращать health-данные по взаимно
аутентифицированному каналу. Сейчас реализован только локальный слой приложения.

## Migration / Миграция

The state file contains `schema_version`. Schema 8 separates access keys and
their protocol credentials from users. Migration from schema 0 through 4 is atomic,
preserves old Xray UUIDs and configured endpoint credentials, and adds
idempotent per-key/per-source usage events plus a persistent per-node
desired-state delivery queue. Unknown fields are retained. Back up
`state.json` before upgrading.
Version `0.1.0` supports schema `8`; no migration from 3x-ui is available yet.
Never copy production secrets into the repository.

Файл состояния содержит `schema_version`. Перед обновлением сохраните резервную
копию `state.json`. Версия `0.1.0` поддерживает схему `8`; импорт из 3x-ui пока
не реализован. Не помещайте production-секреты в репозиторий.

See [TODO.md](TODO.md) for the full product requirements.
