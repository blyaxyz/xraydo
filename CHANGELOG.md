# Changelog

All notable changes are recorded here. Breaking state or configuration changes
must be called out explicitly under a `Breaking` subsection.

## Unreleased

### Added

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

### Changed

- State schema is now version `8`. Existing schema `0` through `7` files
  migrate automatically. Legacy Xray UUIDs and endpoint credentials move to a
  default AccessKey; users, settings, and unknown extension fields are preserved.
