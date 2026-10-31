# Проверка релиза xraydo

Каждый GitHub Release содержит `install.sh`, исходный архив, `SHA256SUMS`, SPDX
SBOM и Sigstore bundle для каждого файла. GitHub также публикует подписанные
provenance- и SBOM-attestations.

После загрузки файлов проверьте контрольные суммы:

```bash
sha256sum --check SHA256SUMS
```

Проверьте keyless-подпись с привязкой к этому репозиторию и release workflow:

```bash
cosign verify-blob install.sh \
  --bundle install.sh.sigstore.json \
  --certificate-identity-regexp '^https://github.com/blyaxyz/xraydo/.github/workflows/release.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Проверьте GitHub provenance attestation:

```bash
gh attestation verify install.sh --repo blyaxyz/xraydo
gh attestation verify xraydo-source.tar.gz --repo blyaxyz/xraydo
```

SBOM имеет формат SPDX 2.3 (`xraydo.spdx.json`). Его собственная подпись
проверяется так же через `xraydo.spdx.json.sigstore.json`.
