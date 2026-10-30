from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from xraydo.access_keys import AccessKeyService
from xraydo.store import Store
from xraydo.subscription_server import create_subscription_server, tls_context
from xraydo.users import UserService


@unittest.skipUnless(shutil.which("openssl"), "openssl is required for TLS fixture")
class SubscriptionServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cert = self.root / "server.crt"
        self.key = self.root / "server.key"
        result = subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-days", "1", "-subj", "/CN=localhost",
                "-addext", "subjectAltName=DNS:localhost",
                "-keyout", str(self.key), "-out", str(self.cert),
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            self.skipTest("openssl does not support the TLS fixture command")
        self.key.chmod(0o600)
        self.store = Store(self.root / "state")
        self.users = UserService(self.store)
        self.user, self.token = self.users.create("alice")
        key = AccessKeyService(self.store).default_for_user(self.user["id"])
        AccessKeyService(self.store).set_endpoint(
            key["id"], "hysteria2", {"uri": "hysteria2://secret@example.test:443"}
        )
        try:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
        except PermissionError:
            self.skipTest("loopback sockets are blocked by the test sandbox")
        self.server = create_subscription_server(
            store=self.store,
            listen="127.0.0.1",
            port=port,
            public_host="localhost",
            cert=self.cert,
            key=self.key,
            max_clients=2,
        )
        self.port = port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
        if hasattr(self, "thread"):
            self.thread.join(timeout=2)
        self.temporary.cleanup()

    def test_https_serves_subscription_and_rejects_invalid_token(self) -> None:
        context = ssl._create_unverified_context()
        token = urllib.parse.quote(self.token, safe="")
        with urllib.request.urlopen(
            f"https://127.0.0.1:{self.port}/sub/{token}", context=context, timeout=5
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertTrue(response.read().strip())
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(
                f"https://127.0.0.1:{self.port}/sub/invalid", context=context, timeout=5
            )
        self.assertEqual(failure.exception.code, 404)
        failure.exception.close()

    def test_plain_http_is_never_served_on_tls_port(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as connection:
            connection.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            try:
                response = connection.recv(1024)
            except (ConnectionResetError, socket.timeout):
                response = b""
        self.assertNotIn(b"HTTP/1.1 200", response)

    def test_tls_validation_rejects_name_and_key_permissions(self) -> None:
        with self.assertRaisesRegex(ValueError, "subscription_tls_name_mismatch"):
            tls_context(self.cert, self.key, "wrong.example")
        self.key.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "subscription_key_permissions"):
            tls_context(self.cert, self.key, "localhost")
