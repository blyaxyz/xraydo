from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from xraydo.control_plane import (
    ControlPKI, ControlPlaneService, PROTOCOL_VERSION, request_signature,
    serve_control_plane,
)
from xraydo.node_agent import NodeAgent, enroll
from xraydo.nodes import NodeService
from xraydo.store import Store
from xraydo.sync_queue import SyncQueueService, enqueue_node_operation
from xraydo.users import UserService


def _serve_integration(master: str, pki: str, port: int) -> None:
    store = Store(Path(master))
    serve_control_plane(ControlPlaneService(store, ControlPKI(Path(pki))), "127.0.0.1", port)


class ControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "master")
        self.node = NodeService(self.store).create("edge", alias="edge")
        self.pki = ControlPKI(self.root / "pki")
        self.pki.initialize("127.0.0.1")
        self.service = ControlPlaneService(self.store, self.pki)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _csr(self, directory: Path) -> str:
        key = directory / "node.key"
        csr = directory / "node.csr"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt",
                        "rsa_keygen_bits:2048", "-out", str(key)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["openssl", "req", "-new", "-key", str(key),
                        "-subj", "/CN=request", "-out", str(csr)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return csr.read_text(encoding="utf-8")

    def _enroll(self) -> dict[str, object]:
        bootstrap = self.service.create_bootstrap(self.node["id"], 60)
        return self.service.enroll(self.node["id"], bootstrap["token"], self._csr(self.root))

    @staticmethod
    def _fingerprint(cert: str) -> str:
        return hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert)).hexdigest()

    def test_bootstrap_is_short_lived_one_time_and_certificate_is_bound_to_node(self) -> None:
        bootstrap = self.service.create_bootstrap("edge", 60)
        csr = self._csr(self.root)
        enrolled = self.service.enroll(self.node["id"], bootstrap["token"], csr)
        self.assertEqual(enrolled["node_id"], self.node["id"])
        self.assertEqual(enrolled["protocol"], PROTOCOL_VERSION)
        with self.assertRaisesRegex(ValueError, "control_invalid_bootstrap"):
            self.service.enroll(self.node["id"], bootstrap["token"], csr)
        decoded_path = self.root / "decoded.crt"
        decoded_path.write_text(enrolled["certificate"], encoding="utf-8")
        subject = subprocess.run(["openssl", "x509", "-in", str(decoded_path), "-noout", "-subject"],
                                 check=True, text=True, stdout=subprocess.PIPE).stdout
        self.assertIn("xraydo-node:" + self.node["id"], subject)

    def test_signed_request_rejects_replay_node_substitution_and_old_protocol(self) -> None:
        enrolled = self._enroll()
        body = b'{"capabilities":["access_key"]}'
        timestamp = str(int(time.time()))
        nonce = "a" * 24
        headers = {
            "X-Xraydo-Node": self.node["id"], "X-Xraydo-Protocol": "1",
            "X-Xraydo-Timestamp": timestamp, "X-Xraydo-Nonce": nonce,
            "X-Xraydo-Signature": request_signature(
                enrolled["secret"], "POST", "/v1/poll", timestamp, nonce, body
            ),
        }
        fingerprint = self._fingerprint(enrolled["certificate"])
        self.assertEqual(self.service.authenticate("POST", "/v1/poll", headers, body,
                                                   fingerprint, self.node["id"]), self.node["id"])
        with self.assertRaisesRegex(ValueError, "control_replay"):
            self.service.authenticate("POST", "/v1/poll", headers, body,
                                      fingerprint, self.node["id"])
        changed = dict(headers)
        changed["X-Xraydo-Node"] = "wrong"
        with self.assertRaisesRegex(ValueError, "control_node_mismatch"):
            self.service.authenticate("POST", "/v1/poll", changed, body,
                                      fingerprint, self.node["id"])
        changed = dict(headers)
        changed["X-Xraydo-Protocol"] = "0"
        with self.assertRaisesRegex(ValueError, "control_incompatible_protocol"):
            self.service.authenticate("POST", "/v1/poll", changed, body,
                                      fingerprint, self.node["id"])

    def test_capabilities_filter_operations_and_ack_is_node_bound_and_idempotent(self) -> None:
        UserService(self.store).create("alice")
        data = self.store.load()
        unsupported = enqueue_node_operation(data, self.node["id"], "core", "install", {})
        self.store.save(data)
        response = self.service.poll(self.node["id"], {
            "capabilities": ["access_key"], "agent_version": "test", "limit": 10,
        })
        self.assertEqual(len(response["operations"]), 1)
        self.assertEqual(response["unsupported_pending"], 1)
        operation = response["operations"][0]
        with self.assertRaisesRegex(ValueError, "sync_wrong_node"):
            self.service.acknowledge("other-node", {
                "operation_id": operation["id"], "lease_id": operation["lease_id"],
                "status": "applied", "result": {},
            })
        request = {"operation_id": operation["id"], "lease_id": operation["lease_id"],
                   "status": "applied", "result": {}}
        first = self.service.acknowledge(self.node["id"], request)
        second = self.service.acknowledge(self.node["id"], request)
        self.assertEqual(first, second)
        self.assertEqual(SyncQueueService(self.store).find(unsupported["id"])["state"], "pending")

    def test_expired_lease_is_redelivered_and_revocation_removes_trust(self) -> None:
        enrolled = self._enroll()
        UserService(self.store).create("alice")
        queue = SyncQueueService(self.store)
        first = queue.claim(self.node["id"], supported_types={"access_key"})[0]
        data = self.store.load()
        stored = next(item for item in data["node_operations"] if item["id"] == first["id"])
        stored["lease_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.store.save(data)
        second = queue.claim(self.node["id"], supported_types={"access_key"})[0]
        self.assertNotEqual(first["lease_id"], second["lease_id"])
        revoked = self.service.revoke(self.node["id"])
        self.assertFalse(revoked["trusted"])
        data = self.store.load()
        self.assertFalse(next(item for item in data["nodes"] if item["id"] == self.node["id"])["enabled"])
        self.assertTrue(all(item["status"] == "revoked" for item in data["node_credentials"]))


class NodeAgentTests(unittest.TestCase):
    def test_apply_is_idempotent_and_revoke_removes_desired_resource(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "agent.json"
            state = root / "state.json"
            config.write_text(json.dumps({
                "master": "https://127.0.0.1:8445", "node_id": "node",
                "secret": "secret", "ca": str(root / "ca"), "cert": str(root / "cert"),
                "key": str(root / "key"), "state": str(state),
                "capabilities": ["access_key"], "protocol": 1,
            }), encoding="utf-8")
            os.chmod(config, 0o600)
            agent = NodeAgent(config)
            operation = {"id": "op-1", "lease_id": "lease-1", "resource_type": "access_key",
                         "resource_id": "key-1", "revision": "1", "action": "upsert",
                         "payload": {"id": "key-1"}}
            self.assertTrue(agent.apply(operation)["result"]["changed"])
            redelivery = dict(operation, lease_id="lease-new")
            self.assertEqual(agent.apply(redelivery)["lease_id"], "lease-new")
            revoke = dict(operation, id="op-2", lease_id="lease-2", action="revoke")
            agent.apply(revoke)
            self.assertEqual(json.loads(state.read_text())["resources"], {})


class NetworkControlPlaneTests(unittest.TestCase):
    def test_real_tls_registration_redelivery_rotation_and_revocation(self) -> None:
        try:
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
        except OSError as error:
            probe.close()
            self.skipTest(f"local sockets unavailable: {error}")
        port = probe.getsockname()[1]
        probe.close()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / "master")
            node = NodeService(store).create("edge")
            UserService(store).create("alice")
            pki = ControlPKI(root / "pki")
            pki.initialize("127.0.0.1")
            service = ControlPlaneService(store, pki)
            bootstrap = service.create_bootstrap(node["id"], 60)
            process = multiprocessing.get_context("fork").Process(
                target=_serve_integration,
                args=(str(store.data_dir), str(pki.root), port), daemon=True,
            )
            process.start()
            try:
                for _attempt in range(50):
                    try:
                        connection = socket.create_connection(("127.0.0.1", port), timeout=0.1)
                        connection.close()
                        break
                    except OSError:
                        time.sleep(0.02)
                else:
                    self.fail("control plane did not start")
                agent_root = root / "agent"
                enroll(f"https://127.0.0.1:{port}", node["id"], bootstrap["token"],
                       agent_root, pki.ca_cert)
                agent = NodeAgent(agent_root / "agent.json")
                first = agent.client.request("/v1/poll", {
                    "capabilities": ["access_key"], "agent_version": "integration", "limit": 10,
                })["operations"][0]
                agent.apply(first)  # response/ACK is deliberately lost
                data = store.load()
                queued = next(item for item in data["node_operations"] if item["id"] == first["id"])
                queued["lease_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
                store.save(data)
                result = agent.run_once()
                self.assertEqual(result["applied"], 1)
                self.assertEqual(SyncQueueService(store).find(first["id"])["state"], "applied")
                self.assertTrue(agent.rotate()["rotated"])
                self.assertEqual(agent.run_once()["received"], 0)
                service.revoke(node["id"])
                with self.assertRaisesRegex(ValueError, "control_untrusted_node"):
                    agent.run_once()
            finally:
                process.terminate()
                process.join(5)


if __name__ == "__main__":
    unittest.main()
