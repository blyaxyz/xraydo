from __future__ import annotations

import json
import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xraydo.reality import (
    DEFAULT_REALITY_TARGET,
    MAX_CIDR_ADDRESSES,
    RealityError,
    _prefer_x25519,
    _proxy_header,
    configure_reality,
    discover_targets,
    expand_targets,
    fingerprint_profiles,
    profile_target,
    scan_target,
)
from xraydo.store import Store


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

    def test_x25519_preference_falls_back_to_system_tls_group_policy(self) -> None:
        context = mock.Mock()
        context.set_ecdh_curve.side_effect = ssl.SSLError("UNKNOWN_GROUP")
        self.assertEqual(_prefer_x25519(context), "system-default")

        context.set_ecdh_curve.side_effect = None
        self.assertEqual(_prefer_x25519(context), "X25519")
        context.set_ecdh_curve.assert_called_with("X25519")

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
                        "access_key_id": "22222222-2222-4222-8222-222222222222",
                        "credentials": {"xray_uuid": "11111111-1111-4111-8111-111111111111"},
                    },
                    target_host="example.com",
                    target_port=443,
                    listen_port=443,
                    fingerprint="firefox",
                )
            config = json.loads(destination.read_text(encoding="utf-8"))
            reality = config["inbounds"][0]["streamSettings"]["realitySettings"]
            client = config["inbounds"][0]["settings"]["clients"][0]
            self.assertEqual(reality["privateKey"], "A" * 43)
            self.assertEqual(
                client["email"],
                "access-key:22222222-2222-4222-8222-222222222222",
            )
            self.assertNotIn("alice", json.dumps(config))
            self.assertNotIn("private_key", endpoint)
            self.assertEqual(endpoint["public_key"], "B" * 43)
            self.assertEqual(endpoint["stats_id"], client["email"])
            self.assertEqual(destination.stat().st_mode & 0o777, 0o640)

    def test_missing_target_defaults_to_ya_ru_for_target_and_sni(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "config.json"
            destination.write_text("{}\n", encoding="utf-8")
            scan = {
                "target": "ya.ru:443", "server_name": "ya.ru",
                "resolved_ips": ["77.88.55.242"], "tls_version": "TLSv1.3",
                "alpn": "h2", "sans": ["ya.ru"], "latency_ms": 10.0,
            }
            with mock.patch("xraydo.reality.scan_target", return_value=scan) as probe:
                configure_reality(
                    binary=str(self.fake_xray(root)), destination=destination,
                    user={
                        "name": "alice",
                        "access_key_id": "22222222-2222-4222-8222-222222222222",
                        "credentials": {"xray_uuid": "11111111-1111-4111-8111-111111111111"},
                    },
                    target_host=None, target_port=443, listen_port=443,
                    fingerprint="firefox",
                )
            self.assertEqual(probe.call_args.args[:2], (DEFAULT_REALITY_TARGET, 443))
            self.assertEqual(probe.call_args.kwargs["sni"], DEFAULT_REALITY_TARGET)
            config = json.loads(destination.read_text(encoding="utf-8"))
            settings = config["inbounds"][0]["streamSettings"]["realitySettings"]
            self.assertEqual(settings["target"], "ya.ru:443")
            self.assertEqual(settings["serverNames"], ["ya.ru"])

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
                            "access_key_id": "22222222-2222-4222-8222-222222222222",
                            "credentials": {"xray_uuid": "11111111-1111-4111-8111-111111111111"},
                        },
                        target_host="example.com",
                        target_port=443,
                        listen_port=443,
                        fingerprint="firefox",
                    )
            self.assertEqual(destination.read_text(encoding="utf-8"), "original\n")

    def test_reality_refuses_mutable_name_as_stats_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "config.json"
            destination.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(RealityError, "reality_access_key_id_required"):
                configure_reality(
                    binary=str(self.fake_xray(root)),
                    destination=destination,
                    user={
                        "name": "alice",
                        "credentials": {
                            "xray_uuid": "11111111-1111-4111-8111-111111111111"
                        },
                    },
                    target_host="example.com",
                    target_port=443,
                    listen_port=443,
                    fingerprint="firefox",
                )

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

    def test_profile_prefers_verified_ya_ru_over_faster_alternative(self) -> None:
        amazon = {
            "target": "www.amazon.com:443",
            "server_name": "www.amazon.com",
            "latency_ms": 1.0,
        }
        ya = {"target": "ya.ru:443", "server_name": "ya.ru", "latency_ms": 20.0}
        with mock.patch(
            "xraydo.reality.discover_targets",
            return_value={"recommended": [amazon, ya], "rejected": []},
        ):
            selected, alternatives = profile_target()
        self.assertEqual(selected["target"], "ya.ru:443")
        self.assertEqual(
            [item["target"] for item in alternatives], ["www.amazon.com:443"]
        )

    def test_ip_without_sni_extracts_non_wildcard_san_then_verifies(self) -> None:
        unverified = {"sans": ["*.example.test", "edge.example.test"]}
        verified = {
            "tls_version": "TLSv1.3", "alpn": "h2", "curve": "X25519",
            "sans": ["edge.example.test"], "issuer": "CN=CA", "subject": "CN=edge",
            "not_after": "Jan 1 00:00:00 2030 GMT", "chain_certificates": 2,
            "chain_size_bytes": 4096, "latency_ms": 12.3,
        }
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with mock.patch("socket.getaddrinfo", return_value=answers), mock.patch(
            "xraydo.reality._tls_probe", side_effect=[unverified, verified]
        ) as probe:
            result = scan_target("93.184.216.34")
        self.assertEqual(result["sni"], "edge.example.test")
        self.assertEqual(result["chain_size_bytes"], 4096)
        self.assertEqual(probe.call_args_list[1].args[2], "edge.example.test")

    def test_scan_prefers_ipv4_when_ipv6_has_no_route(self) -> None:
        verified = {
            "tls_version": "TLSv1.3", "alpn": "h2", "curve": "X25519",
            "sans": ["ya.ru"], "issuer": "CN=CA", "subject": "CN=ya.ru",
            "not_after": "Jan 1 00:00:00 2030 GMT", "chain_certificates": 2,
            "chain_size_bytes": 4096, "latency_ms": 12.3,
        }
        answers = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2a02:6b8::2:242", 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("77.88.55.242", 443)),
        ]
        with mock.patch("socket.getaddrinfo", return_value=answers), mock.patch(
            "xraydo.reality._tls_probe", return_value=verified
        ) as probe:
            result = scan_target("ya.ru", sni="ya.ru")
        self.assertEqual(result["resolved_ip"], "77.88.55.242")
        self.assertEqual(result["resolved_ips"], ["77.88.55.242", "2a02:6b8::2:242"])
        self.assertEqual(probe.call_args.args[0], "77.88.55.242")

    def test_scan_tries_next_resolved_address_after_tls_failure(self) -> None:
        verified = {
            "tls_version": "TLSv1.3", "alpn": "h2", "curve": "X25519",
            "sans": ["ya.ru"], "issuer": "CN=CA", "subject": "CN=ya.ru",
            "not_after": "Jan 1 00:00:00 2030 GMT", "chain_certificates": 2,
            "chain_size_bytes": 4096, "latency_ms": 12.3,
        }
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("5.255.255.242", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("77.88.55.242", 443)),
        ]
        with mock.patch("socket.getaddrinfo", return_value=answers), mock.patch(
            "xraydo.reality._tls_probe",
            side_effect=[RealityError("tls"), verified],
        ) as probe:
            result = scan_target("ya.ru", sni="ya.ru")
        self.assertEqual(result["resolved_ip"], "77.88.55.242")
        self.assertEqual(probe.call_count, 2)

    def test_proxy_protocol_v1_and_v2_use_socket_addresses(self) -> None:
        raw = mock.Mock()
        raw.getsockname.return_value = ("192.0.2.10", 12345)
        raw.getpeername.return_value = ("93.184.216.34", 443)
        self.assertEqual(
            _proxy_header(raw, 1),
            b"PROXY TCP4 192.0.2.10 93.184.216.34 12345 443\r\n",
        )
        header = _proxy_header(raw, 2)
        self.assertTrue(header.startswith(b"\r\n\r\n\x00\r\nQUIT\n\x21\x11"))
        self.assertEqual(int.from_bytes(header[14:16], "big"), 12)

    def test_discovery_limits_private_cidr_sorting_and_rejections(self) -> None:
        self.assertEqual(len(expand_targets(["8.8.8.0/30"])), 2)
        with self.assertRaisesRegex(RealityError, "blocked_address"):
            expand_targets(["127.0.0.0/30"])
        with self.assertRaisesRegex(RealityError, "cidr_limit"):
            expand_targets(["8.8.0.0/23"])
        with self.assertRaisesRegex(RealityError, "task_limit"):
            expand_targets(["example.com", "example.net"], max_tasks=1)
        with self.assertRaisesRegex(RealityError, "scan_limit"):
            expand_targets(["example.com"], max_tasks=513)
        self.assertEqual(MAX_CIDR_ADDRESSES, 256)
        fast = {"target": "a.test:443", "sni": "a.test", "suitable": True, "latency_ms": 5}
        slow = {"target": "b.test:443", "sni": "b.test", "suitable": True, "latency_ms": 30}
        bad = {"target": "c.test:443", "sni": "c.test", "suitable": False, "reason": "alpn"}
        with mock.patch("xraydo.reality.scan_result", side_effect=[slow, bad, fast]):
            report = discover_targets(["b.test", "c.test", "a.test"], concurrency=2)
        self.assertEqual([item["target"] for item in report["recommended"]], ["a.test:443", "b.test:443"])
        self.assertEqual(report["rejected"][0]["reason"], "alpn")

    def test_utls_profiles_are_grouped_and_unsafe_is_rejected_for_reality(self) -> None:
        report = fingerprint_profiles()
        profiles = {item["name"]: item for item in report["profiles"]}
        self.assertTrue(profiles["firefox"]["recommended"])
        self.assertEqual(profiles["randomizednoalpn"]["group"], "randomization")
        self.assertFalse(profiles["unsafe"]["supported"])

    def test_apply_rechecks_target_generates_subscription_uri_and_marks_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "config.json"
            destination.write_text("{}\n", encoding="utf-8")
            state = Store(root / "state")
            rejected = {"target": "bad.test:443", "host": "bad.test", "port": 443,
                        "server_name": "bad.test", "sni": "bad.test", "node_id": "local",
                        "suitable": False, "reason": "alpn", "checked_at": "now"}
            with mock.patch("xraydo.reality.scan_result", side_effect=[rejected, rejected]) as scanner:
                endpoint = configure_reality(
                    binary=str(self.fake_xray(root)), destination=destination,
                    user={"name": "alice", "access_key_id": "22222222-2222-4222-8222-222222222222",
                          "credentials": {"xray_uuid": "11111111-1111-4111-8111-111111111111"}},
                    target_host="bad.test", target_port=443, listen_port=443,
                    fingerprint="firefox", server="vpn.example.test",
                    force_unverified_target=True, force_confirmation="bad.test/bad.test", store=state,
                )
            self.assertEqual(scanner.call_count, 2)
            self.assertEqual(endpoint["verification_state"], "unverified")
            self.assertTrue(endpoint["health_check"]["rollback_available"])
            self.assertIn("fp=firefox", endpoint["uri"])
            self.assertIn("@vpn.example.test:443?", endpoint["uri"])
            self.assertEqual(state.load()["audit_log"][-1]["action"], "reality.force_unverified")

    def test_force_does_not_bypass_private_target_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "config.json"
            destination.write_text("{}\n", encoding="utf-8")
            with mock.patch("xraydo.reality.scan_result", return_value={
                "suitable": False, "reason": "blocked_address", "checked_at": "now"
            }):
                with self.assertRaisesRegex(RealityError, "blocked_address"):
                    configure_reality(
                        binary=str(self.fake_xray(root)), destination=destination,
                        user={"access_key_id": "22222222-2222-4222-8222-222222222222",
                              "credentials": {"xray_uuid": "11111111-1111-4111-8111-111111111111"}},
                        target_host="127.0.0.1", target_port=443, listen_port=443,
                        fingerprint="firefox", force_unverified_target=True,
                        force_confirmation="127.0.0.1/127.0.0.1",
                    )
