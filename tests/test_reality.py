from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xraydo.reality import RealityError, configure_reality, profile_target, scan_target


KEY_OUTPUT = """PrivateKey: AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
Password (PublicKey): BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
Hash32: CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC
"""


class RealityTests(unittest.TestCase):
    def fake_xray(self, root: Path, valid: bool = True) -> Path:
        binary = root / ("xray-valid" if valid else "xray-invalid")
        status = "0" if valid else "23"
        binary.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = x25519 ]; then\n"
            f"  printf '%s' '{KEY_OUTPUT}'\n"
            "  exit 0\n"
            "fi\n"
            f"exit {status}\n",
            encoding="utf-8",
        )
        binary.chmod(0o755)
        return binary

    def test_config_is_validated_and_private_key_is_not_returned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "config.json"
            destination.write_text("{}\n", encoding="utf-8")
            scan = {
                "target": "example.com:443",
                "server_name": "example.com",
                "resolved_ips": ["93.184.216.34"],
                "tls_version": "TLSv1.3",
                "alpn": "h2",
                "sans": ["example.com"],
                "latency_ms": 10.0,
            }
            with mock.patch("xraydo.reality.scan_target", return_value=scan):
                endpoint = configure_reality(
                    binary=str(self.fake_xray(root)),
                    destination=destination,
                    user={
                        "name": "alice",
                        "credentials": {"xray_uuid": "11111111-1111-4111-8111-111111111111"},
                    },
                    target_host="example.com",
                    target_port=443,
                    listen_port=443,
                    fingerprint="firefox",
                )
            config = json.loads(destination.read_text(encoding="utf-8"))
            reality = config["inbounds"][0]["streamSettings"]["realitySettings"]
            self.assertEqual(reality["privateKey"], "A" * 43)
            self.assertNotIn("private_key", endpoint)
            self.assertEqual(endpoint["public_key"], "B" * 43)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o640)

    def test_invalid_generated_config_preserves_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "config.json"
            destination.write_text("original\n", encoding="utf-8")
            scan = {"target": "example.com:443", "server_name": "example.com"}
            with mock.patch("xraydo.reality.scan_target", return_value=scan):
                with self.assertRaisesRegex(RealityError, "xray_config_invalid"):
                    configure_reality(
                        binary=str(self.fake_xray(root, valid=False)),
                        destination=destination,
                        user={
                            "name": "alice",
                            "credentials": {"xray_uuid": "11111111-1111-4111-8111-111111111111"},
                        },
                        target_host="example.com",
                        target_port=443,
                        listen_port=443,
                        fingerprint="firefox",
                    )
            self.assertEqual(destination.read_text(encoding="utf-8"), "original\n")

    def test_private_target_is_blocked(self) -> None:
        result = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with mock.patch("socket.getaddrinfo", return_value=result):
            with self.assertRaisesRegex(RealityError, "reality_private_target"):
                scan_target("localhost")

    def test_profile_falls_back_and_records_exact_failure(self) -> None:
        verified = {
            "target": "www.cloudflare.com:443",
            "server_name": "www.cloudflare.com",
            "latency_ms": 20.0,
        }
        with mock.patch(
            "xraydo.reality.scan_target",
            side_effect=[RealityError("reality_tls_failed"), verified, RealityError("reality_h2_required"), RealityError("reality_tls_failed"), RealityError("reality_tls_failed")],
        ):
            selected, alternatives = profile_target()
        self.assertEqual(selected["target"], "www.cloudflare.com:443")
        self.assertEqual(selected["failed_candidates"][0]["target"], "ya.ru:443")
        self.assertEqual(selected["failed_candidates"][0]["reason"], "reality_tls_failed")
        self.assertEqual(alternatives, [])
