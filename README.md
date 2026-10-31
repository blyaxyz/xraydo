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

For a short Telemt-only installation, use:

```sh
sudo ./install.sh --telemt-only
```

This mode asks only for the interface language, Telemt port, and Fake TLS
domain. It automatically detects the public address, creates or reuses the
`hello` user, validates the generated configuration with Telemt, installs and
enables `xraydo-telemt`, and prints both `tg://proxy` and
`https://t.me/proxy` connection links. A mode-0600 copy is stored under
`/var/lib/xraydo`. The selected port is never silently replaced; an unrelated
listener produces an explicit error.

Для короткой установки только Telemt выполните:

```sh
sudo ./install.sh --telemt-only
```

Этот режим спрашивает только язык, порт Telemt и домен Fake TLS. Публичный адрес,
пользователь `hello`, проверка конфигурации, systemd/OpenRC-служба и автозапуск
настраиваются автоматически. В конце установщик показывает ссылки `tg://proxy`
и `https://t.me/proxy`, а их копию сохраняет в root-only файле с правами `0600`.
Занятый посторонним процессом порт вызывает понятную ошибку и не заменяется
другим без ведома пользователя.

The same interactive menu provides two removal modes. `Uninstall` stops and
removes xraydo services, programs, and managed core binaries while preserving
configuration and state. `Purge` additionally removes users, credentials,
subscription state, TLS/private keys, logs, and dedicated system accounts; it
shows the complete target list and requires a separate exact confirmation.

Это же меню предлагает два режима удаления. `Uninstall` останавливает и удаляет
сервисы, программу и управляемые ядра, сохраняя конфигурацию и state. `Purge`
дополнительно удаляет пользователей, credentials, подписки, TLS/private keys,
логи и отдельные системные учётные записи после отдельного точного подтверждения.
Общие системные пакеты не удаляются автоматически, поскольку они могут
использоваться другими программами.

Для автоматизации доступны эквивалентные команды:

```sh
xraydo system update --dry-run
sudo xraydo system update --yes
sudo xraydo system uninstall --yes       # preserves config and state
xraydo system purge --dry-run
sudo xraydo system purge --yes           # permanently removes all data
```

The installed release keeps a root-owned, non-group/world-writable lifecycle
installer under `lib/xraydo`. `xraydo system` validates its type, owner,
permissions, and executable bit before invoking it without a shell. The
standalone script remains the bootstrap entry point, but routine lifecycle
operations no longer require locating `install.sh` manually.

Profiles are `quick`, `master`, `node`, and `advanced`; roles are `standalone`,
`master`, and `node`. Non-interactive answer files use a strict `KEY=VALUE`
format and reject unknown keys instead of executing the file as shell code.

The wizard can create one first global user with independent Xray, Hysteria 2,
Telemt, and subscription credentials, or skip user creation. It validates the
installed cores before generating credentials, applies the selected quota
profile, configures only verified endpoints, and shows the subscription and
MTProto links plus terminal QR codes outside the installation log. Without a
TTY, one-time credentials are written to a root-only bootstrap file whose path
is printed; move it securely and delete it after import.

Мастер может создать одного глобального пользователя с независимыми данными
доступа Xray, Hysteria 2, Telemt и подписки либо пропустить этот шаг. Сначала он
проверяет установленные ядра, затем применяет профиль лимитов и публикует только
проверенные endpoint. URL и QR показываются вне журнала установки; без TTY они
записываются в одноразовый root-only файл, который нужно безопасно перенести и
удалить после импорта.

On the default `/usr/local` installation, each selected core receives its own
locked system account, configuration/data/log directories, and hardened
`xraydo-<core>` systemd or OpenRC unit. Every selected unit is immediately
enabled for automatic startup. It starts only after a validated configuration
exists, is health-checked when configured, and resumes automatically after a
server reboot. Unselected cores have no unit. The installer never opens firewall
ports automatically.

При установке в `/usr/local` каждое выбранное ядро получает отдельный
`xraydo-<core>` unit. Он сразу добавляется в автозагрузку systemd/OpenRC,
запускается после появления проверенной конфигурации и автоматически
восстанавливается после перезагрузки сервера.

Main commands / основные команды:

