# Changelog

All notable changes are recorded here. Breaking state or configuration changes
must be called out explicitly under a `Breaking` subsection.

## Unreleased

### Changed

- Split the quick install's long 70% interval into certificate, user, per-core
  configuration/health, subscription, and link-generation stages. Long-running
  certificate and core health checks now refresh their labels with elapsed time
  or retry counts on interactive terminals. Reinstalls reuse a matching TLS
  certificate when its key matches and it remains valid for at least seven days.
- Fixed the installer completion screen so it always prints a single, structured
  subscription/QR/Xray/Hysteria/Telegram result. Reinstalling rotates and displays
  the existing user's subscription token, and selected core failures now stop the
  installation instead of producing a misleading partial-success screen. Both
  Telegram Proxy URL formats are also stored in bootstrap credentials.
- Fixed REALITY target discovery on Debian Python/OpenSSL builds that negotiate
  X25519 through the TLS 1.3 system group policy but reject explicitly setting
  `SSLContext.set_ecdh_curve("X25519")` with `UNKNOWN_GROUP`.
- Reworked the README into a Russian-first, task-oriented guide and changed the
  bootstrap examples from a release asset to the current `master` installer,
  with both curl and wget commands.
- Runtime health now includes protocol-level checks: Xray configuration plus
  inbound reachability, Hysteria's authenticated traffic API, and Telemt's
  native liveness command. Installation fails closed when a started core does
  not pass its protocol check.
- CI now derives a 28-job amd64/arm64 Linux compatibility matrix from a single
  support data file. Releases include an SPDX SBOM, SHA-256 manifest, keyless
  Sigstore bundles, and signed GitHub provenance/SBOM attestations.

### Added

- Added quick port and SNI switching to the interactive inbound menu and CLI.
  Changes use port probes, atomic apply/rollback, health ACK gating, and delayed
  subscription-link updates for local and remote Xray protocols and proxies.
- Added the `install.sh --quick` shortcut, which selects the quick profile,
  disables every installer prompt, accepts its plan, and starts installation immediately.
- Node-scoped certificate issue/list/renew/replace/revoke/delete/assignment; private
  keys are queued only for the authenticated target node.
- Unattended usage/IP collection, policy enforcement, desired-state reconciliation,
  and rollout reconciliation through `xraydo automation`.
- Per-IP and per-token subscription rate limiting, failed-token bans, and a mandatory
  CI gate for HTTPS and mTLS socket tests.
- Verified Xray install/update/check-update/history/rollback/backup management.
- WSS/TLS guided configuration with SAN checks, secure generated paths, remote TCP/UDP
  port probes, transactional REALITY shortId/SpiderX rotation, and shared canary rollouts.

- A `--telemt-only` three-question installer flow for language, exact public
  port, and Fake TLS domain. It creates/reuses `hello`, validates and starts the
  service automatically, then prints and securely stores both Telegram links.
- Let's Encrypt DNS and public-IP certificate issuance, with Certbot 5.4+
  provisioning through snap where required, HTTP webroot fallback, a twice-daily
  systemd renewal timer, deploy hook, atomic import, and dependent-service reload.
- Seven-character user IDs and additional monotonic numeric IDs accepted by
  every user reference (`12` and `#12`). Schema 10 atomically migrates legacy
  UUID references across related state while keeping existing subscription
  tokens valid.
- Adaptive terminal presentation with colored panels, breadcrumbs, wrapped
  menu entries, danger highlighting, command/result cards, and structured human
  responses. `NO_COLOR`, dumb/non-TTY output, narrow terminals, and explicit
  `XRAYDO_ASCII` fallback are supported without changing JSON output.
- First-class `xraydo system status/update/uninstall/purge` commands backed by
  a permission-validated installed lifecycle tool, including dry-run plans and
  confirmed execution directly from the base menu.
- A parser-backed “All CLI commands” browser that recursively exposes every
  leaf command, required value, and arbitrary optional flags, preventing menu
  coverage from drifting behind the CLI parser.
- Inter-process state locking, revision compare-and-swap for legacy writers,
  locked read-modify-write transactions, durable directory fsync, and automatic
  recovery from a protected `state.json.previous` copy.
- Two-phase user deletion with an impact plan, atomic user/AccessKey blocking,
  subscription revocation, per-node operation confirmation, grace period, and
  explicit credential purge only after access is confirmed blocked everywhere.
- Value-free audit coverage for user and AccessKey lifecycle/limit changes plus
  dry-run retention compaction for audit, usage, IP, inbound history, and
  completed node operations; pending desired state is never compacted.
