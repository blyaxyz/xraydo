from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SupplyChainTests(unittest.TestCase):
    def test_ci_is_generated_from_declared_linux_matrix(self) -> None:
        matrix = json.loads(
            (ROOT / "src/xraydo/data/support_matrix.json").read_text(encoding="utf-8")
        )
        distributions = matrix["distributions"]
        self.assertEqual(len(distributions), 14)
        self.assertEqual(len({item["name"] for item in distributions}), len(distributions))
        self.assertEqual(matrix["architectures"], ["linux/amd64", "linux/arm64"])
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("support_matrix.json", workflow)
        self.assertIn("fromJSON(needs.support-matrix.outputs.matrix)", workflow)
        self.assertIn("setup-qemu-action", workflow)

    def test_release_has_sbom_signatures_and_attestations(self) -> None:
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        for required in (
            "xraydo.spdx.json",
            "SHA256SUMS",
            "cosign",
            "sigstore.json",
            "attest-build-provenance",
            "attest-sbom",
        ):
            self.assertIn(required, workflow)
        action_refs = re.findall(r"uses:\s*[^@\s]+@([^\s#]+)", workflow)
        self.assertTrue(action_refs)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs))
        self.assertTrue((ROOT / "docs/SUPPLY_CHAIN.md").is_file())


if __name__ == "__main__":
    unittest.main()