```sh
xraydo                       # interactive menu; requires a TTY
xraydo status
xraydo system status
xraydo system update --dry-run
sudo xraydo system update --yes
sudo xraydo system uninstall --yes
sudo xraydo system purge --yes
xraydo user create alice
xraydo newUser alice         # compatibility alias
xraydo user list
xraydo user show alice
xraydo user show 1           # monotonic numeric ID; #1 is also accepted
xraydo user show 7K9M2QP     # stable seven-character ID, case-insensitive
xraydo user set-enabled alice off
xraydo user rename alice bob
xraydo user delete-plan bob --grace 7d
xraydo user delete bob --grace 7d --yes
xraydo user delete-status bob
# purge succeeds only after every active node ACKs blocking and grace ends:
xraydo user purge bob --yes
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
xraydo tls sync-ip --ip 203.0.113.20 --ip 2001:db8::20
xraydo tls export-ca --output ./xraydo-ca.crt
chmod 600 ./tls-backup-passphrase
xraydo tls backup --output ./xraydo-tls.backup \
  --passphrase-file ./tls-backup-passphrase
xraydo tls verify-backup --input ./xraydo-tls.backup \
  --passphrase-file ./tls-backup-passphrase
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
xraydo state compact --dry-run
xraydo state compact --audit-days 365 --usage-days 90 \
  --ip-days 7 --inbound-days 180 --operation-days 30
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
xraydo --json sync claim --node de-1 --include-payload  # local admin/debug only
xraydo sync ack <operationID>
xraydo sync fail <operationID> --error "temporary outage"
xraydo control init --public-host master.example.com
xraydo control export-ca --output ./control-ca.crt
xraydo control bootstrap --node de-1 --ttl 10m --token-file ./de-1.bootstrap
xraydo control serve --listen 0.0.0.0 --port 8445
# On the node; transfer only control-ca.crt and the one-time 0600 token:
xraydo agent enroll --master https://master.example.com:8445 --node <nodeUUID> \
  --token-file ./de-1.bootstrap --ca ./control-ca.crt --dir /var/lib/xraydo-agent
xraydo agent run --config /var/lib/xraydo-agent/agent.json
xraydo agent rotate --config /var/lib/xraydo-agent/agent.json
xraydo control revoke --node de-1 --yes
xraydo node remove de-1 --yes
xraydo settings country-flags off
xraydo core reality --user bob --access-key <keyID> --target ya.ru --listen-port 443 --fingerprint firefox
xraydo core hysteria --user bob --server 203.0.113.10 --port 1935
xraydo hysteria install --node all
xraydo hysteria check-update --node de-1
xraydo hysteria capabilities --node de-1
xraydo hysteria diagnostics --node de-1
xraydo hysteria collect-stats --node local --secret '<trafficStats secret>'
xraydo hysteria revoke-key <accessKeyID> --node all
xraydo core telemt --user bob --server 203.0.113.10 --port 8443
xraydo telemt build --version 3.5.14 --profile economy
xraydo telemt install --node all --artifact <artifactID>
xraydo telemt check-update --node all
xraydo telemt user add bob --node all --mode tls
xraydo telemt link bob
xraydo telemt collect-stats --api-auth 'Bearer <secret>'
xraydo telemt diagnostics --api-listen 127.0.0.1:9091 --api-auth 'Bearer <secret>'
xraydo telemt status
xraydo telemt logs --lines 100
sudo xraydo telemt restart
xraydo hysteria status
sudo xraydo hysteria restart
```

Running `xraydo` without arguments opens the bilingual interactive menu. Its
sections cover users, access keys and subscriptions, nodes and synchronization,
inbounds and REALITY, core services, TLS, settings, health, and audit. Every
menu action calls the same application path as the corresponding direct CLI
command. `b`, `m`, and `q` navigate back, to the main menu, or exit; EOF and
Ctrl+C exit cleanly. Update, uninstall, and full purge have a dedicated system
menu with dry-run plans. A parser-backed “All CLI commands” browser recursively
exposes every argparse command and required value, so newly added CLI features
cannot silently disappear from the menu. The advanced-command item still
accepts a complete xraydo command line without invoking a shell.

Interactive output uses a compact terminal theme with section panels,
breadcrumbs, numbered actions, highlighted destructive operations, command
cards, and clear success/error results. Colors are emitted only to a capable
TTY. Set `NO_COLOR=1` to disable ANSI colors or `XRAYDO_ASCII=1` to replace box
drawing and symbols; redirected output and `--json` remain unchanged. Panels
and long menu labels wrap to narrow SSH terminals.

