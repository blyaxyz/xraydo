#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2])
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )
    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "xraydo",
        "documentNamespace": f"https://github.com/blyaxyz/xraydo/sbom/{uuid.uuid4()}",
        "creationInfo": {
            "created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "creators": ["Tool: xraydo-build-sbom"],
        },
        "packages": [
            {
                "name": "xraydo",
                "SPDXID": "SPDXRef-Package-xraydo",
                "versionInfo": os.environ.get("GITHUB_REF_NAME", "development"),
                "downloadLocation": "https://github.com/blyaxyz/xraydo",
                "filesAnalyzed": False,
                "licenseConcluded": "MIT",
                "licenseDeclared": "MIT",
                "copyrightText": "NOASSERTION",
                "externalRefs": [{
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": "pkg:pypi/own-xray",
                }],
            },
            {
                "name": "PyYAML",
                "SPDXID": "SPDXRef-Package-PyYAML",
                "versionInfo": ">=6.0",
                "downloadLocation": "https://pypi.org/project/PyYAML/",
                "filesAnalyzed": False,
                "licenseConcluded": "MIT",
                "licenseDeclared": "MIT",
                "copyrightText": "NOASSERTION",
                "externalRefs": [{
                    "referenceCategory": "PACKAGE-MANAGER",
                    "referenceType": "purl",
                    "referenceLocator": "pkg:pypi/pyyaml@6.0",
                }],
            },
        ],
        "files": [{
            "fileName": "./" + str(path.relative_to(root)),
            "SPDXID": "SPDXRef-File-" + hashlib.sha256(str(path.relative_to(root)).encode()).hexdigest()[:24],
            "checksums": [{"algorithm": "SHA256", "checksumValue": digest(path)}],
            "licenseConcluded": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        } for path in files],
        "relationships": [],
    }
    document["relationships"] = [{
        "spdxElementId": "SPDXRef-Package-xraydo",
        "relationshipType": "CONTAINS",
        "relatedSpdxElement": item["SPDXID"],
    } for item in document["files"]]
    document["relationships"].extend([
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": "SPDXRef-Package-xraydo",
        },
        {
            "spdxElementId": "SPDXRef-Package-xraydo",
            "relationshipType": "DEPENDS_ON",
            "relatedSpdxElement": "SPDXRef-Package-PyYAML",
        },
    ])
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
