from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cli(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["XRAYDO_DATA_DIR"] = str(tmp_path / "data")
    environment["XRAYDO_GEOIP_URL"] = "off"
    return subprocess.run(
        [sys.executable, "-m", "xraydo.cli", *arguments],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_create_list_and_secret_is_hashed(self) -> None:
        created = run_cli(self.path, "--json", "user", "create", "alice")
        self.assertEqual(created.returncode, 0, created.stderr)
        payload = json.loads(created.stdout)
        token = payload["subscription_token"]
        listed = run_cli(self.path, "--json", "user", "list")
        users = json.loads(listed.stdout)["users"]
        self.assertEqual(users[0]["name"], "alice")
        self.assertRegex(users[0]["id"], r"^[23456789A-HJ-NP-Z]{7}$")
        self.assertEqual(users[0]["numeric_id"], 1)
        self.assertNotIn(token, (self.path / "data" / "state.json").read_text())
        state = json.loads((self.path / "data" / "state.json").read_text())
        self.assertEqual(state["subscription_credentials"][0]["algorithm"], "sha256-v1")
        self.assertEqual((self.path / "data" / "state.json").stat().st_mode & 0o777, 0o600)

    def test_users_can_be_addressed_by_short_and_numeric_id(self) -> None:
        first = json.loads(
            run_cli(self.path, "--json", "user", "create", "alice").stdout
        )["user"]
        second = json.loads(
            run_cli(self.path, "--json", "user", "create", "bob").stdout
        )["user"]
        self.assertEqual((first["numeric_id"], second["numeric_id"]), (1, 2))
        by_number = json.loads(
            run_cli(self.path, "--json", "user", "show", "1").stdout
        )["user"]
        by_short_id = json.loads(
            run_cli(self.path, "--json", "user", "show", second["id"].lower()).stdout
        )["user"]
        by_hash_number = json.loads(
            run_cli(self.path, "--json", "user", "show", "#2").stdout
        )["user"]
        self.assertEqual(by_number["id"], first["id"])
        self.assertEqual(by_short_id["numeric_id"], 2)
        self.assertEqual(by_hash_number["id"], second["id"])

    def test_user_card_and_key_show_include_complete_credentials(self) -> None:
        created = json.loads(
            run_cli(self.path, "--json", "user", "create", "alice").stdout
        )["user"]
        self.assertEqual(
            run_cli(self.path, "user", "set-note", "alice", "priority customer").returncode,
            0,
        )
        state_path = self.path / "data" / "state.json"
        state = json.loads(state_path.read_text())
        key = next(item for item in state["access_keys"] if item["user_id"] == created["id"])
        key["endpoints"]["hysteria2"] = {"uri": "hysteria2://secret@example.test:443"}
        state_path.write_text(json.dumps(state), encoding="utf-8")

        card_result = run_cli(self.path, "--json", "user", "show", "alice")
        self.assertEqual(card_result.returncode, 0, card_result.stderr)
        card = json.loads(card_result.stdout)
        self.assertEqual(card["comment"], "priority customer")
        self.assertEqual(card["access_keys"][0]["credentials"], key["credentials"])
        self.assertEqual(card["subscription"]["entries"][0]["protocol"], "hysteria2")

        shown = json.loads(
            run_cli(self.path, "--json", "key", "show", key["id"]).stdout
        )["access_key"]
        self.assertEqual(shown["credentials"], key["credentials"])
        redacted = json.loads(
            run_cli(self.path, "--json", "key", "show", key["id"], "--redact").stdout
        )["access_key"]
        self.assertEqual(set(redacted["credentials"].values()), {"***"})

    def test_key_create_uses_cached_server_country_in_name(self) -> None:
        run_cli(self.path, "--json", "user", "create", "alice")
        state_path = self.path / "data" / "state.json"
        state = json.loads(state_path.read_text())
        state["settings"]["server_location"] = {
            "ip": "8.8.8.8",
            "country": "DE",
            "source": "https://api.country.is/",
            "version": "2",
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")

        location_result = run_cli(self.path, "--json", "country", "location")
        self.assertEqual(location_result.returncode, 0, location_result.stderr)
        self.assertEqual(
            json.loads(location_result.stdout)["server_location"]["country"],
            "DE",
        )

        created = run_cli(
            self.path,
            "--lang", "ru", "--json", "key", "create",
            "--user", "alice", "--device", "phone",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        key = json.loads(created.stdout)["access_key"]
        self.assertEqual(key["device_name"], "🇩🇪 Германия · phone")
        self.assertEqual(key["location"]["country"], "DE")

    def test_subscription_rotation_and_revocation_cli(self) -> None:
        created = json.loads(run_cli(self.path, "--json", "user", "create", "alice").stdout)
        old_token = created["subscription_token"]
        rotated = run_cli(self.path, "--json", "subscription", "rotate", "alice")
        self.assertEqual(rotated.returncode, 0, rotated.stderr)
        replacement = json.loads(rotated.stdout)["token"]
        self.assertNotEqual(old_token, replacement)
        state_text = (self.path / "data" / "state.json").read_text()
        self.assertNotIn(old_token, state_text)
        self.assertNotIn(replacement, state_text)
        status = json.loads(
            run_cli(self.path, "--json", "subscription", "status", "alice").stdout
        )["subscription"]
        self.assertEqual(status["state"], "active")
        revoked = json.loads(
            run_cli(self.path, "--json", "subscription", "revoke", "alice").stdout
        )["subscription"]
        self.assertEqual(revoked["state"], "revoked")

    def test_subscription_show_and_private_file_export(self) -> None:
        created = json.loads(run_cli(self.path, "--json", "user", "create", "alice").stdout)
        state_path = self.path / "data" / "state.json"
        state = json.loads(state_path.read_text())
        key = next(item for item in state["access_keys"] if item["user_id"] == created["user"]["id"])
        key["endpoints"]["hysteria2"] = {"uri": "hysteria2://secret@example.test:443"}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        shown = run_cli(self.path, "subscription", "show", "alice", "--format", "plain")
        self.assertEqual(shown.stdout, "hysteria2://secret@example.test:443#alice\n")
        updated = json.loads(
            run_cli(self.path, "--json", "subscription", "update", "alice").stdout
        )["subscription"]
        self.assertEqual(updated["entries"], 1)
        self.assertEqual(updated["mtproto"], 0)
        destination = self.path / "subscription.txt"
        exported = run_cli(
            self.path,
            "--json",
            "subscription",
            "export",
            "alice",
            "--format",
            "base64",
            "--output",
            str(destination),
        )
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_subscription_qr_requires_https_and_private_token_file(self) -> None:
        created = json.loads(run_cli(self.path, "--json", "user", "create", "alice").stdout)
        configured = run_cli(
            self.path,
            "--json",
            "settings",
            "subscription-url",
            "https://subscriptions.example.test",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        token_file = self.path / "token"
        token_file.write_text(created["subscription_token"], encoding="utf-8")
        token_file.chmod(0o644)
        refused = run_cli(
            self.path, "--json", "subscription", "qr", "alice", "--token-file", str(token_file)
        )
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(json.loads(refused.stderr)["error"]["code"], "token_file_permissions")

    def test_duplicate_has_stable_error_code(self) -> None:
        self.assertEqual(run_cli(self.path, "user", "create", "alice").returncode, 0)
        duplicate = run_cli(self.path, "--json", "user", "create", "ALICE")
        self.assertEqual(duplicate.returncode, 2)
        self.assertEqual(json.loads(duplicate.stderr)["error"]["code"], "duplicate_name")

    def test_language_persists_and_can_be_overridden(self) -> None:
        self.assertEqual(run_cli(self.path, "settings", "language", "ru").returncode, 0)
        self.assertIn("готов", run_cli(self.path, "status").stdout)
        self.assertIn("ready", run_cli(self.path, "--lang", "en", "status").stdout)

    def test_non_tty_does_not_open_menu(self) -> None:
        result = run_cli(self.path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("terminal", result.stderr.lower())

    def test_role_can_be_selected_in_the_same_binary(self) -> None:
        current = json.loads(run_cli(self.path, "--json", "role", "show").stdout)
        self.assertEqual(current["role"], "standalone")
        changed = json.loads(run_cli(self.path, "--json", "role", "set", "master").stdout)
        self.assertEqual(changed["role"], "master")
        persisted = json.loads(run_cli(self.path, "--json", "role", "show").stdout)
        self.assertEqual(persisted["role"], "master")

        policy = run_cli(
            self.path,
            "--json",
            "node",
            "health-policy",
            "--failure-threshold",
            "4",
            "--recovery-threshold",
            "2",
        )
        self.assertEqual(
            json.loads(policy.stdout)["health_policy"],
            {"failure_threshold": 4, "recovery_threshold": 2},
        )

    def test_status_has_stable_core_health_shape(self) -> None:
        result = run_cli(self.path, "--json", "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        cores = json.loads(result.stdout)["cores"]
        self.assertEqual(set(cores), {"xray", "hysteria", "telemt"})
        for health in cores.values():
            self.assertEqual(
                set(health),
                {"installed", "configured", "state", "version", "protocol"},
            )
            self.assertEqual(set(health["protocol"]), {"state", "check"})

    def test_rename_preserves_credentials_and_searches_old_name(self) -> None:
        created = json.loads(
            run_cli(self.path, "--json", "user", "create", "alice").stdout
        )["user"]
        state_before = json.loads((self.path / "data" / "state.json").read_text())
        credentials_before = next(
            key["credentials"]
            for key in state_before["access_keys"]
            if key["user_id"] == created["id"]
        )
        renamed_result = run_cli(
            self.path, "--json", "user", "rename", "alice", "bob"
        )
        self.assertEqual(renamed_result.returncode, 0, renamed_result.stderr)
        renamed = json.loads(renamed_result.stdout)["user"]
        self.assertEqual(renamed["id"], created["id"])
        state = json.loads((self.path / "data" / "state.json").read_text())
        self.assertNotIn("credentials", renamed)
        key_after = next(key for key in state["access_keys"] if key["user_id"] == renamed["id"])
        self.assertEqual(key_after["credentials"], credentials_before)
        self.assertEqual(renamed["previous_names"][0]["name"], "alice")
        found = json.loads(
            run_cli(self.path, "--json", "user", "search", "alice").stdout
        )["users"]
        self.assertEqual(found[0]["name"], "bob")

    def test_notes_and_tags_are_searchable(self) -> None:
        run_cli(self.path, "user", "create", "alice")
        self.assertEqual(
            run_cli(self.path, "user", "set-note", "alice", "priority customer").returncode,
            0,
        )
        self.assertEqual(
            run_cli(self.path, "user", "set-tags", "alice", "team-a,paid,team-a").returncode,
            0,
        )
        found = json.loads(
            run_cli(self.path, "--json", "user", "search", "team-a").stdout
        )["users"]
        self.assertEqual(found[0]["tags"], ["paid", "team-a"])
        self.assertEqual(
            run_cli(self.path, "user", "set-alias", "alice", "customer-01").returncode,
            0,
        )
        by_alias = json.loads(
            run_cli(self.path, "--json", "user", "search", "customer-01").stdout
        )["users"]
        self.assertEqual(by_alias[0]["name"], "alice")

    def test_country_detection_has_stable_machine_states(self) -> None:
        detected = run_cli(self.path, "--json", "country", "detect", "DE Frankfurt")
        self.assertEqual(detected.returncode, 0, detected.stderr)
        payload = json.loads(detected.stdout)
        self.assertEqual(payload["state"], "detected")
        self.assertEqual(payload["matches"][0]["iso"], "DE")

        ambiguous = run_cli(self.path, "--json", "country", "detect", "Congo-01")
        payload = json.loads(ambiguous.stdout)
        self.assertEqual(payload["state"], "ambiguous")
        self.assertEqual([item["iso"] for item in payload["matches"]], ["CD", "CG"])

    def test_node_cli_preserves_id_and_reports_duplicate_name(self) -> None:
        first = json.loads(
            run_cli(
                self.path,
                "--json",
                "node",
                "add",
                "Germany Frankfurt",
                "--alias",
                "de-1",
            ).stdout
        )
        node_id = first["node"]["id"]
        self.assertEqual(first["node"]["remark"], "🇩🇪 Germany Frankfurt")
        second = json.loads(
            run_cli(self.path, "--json", "node", "add", "Germany Frankfurt").stdout
        )
        self.assertTrue(second["ambiguous_name"])
        renamed = json.loads(
            run_cli(
                self.path,
                "--json",
                "node",
                "rename",
                "de-1",
                "Turkey Istanbul",
                "--country",
                "TR",
            ).stdout
        )
        self.assertEqual(renamed["node"]["id"], node_id)
        self.assertEqual(renamed["node"]["remark"], "🇹🇷 Turkey Istanbul")

    def test_node_sync_queue_retries_and_redacts_payload(self) -> None:
        node = json.loads(
            run_cli(self.path, "--json", "node", "add", "edge", "--alias", "edge-1").stdout
        )["node"]
        run_cli(self.path, "user", "create", "alice")
        listed = run_cli(self.path, "--json", "sync", "list", "--node", "edge-1")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        operation = json.loads(listed.stdout)["operations"][0]
        self.assertNotIn("payload", operation)

        claimed = json.loads(
            run_cli(self.path, "--json", "sync", "claim", "--node", node["id"]).stdout
        )["operations"][0]
        failed = run_cli(
            self.path, "--json", "sync", "fail", claimed["id"], "--error", "offline"
        )
        self.assertEqual(json.loads(failed.stdout)["operation"]["state"], "pending")
        claimed_again = json.loads(
            run_cli(self.path, "--json", "sync", "claim", "--node", "edge-1").stdout
        )["operations"][0]
        acknowledged = run_cli(self.path, "--json", "sync", "ack", claimed_again["id"])
        self.assertEqual(json.loads(acknowledged.stdout)["operation"]["state"], "applied")
        status = json.loads(
            run_cli(self.path, "--json", "node", "status", "edge-1").stdout
        )
        self.assertTrue(status["sync"]["complete"])

        refused = run_cli(self.path, "--json", "node", "remove", "edge-1")
        self.assertEqual(json.loads(refused.stderr)["error"]["code"], "confirmation_required")

    def test_access_key_cli_redacts_secrets_and_revoke_is_irreversible(self) -> None:
        run_cli(self.path, "user", "create", "alice")
        created = run_cli(
            self.path,
            "--json",
            "key",
            "create",
            "--user",
            "alice",
            "--device",
            "phone",
        )
        payload = json.loads(created.stdout)["access_key"]
        self.assertNotEqual(payload["credentials"]["xray_uuid"], "***")
        key_id = payload["id"]

        listed = json.loads(
            run_cli(self.path, "--json", "key", "list", "--user", "alice").stdout
        )["access_keys"]
        self.assertTrue(all(set(key["credentials"].values()) == {"***"} for key in listed))
        revoked = run_cli(self.path, "--json", "key", "revoke", key_id)
        self.assertEqual(json.loads(revoked.stdout)["access_key"]["state"], "revoked")
        enabled = run_cli(self.path, "--json", "key", "enable", key_id)
        self.assertEqual(enabled.returncode, 2)
        self.assertEqual(json.loads(enabled.stderr)["error"]["code"], "access_key_revoked")

    def test_access_key_limit_cli_parses_sizes_duration_and_reset(self) -> None:
        run_cli(self.path, "user", "create", "alice")
        key_id = json.loads(
            run_cli(self.path, "--json", "key", "list", "--user", "alice").stdout
        )["access_keys"][0]["id"]
        limited = run_cli(
            self.path,
            "--json",
            "key",
            "set-limit",
            key_id,
            "--traffic",
            "10GB",
            "--upload",
            "2GB",
            "--download",
            "8GB",
            "--duration",
            "30d",
            "--starts",
            "immediate",
            "--ips",
            "2",
        )
        self.assertEqual(limited.returncode, 0, limited.stderr)
        key = json.loads(limited.stdout)["access_key"]
        self.assertEqual(key["quota"]["total_bytes"], 10 * 1024**3)
        self.assertEqual(key["validity"]["duration_seconds"], 30 * 86_400)
        self.assertIsNotNone(key["validity"]["expires_at"])
        unlimited = run_cli(
            self.path, "--json", "key", "set-limit", key_id, "--unlimited"
        )
        self.assertEqual(unlimited.returncode, 0, unlimited.stderr)
        cleared = json.loads(unlimited.stdout)["access_key"]
        self.assertIsNone(cleared["quota"]["total_bytes"])
        self.assertIsNone(cleared["quota"]["max_ips"])
        self.assertIsNone(cleared["validity"]["duration_seconds"])
        conflict = run_cli(
            self.path, "key", "set-limit", key_id, "--unlimited", "--traffic", "1GB"
        )
        self.assertEqual(conflict.returncode, 2)
        self.assertEqual(
            run_cli(self.path, "key", "reset-traffic", key_id).returncode,
            0,
        )

    def test_usage_cli_is_idempotent_and_aggregates_sources(self) -> None:
        run_cli(self.path, "user", "create", "alice")
        key_id = json.loads(
            run_cli(self.path, "--json", "key", "list", "--user", "alice").stdout
        )["access_keys"][0]["id"]
        run_cli(self.path, "key", "set-limit", key_id, "--traffic", "3B")
        first = run_cli(
            self.path,
            "--json",
            "key",
            "record-usage",
            key_id,
            "--source",
            "node-a",
            "--event-id",
            "event-1",
            "--upload-bytes",
            "1",
            "--download-bytes",
            "2",
            "--connected",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertFalse(json.loads(first.stdout)["duplicate"])
        repeated = run_cli(
            self.path,
            "--json",
            "key",
            "record-usage",
            key_id,
            "--source",
            "node-a",
            "--event-id",
            "event-1",
            "--upload-bytes",
            "1",
            "--download-bytes",
            "2",
        )
        self.assertTrue(json.loads(repeated.stdout)["duplicate"])
        usage = json.loads(
            run_cli(self.path, "--json", "key", "usage", "--key", key_id).stdout
        )["usage"]
        self.assertEqual(usage["quota"]["used_total_bytes"], 3)
        self.assertEqual(usage["sources"]["node-a"]["download_bytes"], 2)

    def test_ip_limit_cli_aggregates_and_only_explicit_command_reveals_ips(self) -> None:
        run_cli(self.path, "user", "create", "alice")
        key_id = json.loads(
            run_cli(self.path, "--json", "key", "list", "--user", "alice").stdout
        )["access_keys"][0]["id"]
        configured = run_cli(
            self.path,
            "--json",
            "key",
            "set-limit",
            key_id,
            "--ips",
            "1",
            "--ip-window",
            "5m",
            "--ip-action",
            "warn",
            "--trusted-network",
            "8.8.8.0/24",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        for index, address in enumerate(("1.1.1.1", "9.9.9.9")):
            result = run_cli(
                self.path,
                "--json",
                "key",
                "record-usage",
                key_id,
                "--source",
                f"node-{index}",
                "--event-id",
                f"ip-{index}",
                "--client-ip",
                address,
                "--connected",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(address, result.stdout)
        explicit = json.loads(
            run_cli(self.path, "--json", "key", "ips", key_id).stdout
        )["ip_usage"]
        self.assertEqual(explicit["count"], 2)
        self.assertEqual(set(explicit["active_ips"]), {"1.1.1.1", "9.9.9.9"})
        cleared = run_cli(self.path, "--json", "key", "clear-ips", key_id)
        self.assertEqual(json.loads(cleared.stdout)["ip_usage"]["count"], 0)

    def test_reality_remote_scan_is_queued_for_exact_node(self) -> None:
        node = json.loads(
            run_cli(self.path, "--json", "node", "add", "edge", "--alias", "edge-1").stdout
        )["node"]
        queued = run_cli(
            self.path, "--json", "reality", "scan", "--node", "edge-1",
            "--target", "example.com", "--sni", "example.com", "--xver", "1",
        )
        self.assertEqual(queued.returncode, 0, queued.stderr)
        payload = json.loads(queued.stdout)
        self.assertEqual(payload["state"], "pending_node_execution")
        self.assertEqual(payload["operation"]["node_id"], node["id"])
        self.assertEqual(payload["operation"]["resource_type"], "reality")
        self.assertNotIn("payload", payload["operation"])
        state = json.loads((self.path / "data" / "state.json").read_text())
        operation = state["node_operations"][-1]
        self.assertEqual(operation["payload"]["target"], "example.com")
        self.assertEqual(operation["payload"]["xver"], 1)

    def test_control_plane_pki_bootstrap_export_and_revocation_cli(self) -> None:
        node = json.loads(
            run_cli(self.path, "--json", "node", "add", "edge", "--alias", "edge-1").stdout
        )["node"]
        initialized = run_cli(
            self.path, "--json", "control", "init", "--public-host", "127.0.0.1"
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        ca = self.path / "control-ca.crt"
        exported = run_cli(
            self.path, "--json", "control", "export-ca", "--output", str(ca)
        )
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertIn("BEGIN CERTIFICATE", ca.read_text())
        token_file = self.path / "bootstrap.token"
        bootstrap = run_cli(
            self.path, "--json", "control", "bootstrap", "--node", "edge-1",
            "--ttl", "1m", "--token-file", str(token_file),
        )
        self.assertEqual(bootstrap.returncode, 0, bootstrap.stderr)
        self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)
        payload = json.loads(bootstrap.stdout)["bootstrap"]
        self.assertEqual(payload["node_id"], node["id"])
        self.assertNotIn("token", payload)
        revoked = run_cli(
            self.path, "--json", "control", "revoke", "--node", "edge-1", "--yes"
        )
        self.assertEqual(revoked.returncode, 0, revoked.stderr)
        self.assertFalse(json.loads(revoked.stdout)["revocation"]["trusted"])

    def test_user_delete_lifecycle_and_state_compaction_cli(self) -> None:
        node = run_cli(self.path, "--json", "node", "add", "edge")
        self.assertEqual(node.returncode, 0, node.stderr)
        created = json.loads(
            run_cli(self.path, "--json", "user", "create", "alice").stdout
        )["user"]
        planned = run_cli(
            self.path, "--json", "user", "delete-plan", "alice", "--grace", "1d"
        )
        self.assertEqual(planned.returncode, 0, planned.stderr)
        self.assertEqual(json.loads(planned.stdout)["plan"]["user_id"], created["id"])
        refused = run_cli(self.path, "--json", "user", "delete", "alice", "--grace", "1d")
        self.assertEqual(refused.returncode, 2)
        requested = run_cli(
            self.path, "--json", "user", "delete", "alice", "--grace", "1d", "--yes"
        )
        self.assertEqual(requested.returncode, 0, requested.stderr)
        status = run_cli(self.path, "--json", "user", "delete-status", created["id"])
        self.assertEqual(json.loads(status.stdout)["deletion"]["state"], "blocking")
        preview = run_cli(self.path, "--json", "state", "compact", "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertTrue(json.loads(preview.stdout)["compaction"]["dry_run"])

    def test_system_update_dry_run_is_available_from_xraydo_cli(self) -> None:
        installer = self.path / "installer.sh"
        installer.write_text(
            "#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo 1.2.3; "
            "else printf '%s\\n' \"$*\"; fi\n",
            encoding="utf-8",
        )
        os.chmod(installer, 0o700)
        result = run_cli(
            self.path, "--json", "system", "update", "--dry-run",
            "--prefix", str(self.path / "prefix"), "--installer", str(installer),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)["system"]
        self.assertEqual(payload["action"], "update")
        self.assertIn("--update", payload["stdout"])

    def test_inbound_import_list_show_edit_history_and_bulk_plan(self) -> None:
        config_file = self.path / "xray.json"
        config_file.write_text(json.dumps({"inbounds": [
            {"tag": "one", "listen": "0.0.0.0", "port": 443, "protocol": "vless",
             "settings": {"clients": [{"id": "11111111-1111-4111-8111-111111111111", "password": "hidden"}]},
             "streamSettings": {"network": "tcp", "security": "none"}},
            {"tag": "two", "listen": "127.0.0.1", "port": 8443, "protocol": "http",
             "settings": {}, "streamSettings": {"network": "tcp", "security": "none"}},
        ]}), encoding="utf-8")
        first = run_cli(self.path, "--json", "inbound", "import", "--file", str(config_file), "--index", "0")
        second = run_cli(self.path, "--json", "inbound", "import", "--file", str(config_file), "--index", "1")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        first_id = json.loads(first.stdout)["inbound"]["id"]
        second_id = json.loads(second.stdout)["inbound"]["id"]
        shown = json.loads(run_cli(self.path, "--json", "inbound", "show", first_id).stdout)["inbound"]
        self.assertEqual(shown["config"]["settings"]["clients"][0]["id"], "***")
        self.assertNotIn("hidden", json.dumps(shown))
        listed = json.loads(run_cli(
            self.path, "--json", "inbound", "list", "--protocol", "vless", "--port", "443"
        ).stdout)["inbounds"]
        self.assertEqual([item["id"] for item in listed], [first_id])
        planned = run_cli(
            self.path, "--json", "inbound", "edit", first_id,
            "--expected-revision", "1", "--set", "port=9443",
        )
        self.assertEqual(planned.returncode, 0, planned.stderr)
        self.assertEqual(json.loads(planned.stdout)["state"], "planned")
        bulk = run_cli(
            self.path, "--json", "inbound", "bulk-edit",
            "--target", f"local:{first_id}", "--target", f"local:{second_id}",
            "--set", "sniffing.enabled=false",
        )
        self.assertEqual(bulk.returncode, 0, bulk.stderr)
        self.assertTrue(json.loads(bulk.stdout)["complete"])
        history = json.loads(run_cli(self.path, "--json", "inbound", "history", first_id).stdout)
        self.assertEqual(history["history"][0]["revision"], 1)