Запуск `xraydo` без аргументов открывает двуязычное интерактивное меню для
пользователей, ключей и подписок, нод, inbound, ядер, TLS, health и настроек.
Клавиши `b`, `m` и `q` возвращают назад, в главное меню или завершают работу.
Обновление, uninstall и полный purge доступны в отдельном системном разделе с
dry-run. Раздел «Все команды CLI» автоматически строится из argparse и поэтому
покрывает весь функционал. Расширенный пункт принимает обычную команду xraydo,
не запуская shell.

Интерактивный вывод использует панели, breadcrumbs, выделение опасных действий
и понятные статусы результата. `NO_COLOR=1` отключает ANSI-цвета,
`XRAYDO_ASCII=1` включает ASCII-рамки; JSON и перенаправленный вывод не меняются.

The REALITY command resolves and blocks non-public targets, requires a trusted
TLS 1.3 + HTTP/2 handshake for the selected SNI, asks Xray itself to generate
X25519 keys, and validates the generated JSON with `xray run -test` before an
atomic replacement. Private keys are never returned in command output.
The Xray client `email` field is an internal statistics key formatted as
`access-key:<immutable AccessKey ID>`; it never uses the mutable display name.
Human-readable names exist only in dynamically rendered subscription remarks,
so renaming a user neither splits traffic counters nor changes credentials.
`firefox` is the default uTLS ClientHello profile; it is a compatibility profile,
not a randomly generated cryptographic fingerprint. Explicit randomizer profiles
remain available through `--fingerprint`.

The Hysteria command checks UDP and loopback API ports, creates independent
AccessKey userpass credentials and cryptographically random obfuscation/stats
secrets, verifies an HTTPS masquerade target, and uses a matching
`--cert`/`--key` pair, the common certificate manager (`--tls-managed`), ACME,
or a locally generated private CA. QUIC bandwidth, BBR/Reno congestion control,
UDP policy, IPv4/IPv6 listeners and ECH can be configured explicitly.
The returned URI uses Hysteria's `pinSHA256` leaf-certificate pinning and never
enables `insecure` by default; `--insecure` is an explicit unsafe override and
returns a warning. Subscription rendering validates that pin against stored
endpoint metadata, restores it to a URI if needed, and exposes normalized
`tls.verification=leaf-sha256` metadata in structured JSON. A mismatch fails
closed instead of publishing a weaker connection string. Plain HTTPS
subscription URLs have no portable pin parameter; private-CA trust is therefore
distributed through the CA fingerprint and exported trust bundle.

`xraydo hysteria install/update/rollback` manages the official standalone core
per node. The target node resolves its own Linux architecture, downloads only
from the official HyNetworks release, verifies the published SHA-256 digest and
reports the installed version/capabilities back with its synchronization ACK.
Hysteria endpoints are published into the common subscription only after that
node ACK succeeds. Traffic statistics are loopback-only: `/online` counts client
instances rather than proxy streams, aggregate traffic does not reliably expose
client IP/UDP attribution, and a kick must be paired with auth revocation to
prevent reconnects.

The Telemt command enables only Fake TLS (`ee`), verifies the masking domain,
creates an independent 16-byte user secret, binds its authenticated Control API
to loopback, and runs Telemt's own liveness healthcheck before installing the
TOML configuration or showing `tg://proxy` and `https://t.me/proxy` links.
It supports multiple `[access.users]`, Classic/`dd`/`ee` links, and an explicit
safe masking backend through `--mask-host`, `--mask-port` and
`--unknown-sni-action mask`. User API mutations are preferred for hot admission
updates; atomic config replacement plus a controlled restart is retained as the
fallback for fields Telemt cannot reliably reload.

The Telemt artifact manager can install a SHA-256-verified official release or
build one artifact on the controller/build host from an exact tag or commit.
Source builds require Rust 1.88+, use the repository `Cargo.lock` with
`cargo build --locked`, and record commit, target, profile, toolchain, lockfile
digest and binary digest. The `economy` profile uses one Cargo job and disables
LTO to lower peak build memory. Worker nodes never compile by default: a cached,
verified artifact is distributed sequentially and each node must verify its
SHA-256 before reporting success. Version history supports rolling updates and
checksum-verified rollback.