- Secure master-to-node control plane with a dedicated node agent, one-time
  expiring bootstrap, private-CA mTLS identities bound to immutable node UUIDs,
  online certificate rotation and immediate application-layer revocation.
- Versioned, HMAC-signed and replay-protected node messages with bounded
  size/rate/time windows, capability negotiation, node-bound delivery and ACK,
  expiring leases, exponential retry, idempotent redelivery, and desired-state
  reconciliation. A real TLS integration test covers enrollment, lost ACK,
  rotation, revocation, and protocol incompatibility.
- Full English/Russian interactive navigation for users, access keys,
  subscriptions, nodes, synchronization, inbounds, REALITY, cores, TLS,
  settings, health, and audit. Menu actions reuse the existing CLI handlers;
  an advanced command entry exposes the complete xraydo parser without
  invoking a shell. Back/main/exit navigation, EOF/Ctrl+C handling, and
  confirmations for disruptive actions are included.
- Standalone Bash installer for apt, dnf, yum, zypper, and apk systems.
- Pinned, SHA-256-verified Xray-core, Hysteria 2, and Telemt artifacts.
- Quick, master, node, and advanced installation profiles.
- Strict non-executable answer files for CI and cloud-init.
- Preflight reporting for ports, RAM, disk, init system, SELinux, and AppArmor.
- Isolated service accounts and hardened systemd/OpenRC units for every core.
- Release-specific source payloads pinned by commit and embedded SHA-256.
- English and Russian CLI output, local users, and atomic state storage.
- Ordered state-schema migrations that preserve unknown user fields.
- Credential-preserving user rename history, notes, tags, and search.
- Atomic VLESS REALITY preset with target checks and Xray-native validation.
- Hysteria 2 preset with private CA/pinning, userpass, selectable obfuscation,
  loopback-only stats, verified masquerade, and runtime config validation.
- Telemt Fake TLS preset with verified SNI fallback, local authenticated API,
  atomic TOML configuration, liveness validation, and client links.
- Local systemd/OpenRC lifecycle, structured status, bounded logs, and version
  commands for Telemt and Hysteria 2.
- Validated Telemt IPv4/IPv6 listeners, IP preference, middle-proxy toggle, and
  handshake/idle/keepalive/ack timeout controls.
- Bilingual country catalog and deterministic country detection for node names.
- Local node records with immutable IDs, duplicate display-name handling,
  unique aliases, manual/automatic country selection, and flag-aware remarks.
- Structured administrative user metadata and value-free audit events for
  note, tag, contact, source, external-ID, and custom-attribute changes.
- Separate multi-device AccessKey records with immutable IDs, independent
  Xray/Hysteria/Telemt credentials, explicit selection, redacted display, and
  active/disabled/revoked lifecycle.
- AccessKey total/upload/download byte limits, IP policy field, absolute or
  duration-based validity, manual extension, and traffic-counter reset.
- Idempotent per-node usage events, cross-node/user aggregation, first-connect
  validity activation, and local traffic/expiry policy evaluation.
- Persistent per-node desired-state operations with retry, acknowledgement,
  redacted inspection, node heartbeat state, and queue summaries.
- Explicit standalone/master/node runtime roles, unified node health states,
  heartbeat failure/recovery thresholds, latency tracking, and maintenance mode.
- Cross-node active public-IP accounting for AccessKeys with configurable time
  windows, trusted CIDRs, warning/temporary-block/disable actions, and explicit
  sensitive IP inspection/clearing commands.
- Salted scrypt subscription-token verifiers, constant-time validation,
  one-time token display, rotation, revocation, and value-free audit events.
- Dynamic active-key subscription rendering in plain, standard Base64, and
  structured JSON formats, with separate MTProto links and atomic 0600 export.
- HTTPS-URL-only terminal QR output with ANSI/Unicode and ASCII modes, terminal
  width handling, private token-file input, and installer-managed qrencode.
- Strict HTTPS-only subscription serving with pre-bind certificate/SAN/key
  validation, bounded requests and concurrency, no token-bearing access log,
  no-store responses, and no plaintext HTTP fallback.
- Autonomous private-CA management with protected CA/leaf keys, multi-DNS and
  IPv4/IPv6 SAN issuance, two-thirds renewal policy, atomic version switching,
  stable CA reuse, fingerprint reporting, and public CA export.
