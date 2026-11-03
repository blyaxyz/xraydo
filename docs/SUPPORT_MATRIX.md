[English](./SUPPORT_MATRIX.md) | [Русский](./SUPPORT_MATRIX.ru.md)

# Support matrix

[`src/xraydo/data/support_matrix.json`](../src/xraydo/data/support_matrix.json)
is the source of truth for CI. The workflow builds its matrix directly from
this file, preventing the documentation and CI configuration from silently
diverging.

Only official Debian and Ubuntu Server releases with systemd are supported:

- Ubuntu Server 26.10 — current interim release, supported until July 2027;
- Ubuntu Server 26.04 LTS — standard support until May 2031;
- Ubuntu Server 24.04 LTS — standard support until May 2029;
- Ubuntu Server 22.04 LTS — standard support until May 2027;
- Debian 13 — current stable release, LTS until June 30, 2030;
- Debian 12 — current Debian LTS release, supported until June 30, 2028.

AlmaLinux, Rocky Linux, Alpine, openSUSE, Debian/Ubuntu derivatives, and
systems without systemd are outside the supported matrix. The installer exits
before changing the system when `ID` or `VERSION_ID` does not match the matrix.

Every platform is tested on `linux/amd64` and `linux/arm64`. The compatibility
job verifies the operating-system identity, availability of the distribution's
Python, installer syntax, dry-run behavior, and CLI startup. The full unit-test
suite runs separately on the minimum and current Python versions, while the
installer tests run once. ARM64 uses QEMU; AMD64 does not start QEMU.

Container checks do not verify integration with systemd, reboot behavior,
SELinux, AppArmor, or a real firewall. Those checks require a dedicated VM and
remain a release gate until the project is declared production-ready.

The matrix is reviewed after every Debian stable and Ubuntu release. A version
is removed after regular support or LTS ends. Paid Ubuntu Pro/ESM and Debian
ELTS coverage do not extend the project's support matrix.

Dates are checked against the official
[Ubuntu release cycle](https://ubuntu.com/about/release-cycle) and
[Debian Releases](https://www.debian.org/releases/) pages.