Telegram links remain in the separate `mtproto` subscription collection and are
published only after node synchronization succeeds. Telemt API polling uses a
per-node cumulative cursor so `total_octets` is not counted twice; the API does
not reliably split that value into upload/download, and connection/IP precision
depends on the runtime telemetry features enabled in Telemt.

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
Client subscription rendering crosses an explicit `{user_id, remark}` identity
boundary, while node synchronization uses an allowlist of AccessKey runtime
fields. Notes, tags, contacts, source, external IDs, custom attributes, and
their values are therefore excluded from both subscriptions and node payloads.
AccessKey list/show output redacts both protocol credentials and secret-bearing
endpoint fields unless `--show-secrets` is explicitly requested. Size limits
use binary multipliers for `B`, `KB`, `MB`, `GB`, and `TB`; durations accept
`s`, `m`, `h`, `d`, or `w`. Client IP observations are never included in
normal key/usage output; `key ips` is the explicit sensitive-data command.

Protocol credential ownership is explicit and fail-closed:

| Protocol | AccessKey credential | Client endpoint material | Node-only material |
| --- | --- | --- | --- |
| VLESS REALITY | `xray_uuid` | public key, short ID, spider path, fingerprint | REALITY private key |
| Hysteria 2 | `hysteria_password` | username, obfs password, certificate pin, URI | stats secret, TLS private key |
| Telemt | `telemt_secret` (16 bytes) | username, derived connection secret, client links | Control API authorization |

Generators validate the required UUID/password/hex secret and stop if it is
missing or malformed. They never silently create an untracked replacement.
Subscription content is assembled dynamically from active AccessKeys. Standard
URI entries are available as plain text or standard Base64; Telemt links remain
separate in JSON output. Export is atomic and always writes mode `0600`.
Every rendered URI receives a percent-encoded fragment remark from the user's
current display name. Renaming a user therefore updates plain, Base64, and JSON
subscriptions immediately without rewriting stored endpoints, rotating tokens,
or changing any protocol credential. Structured entries expose the same value
as `remark`.
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
closes every connection after its response. Run it under the service manager.

The installer can obtain a Let's Encrypt certificate for either a DNS name or
a public IP address. Public-IP issuance requires Certbot 5.4 or newer and uses
Let's Encrypt's short-lived profile; on Debian 13 the installer therefore uses
the official Certbot snap instead of the older distribution package. HTTP-01
must be reachable on port 80. When another web server already owns that port,
the installer uses `/var/www/html` as its webroot instead of stopping it.
`xraydo-certbot-renew.timer` checks twice daily, and a Certbot deploy hook
atomically imports each renewed certificate before dependent services restart.
The same operations are available explicitly through `tls acme-issue`,
`tls acme-renew`, and `tls acme-deploy`.

The autonomous TLS manager creates its private CA once, stores CA and leaf keys
in separate mode-0700 version directories, and exposes stable `current` paths
through an atomic symlink switch. Leaf certificates support multiple DNS,
IPv4, and IPv6 SAN values. `tls renew` keeps the existing CA and replaces the
leaf only after two thirds of its configured lifetime; `--force` overrides the
threshold. The CA SHA-256 fingerprint is printed by `tls init-ca/status`, and
only the public CA certificate can be exported. The exporter prints a client
trust-store installation command. QR output explicitly warns about private-CA
trust and includes the CA fingerprint.

Before a newly issued or renewed managed leaf becomes `current`, xraydo runs a
socket-free local HTTPS probe through TLS memory BIOs for every DNS/IP SAN. The
probe verifies the private-CA chain, hostname matching, cert/key pairing, TLS
handshake, and encrypted HTTP request/response exchange. Probe time and hosts
are stored in certificate metadata. A failed probe removes the candidate
version and leaves the active leaf untouched. Issued leaf certificates include
both Subject Key Identifier and Authority Key Identifier for strict OpenSSL
chain validation.

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

`tls sync-ip` compares the managed leaf IP SAN values with an authoritative
address list and atomically reissues the leaf only when they differ. With no
`--ip`, it uses an IP-literal host from the configured HTTPS subscription URL;
it never contacts a third-party IP-discovery service. URL-derived IPv4 replaces
only IPv4 SAN values (and likewise for IPv6), preserving the other half of a
dual-stack setup. When explicit addresses replace the IP used by the current
subscription URL, its host is updated while its scheme, port, and path remain
unchanged. QR output reads that URL dynamically and therefore follows the new
address without token rotation.

`tls backup` is the only TLS export that includes private keys. It packages all
CA/leaf certificate versions, symlinks, modes, certificate metadata, and a
SHA-256 manifest, then encrypts the tar payload with OpenSSL AES-256-CBC and
PBKDF2-SHA256 (600,000 iterations). A separately derived HMAC-SHA256 covers the
complete encrypted envelope, so a wrong password or modified byte is rejected
before decryption. The backup is written atomically with mode `0600`, must live
outside the active PKI directory, and accepts its 12+ byte password only from a
mode-0600 file. `tls verify-backup` authenticates, decrypts, and reads the
manifest without restoring files. Ordinary `tls export-ca` remains a
certificate-only public export and never includes `ca.key` or `server.key`.

