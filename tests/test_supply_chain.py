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
        self.assertEqual(len(distributions), 6)
        self.assertEqual(len({item["name"] for item in distributions}), len(distributions))
        self.assertEqual({item["image"] for item in distributions}, {
            "ubuntu:22.04", "ubuntu:24.04", "ubuntu:26.04", "ubuntu:26.10",
            "debian:12", "debian:13",
        })
        self.assertTrue(all(item["channel"] in {"current", "stable", "lts"}
                            for item in distributions))
        self.assertEqual(matrix["architectures"], ["linux/amd64", "linux/arm64"])
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("support_matrix.json", workflow)
        self.assertIn("fromJSON(needs.support-matrix.outputs.matrix)", workflow)
        self.assertIn("setup-qemu-action", workflow)
        self.assertIn("matrix.platform == 'linux/arm64'", workflow)
        self.assertNotIn("scripts/check_release_assets.sh", workflow)
        smoke = (ROOT / "scripts/ci-platform-smoke.sh").read_text(encoding="utf-8")
        self.assertNotIn("unittest discover", smoke)
        self.assertNotIn("tests/test_install.sh", smoke)

    def test_supported_runtime_floor_matches_oldest_supported_distribution(self) -> None:
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.10"', project)
        self.assertIn('python: ["3.10", "3.14"]', workflow)

    def test_release_has_sbom_signatures_and_attestations(self) -> None:
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        for required in (
            "xraydo.spdx.json",
            "SHA256SUMS",
            "cosign",
            "sigstore.json",
            "attest-build-provenance",
            "attest-sbom",
            "scripts/check_release_assets.sh",
        ):
            self.assertIn(required, workflow)
        action_refs = re.findall(r"uses:\s*[^@\s]+@([^\s#]+)", workflow)
        self.assertTrue(action_refs)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs))
        self.assertTrue((ROOT / "docs/SUPPLY_CHAIN.md").is_file())


if __name__ == "__main__":
    unittest.main()