- Installer-enabled hardened TLS renewal agents for systemd/OpenRC with
  configurable threshold, interval and jitter, plus private-CA QR trust
  warnings and platform-aware CA installation guidance.
- Atomic existing-certificate import with key permissions, SAN, date,
  certificate/key, issuer-chain and sslserver-purpose validation, including
  intermediate-chain serving and failure-safe preservation of the active leaf.
- Controlled private-CA rotation with prepare, activate, and finalize phases,
  dual-anchor trust-bundle export, an enforced overlap deadline, atomic leaf
  reissue, and retained old CA versions for recovery.
- Fail-closed Hysteria 2 leaf-certificate pin validation during subscription
  rendering, automatic `pinSHA256` URI repair from endpoint metadata, and
  normalized TLS verification metadata in structured JSON subscriptions.
- Managed leaf IP SAN reconciliation from explicit authoritative addresses or
  an IP-literal subscription URL, with dual-stack preservation, atomic reissue,
  and automatic subscription URL/QR host updates after an address change.
- Pre-activation in-memory HTTPS probes after managed leaf issuance and renewal,
  covering CA trust, every DNS/IP SAN, cert/key pairing, TLS, and encrypted HTTP
  exchange, with failure-safe active-leaf preservation and AKI/SKI extensions.
- Atomic mode-0600 encrypted TLS backups containing CA/leaf versions, keys and
  metadata, with PBKDF2/AES-256 encryption, independent HMAC authentication,
  protected passphrase files, manifests, and non-restoring integrity checks.
- Dynamic URI subscription remarks derived from the current user display name,
  so rename operations update plain/Base64/JSON output without modifying stored
  endpoints or rotating protocol credentials.
- Stable `access-key:<ID>` Xray client email/statistics identifiers with no
  fallback to mutable user names, keeping counters continuous across renames.
- Explicit subscription identity and node AccessKey payload allowlists that
  keep notes, tags, contacts, sources, external IDs, and custom administrative
  attributes local to the management state.
- A single scoped credential registry for REALITY, Hysteria 2, and Telemt, with
  fail-closed UUID/password/secret validation and removal of untracked secret
  fallbacks from protocol generators.

### Changed

- Random 256-bit subscription tokens now use a salted SHA-256 verifier instead
  of a password-oriented scrypt calculation, reducing user creation to a few
  milliseconds while retaining constant-time checks and legacy scrypt support.
- State schema is now version `10`. Existing schema `0` through `9` files
  migrate automatically. Legacy Xray UUIDs and endpoint credentials move to a
  default AccessKey; user UUIDs become seven-character IDs with numeric aliases;
  persisted sync operations gain immutable owner IDs; settings and unknown
  extension fields are preserved.
- AccessKey synchronization uses explicit immutable `owner_id`/`resource_id`
  routing and an allowlisted payload that cannot acquire user display or
  administrative fields accidentally.

### Fixed

- Reinstalling no longer shifts an explicitly selected Telemt port when the
  existing managed service owns it. Subscription allocation excludes planned
  core ports, active units are restarted after unit changes, and the installer
  requires a successful local HTTPS `/healthz` probe before showing links.
- Telemt-only reconciliation now disables a stale standard subscription URL
  and service, preventing obsolete HTTPS subscription links from being
  confused with the Telegram `tg://proxy` and `https://t.me/proxy` links.
- The standalone installer no longer requests the nonexistent Xray-core
  `v26.10.29` release. It pins the published `v26.10.31` amd64/arm64 artifacts,
  verifies their official SHA-256 digests, reports the failed URL clearly, and
  CI now checks every pinned source/core download for availability.
- REALITY creation now consistently defaults both target and SNI to `ya.ru` in
  the installer, direct CLI, menu, and internal API. Candidate latency ranking
  can no longer silently select Amazon when no target was supplied.
- The system state root now remains traversable/readable as a directory while
  `state.json`, TLS keys, and other sensitive contents keep their restrictive
  modes. This lets isolated Xray, Hysteria, and Telemt service accounts reach
  their own working directories; Telemt can also perform its required
  symlink-safe parent-chain validation instead of entering a restart loop.
- Core health output now preserves non-active systemd states such as
  `activating` and `failed` instead of reporting every non-zero `is-active`
  result as merely `stopped`.
- The standalone installer now pins the source archive that actually matches
  its generated services, treats release data JSON as optional for older
  payloads, and smoke-tests both status and the TLS renewer before enabling it.
- Hysteria version reporting now extracts the `Version:` field instead of
  returning the first line of its ASCII banner.