## Architecture / Архитектура

The architecture has three roles: `standalone`, `master`, and `node`. The master
owns global identities and desired state. A separate node agent polls the
versioned HTTPS control protocol, materializes supported desired-state
operations idempotently, reconciles drift, and reports health. Bootstrap tokens
are single-use and expire within one hour. Every later message requires a
private-CA client certificate bound to the immutable node UUID plus a timestamp,
nonce, and HMAC; the master enforces replay windows, message/rate limits,
capabilities, operation ownership, leases, and idempotent ACKs. Certificates can
be rotated online or revoked immediately. `sync claim/ack/fail` remain local
administrative/debug commands and are not the network API.

Роли: `standalone`, `master` и `node`. Master хранит глобальные идентификаторы и
desired state. Отдельный node-agent получает поддерживаемые операции по HTTPS,
применяет их идемпотентно, сверяет desired/actual state и отправляет health.
Одноразовый bootstrap ограничен временем; далее обязательны mTLS-сертификат,
привязанный к UUID ноды, timestamp, nonce и HMAC. Master проверяет replay,
размер/частоту сообщений, capability, владельца операции, lease и повторный
ACK. Сертификат можно ротировать или немедленно отозвать. Команды `sync` остаются
только локальным административным интерфейсом.

Every state read/write is guarded by an inter-process lock. New multi-step
operations use a locked read-modify-write transaction; legacy callers also use
revision compare-and-swap, so a concurrent update fails without overwriting
newer data. Writes remain atomic and durable, and `state.json.previous` (mode
`0600`) is restored automatically if the primary JSON is damaged. Disabling a
user atomically disables every non-revoked AccessKey and queues the change for
all enabled nodes. Deletion is a two-phase lifecycle: request blocking, wait for
per-node ACK plus the grace deadline, then explicitly purge credentials. Audit
events contain actor/action/object and changed field names only. `state compact`
provides dry-run and bounded retention while never deleting pending operations.

Каждое чтение и изменение state защищено межпроцессным lock. Многошаговые
операции используют транзакцию read-modify-write, а старые вызовы защищены
revision/CAS и не могут молча затереть более новую запись. При повреждении JSON
автоматически восстанавливается `state.json.previous` с правами `0600`.
Отключение пользователя одной транзакцией блокирует все AccessKey и создаёт
операции для активных нод. Удаление состоит из блокировки, ACK каждой ноды,
grace period и отдельного purge credentials. Audit хранит только имена
изменённых полей, без значений секретов и IP. `state compact` поддерживает
dry-run и никогда не удаляет незавершённые операции.

## Migration / Миграция

The state file contains `schema_version`. Schema 10 uses a stable, unambiguous
seven-character primary user ID plus a monotonic positive `numeric_id`. Every
user-taking command accepts the name, alias, short ID, `12`, or `#12`. Numeric
IDs are never reused after deletion. Migration replaces legacy user UUIDs in
AccessKeys, subscriptions, sync operations, deletion state, and audit links;
previously issued subscription tokens remain valid. Schema 9 separates access
keys and their protocol credentials from users. Migration from schema 0 through 9 is atomic,
preserves old Xray UUIDs and configured endpoint credentials, and adds
idempotent per-key/per-source usage events plus a persistent per-node
desired-state delivery queue. Sync operations use immutable `owner_id` and
`resource_id`; display names are never routing keys or part of the AccessKey
payload. Unknown fields are retained. Back up
`state.json` before upgrading.
Version `0.1.0` supports schema `10`; no migration from 3x-ui is available yet.
Never copy production secrets into the repository.

Файл состояния содержит `schema_version`. Схема `10` заменяет UUID пользователя
на стабильный семисимвольный ID и добавляет монотонный цифровой `numeric_id`.
Во всех командах можно использовать имя, alias, короткий ID, `12` или `#12`;
освободившиеся номера не переиспользуются. Миграция обновляет все внутренние
ссылки и сохраняет работоспособность ранее выданных subscription-токенов.
Перед обновлением сохраните резервную копию `state.json`. Импорт из 3x-ui пока
не реализован. Не помещайте production-секреты в репозиторий.

See [TODO.md](TODO.md) for the full product requirements.
