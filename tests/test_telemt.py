from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xraydo.telemt import build_config, configure_telemt, validate_and_install


class TelemtTests(unittest.TestCase):
    def test_config_enables_only_fake_tls_and_secures_api(self) -> None:
        config = build_config(
            port=8443,
            api_port=9091,
            api_secret="control-secret",
            tls_domain="example.com",
            username="u_alice",
            user_secret="01" * 16,
            public_host="proxy.example.com",
            data_dir=Path("/var/lib/telemt"),
        )
        self.assertIn("classic = false", config)
        self.assertIn("secure = false", config)
        self.assertIn("tls = true", config)
        self.assertIn('listen = "127.0.0.1:9091"', config)
        self.assertIn('auth_header = "Bearer control-secret"', config)
        self.assertNotIn("ad_tag", config)

    def test_fake_tls_links_contain_16_byte_secret_and_domain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = {
                "target": "example.com:443",
                "server_name": "example.com",
                "tls_version": "TLSv1.3",
                "alpn": "h2",
            }
            with (
                mock.patch("xraydo.telemt._available_tcp_port", return_value=True),
                mock.patch("xraydo.telemt._ephemeral_port", side_effect=[21001, 21002]),
                mock.patch("xraydo.telemt.select_tls_domain", return_value=("example.com", target)),
                mock.patch("xraydo.telemt.validate_and_install"),
            ):
                endpoint = configure_telemt(
                    binary="telemt",
                    destination=root / "telemt.toml",
                    data_dir=root / "runtime",
                    user={
                        "id": "11111111-1111-4111-8111-111111111111",
                        "name": "alice",
                        "credentials": {"telemt_secret": "12" * 16},
                    },
                    public_host="proxy.example.com",
                )
            self.assertEqual(endpoint["user_secret"], "12" * 16)
            self.assertTrue(endpoint["fake_tls_secret"].startswith("ee" + endpoint["user_secret"]))
            self.assertTrue(endpoint["fake_tls_secret"].endswith("example.com".encode().hex()))
            self.assertIn("tg://proxy?", endpoint["tg_uri"])
            self.assertIn("https://t.me/proxy?", endpoint["https_uri"])

    def test_advanced_secure_mode_uses_dd_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch("xraydo.telemt._available_tcp_port", return_value=True),
                mock.patch("xraydo.telemt._ephemeral_port", side_effect=[21001, 21002]),
                mock.patch("xraydo.telemt.select_tls_domain", return_value=("example.com", {})),
                mock.patch("xraydo.telemt.validate_and_install"),
            ):
                endpoint = configure_telemt(
                    binary="telemt",
                    destination=root / "telemt.toml",
                    data_dir=root / "runtime",
                    user={"id": "11111111-1111-4111-8111-111111111111", "name": "alice"},
                    public_host="proxy.example.com",
                    mode="secure",
                )
            self.assertEqual(endpoint["mode"], "secure")
            self.assertTrue(endpoint["connection_secret"].startswith("dd"))
            self.assertIsNone(endpoint["fake_tls_secret"])

    def test_optional_ad_tag_is_validated_and_written(self) -> None:
        tag = "a1" * 16
        config = build_config(
            port=8443,
            api_port=9091,
            api_secret="control-secret",
            tls_domain="example.com",
            username="u_alice",
            user_secret="01" * 16,
            public_host="proxy.example.com",
            data_dir=Path("/var/lib/telemt"),
            ad_tag=tag,
        )
        self.assertIn(f'ad_tag = "{tag}"', config)
        with self.assertRaisesRegex(ValueError, "telemt_invalid_ad_tag"):
            configure_telemt(
                binary="telemt",
                destination=Path("/tmp/telemt.toml"),
                data_dir=Path("/tmp/telemt-runtime"),
                user={"id": "11111111-1111-4111-8111-111111111111"},
                public_host="proxy.example.com",
                ad_tag="invalid",
            )

    def test_advanced_network_and_timeout_options_are_typed(self) -> None:
        config = build_config(
            port=8443,
            api_port=9091,
            api_secret="control-secret",
            tls_domain="example.com",
            username="u_alice",
            user_secret="01" * 16,
            public_host="proxy.example.com",
            data_dir=Path("/var/lib/telemt"),
            listener_ips=["127.0.0.1", "::1"],
            ipv4=True,
            ipv6=True,
            prefer_ip=6,
            use_middle_proxy=False,
            first_byte_timeout=120,
            handshake_timeout=30,
            keepalive=20,
            ack_timeout=45,
        )
        self.assertIn("use_middle_proxy = false", config)
        self.assertIn("ipv4 = true\nipv6 = true\nprefer = 6", config)
        self.assertIn('ip = "127.0.0.1"', config)
        self.assertIn('ip = "::1"', config)
        self.assertIn("client_keepalive = 20", config)

    def test_network_options_reject_inconsistent_or_unsafe_values(self) -> None:
        common = {
            "binary": "telemt",
            "destination": Path("/tmp/telemt.toml"),
            "data_dir": Path("/tmp/telemt-runtime"),
            "user": {"id": "11111111-1111-4111-8111-111111111111"},
            "public_host": "proxy.example.com",
        }
        with self.assertRaisesRegex(ValueError, "telemt_no_ip_family"):
            configure_telemt(**common, ipv4=False, ipv6=False)
        with self.assertRaisesRegex(ValueError, "telemt_invalid_listener"):
            configure_telemt(**common, listener_ips=["not-an-ip"])
        with self.assertRaisesRegex(ValueError, "telemt_invalid_timeout"):
            configure_telemt(**common, keepalive=0)

    def test_liveness_validation_precedes_atomic_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "telemt.toml"
            process = mock.Mock()
            process.poll.side_effect = [None, 0]
            process.wait.return_value = 0
            probe = mock.Mock(returncode=0)
            with (
                mock.patch("subprocess.Popen", return_value=process),
                mock.patch("subprocess.run", return_value=probe),
            ):
                validate_and_install(
                    "telemt",
                    "[server]\nport = 8443\n",
                    "[server]\nport = 21001\n",
                    destination,
                )
            self.assertEqual(destination.read_text(encoding="utf-8"), "[server]\nport = 8443\n")
            process.terminate.assert_called_once()
