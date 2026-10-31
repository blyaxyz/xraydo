from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xraydo.hysteria import HysteriaError, build_config, configure_hysteria, validate_and_install


class HysteriaTests(unittest.TestCase):
    def test_config_uses_userpass_salamander_loopback_stats_and_safe_proxy(self) -> None:
        config = build_config(
            port=1935,
            cert=Path("/secure/server.crt"),
            key=Path("/secure/server.key"),
            username="u_alice",
            password="password-one",
            obfs_type="salamander",
            obfs_password="password-two",
            stats_port=9999,
            stats_secret="password-three",
            masquerade="https://example.com",
        )
        self.assertEqual(config["listen"], ":1935")
        self.assertEqual(config["auth"]["type"], "userpass")
        self.assertEqual(config["obfs"]["type"], "salamander")
        self.assertEqual(config["trafficStats"]["listen"], "127.0.0.1:9999")
        self.assertFalse(config["masquerade"]["proxy"]["insecure"])
        self.assertNotIn("bandwidth", config)

    def test_endpoint_has_pin_and_no_insecure_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert = root / "server.crt"
            key = root / "server.key"
            cert.write_text("certificate", encoding="utf-8")
            key.write_text("key", encoding="utf-8")
            captured = {}

            def capture(_binary, config, _destination):
                captured.update(config)

            with (
                mock.patch("xraydo.hysteria._port_available", return_value=True),
                mock.patch("xraydo.hysteria.select_masquerade", return_value="https://example.com"),
                mock.patch("xraydo.hysteria.certificate_pin", return_value="ab" * 32),
                mock.patch("xraydo.hysteria.certificate_matches", return_value=True),
                mock.patch("xraydo.hysteria.validate_and_install", side_effect=capture),
            ):
                endpoint = configure_hysteria(
                    binary="hysteria",
                    destination=root / "config.yaml",
                    pki_dir=root / "pki",
                    user={
                        "id": "11111111-1111-4111-8111-111111111111",
                        "name": "alice",
                        "credentials": {"hysteria_password": "key-owned-password"},
                    },
                    server="vpn.example.com",
                    cert=cert,
                    key=key,
                )
            self.assertIn("pinSHA256=" + "ab" * 32, endpoint["uri"])
            self.assertNotIn("insecure", endpoint["uri"])
            self.assertNotEqual(endpoint["password"], endpoint["obfs_password"])
            self.assertNotEqual(endpoint["stats_secret"], endpoint["password"])
            self.assertEqual(endpoint["password"], "key-owned-password")
            self.assertEqual(captured["auth"]["userpass"][endpoint["username"]], "key-owned-password")
            self.assertEqual(captured["congestion"]["type"], "bbr")
            self.assertIn("alpn=h3", endpoint["uri"])
            self.assertEqual(endpoint["client_config"]["quic"]["alpn"], ["h3"])

    def test_extended_quic_udp_acme_ech_and_multiple_users(self) -> None:
        config = build_config(
            port=443, cert=Path("/dev/null"), key=Path("/dev/null"),
            username="k_current", password="current-secret", obfs_type="none",
            obfs_password=None, stats_port=9999, stats_secret="stats-secret",
            masquerade="https://example.com", listen="2001:db8::1",
            bandwidth_up="100 mbps", bandwidth_down="500 mbps",
            congestion="reno", ignore_client_bandwidth=False, disable_udp=True,
            udp_idle_timeout="2m", users={"k_existing": "existing-secret"},
            acme={"domains": ["vpn.example.com"], "email": "ops@example.com"},
            ech_key_path=Path("/secure/ech.key"),
        )
        self.assertEqual(config["listen"], "[2001:db8::1]:443")
        self.assertEqual(config["bandwidth"], {"up": "100 mbps", "down": "500 mbps"})
        self.assertEqual(config["congestion"]["type"], "reno")
        self.assertFalse(config["ignoreClientBandwidth"])
        self.assertTrue(config["disableUDP"])
        self.assertEqual(config["udpIdleTimeout"], "2m")
        self.assertEqual(set(config["auth"]["userpass"]), {"k_current", "k_existing"})
        self.assertIn("acme", config)
        self.assertNotIn("tls", config)
        self.assertEqual(config["ech"]["keyPath"], "/secure/ech.key")

    def test_insecure_is_explicit_and_tls_acme_conflicts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cert, key = root / "server.crt", root / "server.key"
            cert.write_text("certificate", encoding="utf-8")
            key.write_text("key", encoding="utf-8")
            common = dict(binary="hysteria", destination=root / "config.yaml",
                          pki_dir=root / "pki", user={"id": "key-id", "name": "phone",
                          "credentials": {"hysteria_password": "password-secret"}},
                          server="vpn.example.com", cert=cert, key=key)
            with self.assertRaisesRegex(HysteriaError, "hysteria_tls_acme_conflict"):
                configure_hysteria(**common, acme={"domains": ["vpn.example.com"]})
            with (mock.patch("xraydo.hysteria._port_available", return_value=True),
                  mock.patch("xraydo.hysteria.select_masquerade", return_value="https://example.com"),
                  mock.patch("xraydo.hysteria.certificate_pin", return_value="ab" * 32),
                  mock.patch("xraydo.hysteria.certificate_matches", return_value=True),
                  mock.patch("xraydo.hysteria.validate_and_install")):
                endpoint = configure_hysteria(**common, allow_insecure=True, remark="🇩🇪 phone")
            self.assertIn("insecure=1", endpoint["uri"])
            self.assertIn("%F0%9F%87%A9%F0%9F%87%AA%20phone", endpoint["uri"])
            self.assertIsNotNone(endpoint["warning"])

    def test_atomic_validation_writes_final_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "config.yaml"
            process = mock.Mock()
            process.wait.side_effect = [__import__("subprocess").TimeoutExpired("hysteria", 1.5), 0]
            with mock.patch("subprocess.Popen", return_value=process):
                validate_and_install(
                    "hysteria",
                    {
                        "listen": ":1935",
                        "trafficStats": {"listen": "127.0.0.1:9999", "secret": "secret"},
                    },
                    destination,
                )
            self.assertIn('"listen": ":1935"', destination.read_text(encoding="utf-8"))
            process.terminate.assert_called_once()
