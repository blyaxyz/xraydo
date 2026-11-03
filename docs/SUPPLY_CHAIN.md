[English](./SUPPLY_CHAIN.md) | [Русский](./SUPPLY_CHAIN.ru.md)

# Verifying an xraydo release

Every GitHub Release contains `install.sh`, a source archive, `SHA256SUMS`, an
SPDX SBOM, and a Sigstore bundle for every file. GitHub also publishes signed
provenance and SBOM attestations.

Verify the checksums after downloading the release files:

```bash
sha256sum --check SHA256SUMS
```

Verify the keyless signature and bind it to this repository and its release
workflow:

```bash
cosign verify-blob install.sh \
  --bundle install.sh.sigstore.json \
  --certificate-identity-regexp '^https://github.com/blyaxyz/xraydo/.github/workflows/release.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Verify the GitHub provenance attestations:

```bash
gh attestation verify install.sh --repo blyaxyz/xraydo
gh attestation verify xraydo-source.tar.gz --repo blyaxyz/xraydo
```

The SBOM uses SPDX 2.3 format (`xraydo.spdx.json`). Verify its signature in the
same way with `xraydo.spdx.json.sigstore.json`.
