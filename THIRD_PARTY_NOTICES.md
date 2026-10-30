# Third-party notices

`xraydo` itself is licensed under the MIT License. The one-file installer can
download the following independent, unmodified upstream programs directly from
their official GitHub release pages. They remain governed by their own
licenses; the xraydo license does not cover them.

| Component | Pinned release | License | Upstream source |
| --- | --- | --- | --- |
| Xray-core | `v26.10.29` | Mozilla Public License 2.0 | <https://github.com/XTLS/Xray-core/tree/v26.10.29> |
| Hysteria 2 | `app/v2.13.0` | MIT License, copyright 2023 Toby | <https://github.com/apernet/hysteria/tree/app/v2.13.0> |
| Telemt | `3.5.14` | TELEMT Public License 3.3, copyright 2026 Telemt | <https://github.com/telemt/telemt/tree/3.5.14> |

Exact license texts for the pinned sources:

- Xray-core: <https://github.com/XTLS/Xray-core/blob/v26.10.29/LICENSE>
- Hysteria 2: <https://github.com/apernet/hysteria/blob/app/v2.13.0/LICENSE.md>
- Telemt: <https://github.com/telemt/telemt/blob/3.5.14/LICENSE>

The installer verifies each downloaded release artifact against a SHA-256
value embedded for the selected architecture. It does not modify or repackage
the upstream executables. Their names and links identify the upstream projects
and do not imply endorsement of xraydo.

If a release artifact is redistributed instead of being fetched from its
official upstream, the redistributor must review and satisfy the corresponding
license obligations, including preserving required license and attribution
notices and, for Xray-core, the MPL 2.0 source-availability requirements.
