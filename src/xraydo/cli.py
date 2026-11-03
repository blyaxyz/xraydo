from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import shlex
import sys
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .access_keys import AccessKeyService
from .automation import AutomationService
from .acme import CertbotManager
from .countries import (
    detect_countries,
    detect_public_location,
    format_key_name,
    get_country,
    load_countries,
)
from .control_plane import ControlPKI, ControlPlaneService, serve_control_plane
from .certificates import NodeCertificateService
from .health import core_health, overall_health
from .hysteria import configure_hysteria
from .hysteria_manager import HysteriaManager
from .inbounds import InboundService, apply_assignments, load_document
from .i18n import SUPPORTED_LANGUAGES, Translator, installed_language, requested_language
from .menu import run_interactive
from .nodes import NodeService
from .node_agent import NodeAgent, enroll as enroll_node_agent
from .reality import (
    DEFAULT_REALITY_TARGET, FINGERPRINTS, configure_reality, discover_targets,
    fingerprint_profiles, scan_result,
)
from .roles import ROLES, RoleService
from .services import control_service, core_version, service_logs, service_status
from .store import Store
from .state_maintenance import StateMaintenanceService
from .sync_queue import SyncQueueService, enqueue_node_operation, public_operation
from .subscriptions import SubscriptionService
from .subscription_server import serve_https
from .system_lifecycle import SystemLifecycleService
from .telemt import configure_telemt
from .telemt_manager import TelemtManager
from .terminal_ui import error as render_error, response as render_response
from .tls_manager import TLSManager
from .xray_manager import XrayManager
from .users import UserService


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="xraydo", description="Terminal-first Xray management")
    result.add_argument("--lang", choices=SUPPORTED_LANGUAGES, help="temporary output language")
    result.add_argument("--json", action="store_true", help="stable machine-readable output")
    result.add_argument("--version", action="version", version=f"xraydo {__version__}")
    commands = result.add_subparsers(dest="command")

    settings = commands.add_parser("settings")
    settings_commands = settings.add_subparsers(dest="settings_command", required=True)
    language = settings_commands.add_parser("language")
    language.add_argument("value", choices=SUPPORTED_LANGUAGES, nargs="?")
    country_flags = settings_commands.add_parser("country-flags")
    country_flags.add_argument("value", choices=("on", "off"), nargs="?")
    subscription_url = settings_commands.add_parser("subscription-url")
    subscription_url.add_argument("value", nargs="?", help="HTTPS base URL or 'off'")

    role = commands.add_parser("role")
    role_commands = role.add_subparsers(dest="role_command", required=True)
    role_commands.add_parser("show")
    role_set = role_commands.add_parser("set")
    role_set.add_argument("value", choices=ROLES)

    country = commands.add_parser("country")
    country_commands = country.add_subparsers(dest="country_command", required=True)
    country_commands.add_parser("list")
    country_detect = country_commands.add_parser("detect")
    country_detect.add_argument("server_name")
    country_location = country_commands.add_parser("location")
    country_location.add_argument("--refresh", action="store_true")

    node = commands.add_parser("node", aliases=["nodes"])
    node_commands = node.add_subparsers(dest="node_command", required=True)
    node_add = node_commands.add_parser("add")
    node_add.add_argument("name")
    node_add.add_argument("--alias")
    node_add.add_argument("--country", help="ISO code, auto, or none")
    node_add.add_argument("--remark")
    node_commands.add_parser("list")
    node_show = node_commands.add_parser("show")
    node_show.add_argument("reference")
    node_rename = node_commands.add_parser("rename")
    node_rename.add_argument("reference")
    node_rename.add_argument("name")
    node_rename.add_argument("--country", help="ISO code, auto, or none")
    node_country = node_commands.add_parser("set-country")
    node_country.add_argument("reference")
    node_country.add_argument("country", help="ISO code, auto, or none")
    node_alias = node_commands.add_parser("set-alias")
    node_alias.add_argument("reference")
    node_alias.add_argument("alias")
    node_remark = node_commands.add_parser("set-remark")
    node_remark.add_argument("reference")
    node_remark.add_argument("remark")
    node_status = node_commands.add_parser("status")
    node_status.add_argument("reference")
    node_enabled = node_commands.add_parser("set-enabled")
    node_enabled.add_argument("reference")
    node_enabled.add_argument("value", choices=("on", "off"))
    node_heartbeat = node_commands.add_parser("heartbeat")
    node_heartbeat.add_argument("reference")
    node_heartbeat.add_argument("--state", choices=("healthy", "degraded", "unhealthy"), default="healthy")
    node_heartbeat.add_argument("--error")
    node_heartbeat.add_argument("--latency-ms", type=int)
    node_missed = node_commands.add_parser("missed-heartbeat")
    node_missed.add_argument("reference")
    node_missed.add_argument("--error", default="heartbeat timeout")
    node_maintenance = node_commands.add_parser("maintenance")
    node_maintenance.add_argument("reference")
    node_maintenance.add_argument("value", choices=("on", "off"))
    node_policy = node_commands.add_parser("health-policy")
    node_policy.add_argument("--failure-threshold", type=int)
    node_policy.add_argument("--recovery-threshold", type=int)
    node_remove = node_commands.add_parser("remove")
    node_remove.add_argument("reference")
    node_remove.add_argument("--yes", action="store_true")

    sync = commands.add_parser("sync")
    sync_commands = sync.add_subparsers(dest="sync_command", required=True)
    sync_list = sync_commands.add_parser("list")
    sync_list.add_argument("--node", required=True)
    sync_list.add_argument("--state", choices=("pending", "delivering", "applied"))
    sync_claim = sync_commands.add_parser("claim")
    sync_claim.add_argument("--node", required=True)
    sync_claim.add_argument("--limit", type=int, default=100)
    sync_claim.add_argument("--include-payload", action="store_true")
    sync_ack = sync_commands.add_parser("ack")
    sync_ack.add_argument("operation_id")
    sync_ack.add_argument("--result", help="JSON result reported by the node agent")
    sync_fail = sync_commands.add_parser("fail")
    sync_fail.add_argument("operation_id")
    sync_fail.add_argument("--error", required=True)
    sync_summary = sync_commands.add_parser("summary")
    sync_summary.add_argument("--node", required=True)

    control = commands.add_parser("control", help="secure master-to-node control plane")
    control_commands = control.add_subparsers(dest="control_command", required=True)
    control_init = control_commands.add_parser("init")
    control_init.add_argument("--public-host", required=True)
    control_init.add_argument("--dir", type=Path)
    control_init.add_argument("--days", type=int, default=365)
    control_export = control_commands.add_parser("export-ca")
    control_export.add_argument("--dir", type=Path)
    control_export.add_argument("--output", type=Path, required=True)
    control_bootstrap = control_commands.add_parser("bootstrap")
    control_bootstrap.add_argument("--node", required=True)
    control_bootstrap.add_argument("--ttl", default="10m")
    control_bootstrap.add_argument("--token-file", type=Path)
    control_serve = control_commands.add_parser("serve")
    control_serve.add_argument("--dir", type=Path)
    control_serve.add_argument("--listen", default="0.0.0.0")
    control_serve.add_argument("--port", type=int, default=8445)
    control_serve.add_argument("--rate-limit", type=int, default=120)
    control_revoke = control_commands.add_parser("revoke")
    control_revoke.add_argument("--node", required=True)
    control_revoke.add_argument("--yes", action="store_true")

    agent = commands.add_parser("agent", help="authenticated node agent")
    agent_commands = agent.add_subparsers(dest="agent_command", required=True)
    agent_enroll = agent_commands.add_parser("enroll")
    agent_enroll.add_argument("--master", required=True)
    agent_enroll.add_argument("--node", required=True)
    agent_enroll.add_argument("--token-file", type=Path, required=True)
    agent_enroll.add_argument("--ca", type=Path, required=True)
    agent_enroll.add_argument("--dir", type=Path, required=True)
    agent_run = agent_commands.add_parser("run")
    agent_run.add_argument("--config", type=Path, required=True)
    agent_run.add_argument("--once", action="store_true")
    agent_run.add_argument("--interval", type=int, default=10)
    agent_rotate = agent_commands.add_parser("rotate")
    agent_rotate.add_argument("--config", type=Path, required=True)

    user = commands.add_parser("user", aliases=["users"])
    user_commands = user.add_subparsers(dest="user_command", required=True)
    create = user_commands.add_parser("create", aliases=["new"])
    create.add_argument("name", nargs="?", default="default")
    user_commands.add_parser("list")
    show = user_commands.add_parser("show")
    show.add_argument("reference")
    state = user_commands.add_parser("set-enabled")
    state.add_argument("reference")
    state.add_argument("value", choices=("on", "off"))
    rename = user_commands.add_parser("rename")
    rename.add_argument("reference")
    rename.add_argument("new_name")
    note = user_commands.add_parser("set-note")
    note.add_argument("reference")
    note.add_argument("note")
    note.add_argument("--actor")
    tags = user_commands.add_parser("set-tags")
    tags.add_argument("reference")
    tags.add_argument("tags", help="comma-separated tags")
    tags.add_argument("--actor")
    alias = user_commands.add_parser("set-alias")
    alias.add_argument("reference")
    alias.add_argument("alias", help="unique CLI alias; use an empty value to clear")
    admin = user_commands.add_parser("set-admin")
    admin.add_argument("reference")
    admin.add_argument("--contact")
    admin.add_argument("--source")
    admin.add_argument("--external-id")
    admin.add_argument("--attribute", action="append", default=[], metavar="KEY=VALUE")
    admin.add_argument("--clear-attributes", action="store_true")
    admin.add_argument("--actor")
    search = user_commands.add_parser("search")
    search.add_argument("query")
    user_delete_plan = user_commands.add_parser("delete-plan")
    user_delete_plan.add_argument("reference")
    user_delete_plan.add_argument("--grace", default="1d")
    user_delete = user_commands.add_parser("delete")
    user_delete.add_argument("reference")
    user_delete.add_argument("--grace", default="1d")
    user_delete.add_argument("--actor")
    user_delete.add_argument("--yes", action="store_true")
    user_delete_status = user_commands.add_parser("delete-status")
    user_delete_status.add_argument("reference")
    user_purge = user_commands.add_parser("purge")
    user_purge.add_argument("reference")
    user_purge.add_argument("--actor")
    user_purge.add_argument("--yes", action="store_true")

    state_store = commands.add_parser("state", help="state integrity and retention")
    state_commands = state_store.add_subparsers(dest="state_command", required=True)
    state_compact = state_commands.add_parser("compact")
    state_compact.add_argument("--dry-run", action="store_true")
    state_compact.add_argument("--audit-days", type=int)
    state_compact.add_argument("--usage-days", type=int)
    state_compact.add_argument("--ip-days", type=int)
    state_compact.add_argument("--inbound-days", type=int)
    state_compact.add_argument("--operation-days", type=int)
    state_compact.add_argument("--max-records", type=int)

    automation = commands.add_parser("automation", help="scheduled usage and desired-state sync")
    automation_commands = automation.add_subparsers(dest="automation_command", required=True)
    automation_run = automation_commands.add_parser("run")
    automation_run.add_argument("--once", action="store_true")
    automation_run.add_argument("--interval", type=int)
    automation_configure = automation_commands.add_parser("configure")
    automation_configure.add_argument("value", choices=("on", "off"))
    automation_configure.add_argument("--interval", type=int, default=60)

    system = commands.add_parser("system", help="install, update, or remove xraydo")
    system_commands = system.add_subparsers(dest="system_command", required=True)
    system_status = system_commands.add_parser("status")
    system_status.add_argument("--installer", type=Path)
    for lifecycle_action in ("update", "uninstall", "purge"):
        lifecycle = system_commands.add_parser(lifecycle_action)
        lifecycle.add_argument("--prefix", type=Path, default=Path("/usr/local"))
        lifecycle.add_argument("--installer", type=Path)
        lifecycle.add_argument("--dry-run", action="store_true")
        lifecycle.add_argument("--yes", action="store_true")

    subscription = commands.add_parser("subscription")
    subscription_commands = subscription.add_subparsers(
        dest="subscription_command", required=True
    )
    subscription_status = subscription_commands.add_parser("status")
    subscription_status.add_argument("user")
    subscription_create = subscription_commands.add_parser("create")
    subscription_create.add_argument("user")
    subscription_create.add_argument("--actor")
    subscription_rotate = subscription_commands.add_parser("rotate")
    subscription_rotate.add_argument("user")
    subscription_rotate.add_argument("--actor")
    subscription_revoke = subscription_commands.add_parser("revoke")
    subscription_revoke.add_argument("user")
    subscription_revoke.add_argument("--actor")
    subscription_show = subscription_commands.add_parser("show")
    subscription_show.add_argument("user")
    subscription_show.add_argument("--format", choices=("plain", "base64", "json"), default="base64")
    subscription_export = subscription_commands.add_parser("export")
    subscription_export.add_argument("user")
    subscription_export.add_argument("--format", choices=("plain", "base64", "json"), default="base64")
    subscription_export.add_argument("--output", type=Path, required=True)
    subscription_update = subscription_commands.add_parser("update")
    subscription_update.add_argument("user")
    subscription_qr = subscription_commands.add_parser("qr")
    subscription_qr.add_argument("user")
    subscription_qr.add_argument("--token-file", type=Path, required=True)
    subscription_qr.add_argument("--ascii", action="store_true")
    subscription_serve = subscription_commands.add_parser("serve")
    subscription_serve.add_argument("--https-only", action="store_true")
    subscription_serve.add_argument("--listen", default="0.0.0.0")
    subscription_serve.add_argument("--port", type=int, default=8444)
    subscription_serve.add_argument("--public-host", required=True)
    subscription_serve.add_argument("--cert", type=Path, required=True)
    subscription_serve.add_argument("--key", type=Path, required=True)
    subscription_serve.add_argument("--max-clients", type=int, default=8)
    subscription_serve.add_argument("--rate-limit", type=int, default=30)
    subscription_serve.add_argument("--rate-window", type=int, default=60)
    subscription_serve.add_argument("--abuse-failures", type=int, default=10)
    subscription_serve.add_argument("--abuse-ban", type=int, default=300)

    tls = commands.add_parser("tls")
    tls_commands = tls.add_subparsers(dest="tls_command", required=True)
    tls_init = tls_commands.add_parser("init-ca")
    tls_init.add_argument("--dir", type=Path)
    tls_issue = tls_commands.add_parser("issue-self")
    tls_issue.add_argument("--dir", type=Path)
    tls_issue.add_argument("--domain", action="append", default=[])
    tls_issue.add_argument("--ip", action="append", default=[])
    tls_issue.add_argument("--days", type=int, default=90)
    tls_issue.add_argument("--renew-percent", type=int, default=67)
    tls_status = tls_commands.add_parser("status")
    tls_status.add_argument("--dir", type=Path)
    tls_renew = tls_commands.add_parser("renew")
    tls_renew.add_argument("--dir", type=Path)
    tls_renew.add_argument("--force", action="store_true")
    tls_export = tls_commands.add_parser("export-ca")
    tls_export.add_argument("--dir", type=Path)
    tls_export.add_argument("--output", type=Path, required=True)
    tls_daemon = tls_commands.add_parser("renew-daemon")
    tls_daemon.add_argument("--dir", type=Path)
    tls_daemon.add_argument("--interval", type=int, default=3600)
    tls_daemon.add_argument("--jitter", type=int, default=300)
    tls_daemon.add_argument("--once", action="store_true")
    tls_import = tls_commands.add_parser("import")
    tls_import.add_argument("--dir", type=Path)
    tls_import.add_argument("--cert", type=Path, required=True)
    tls_import.add_argument("--key", type=Path, required=True)
    tls_import.add_argument("--ca", type=Path)
    tls_import.add_argument("--chain", type=Path)
    tls_import.add_argument("--domain", action="append", default=[])
    tls_import.add_argument("--ip", action="append", default=[])
    for acme_action in ("acme-issue", "acme-renew", "acme-deploy"):
        acme = tls_commands.add_parser(acme_action)
        acme.add_argument("--dir", type=Path)
        acme.add_argument("--identifier", required=True)
        acme.add_argument("--certbot", type=Path, default=Path("certbot"))
        acme.add_argument("--cert-name", default="xraydo-subscription")
        acme.add_argument("--config-dir", type=Path, default=Path("/etc/letsencrypt"))
        acme.add_argument("--work-dir", type=Path, default=Path("/var/lib/letsencrypt"))
        acme.add_argument("--logs-dir", type=Path, default=Path("/var/log/letsencrypt"))
        if acme_action == "acme-issue":
            acme.add_argument("--email")
            acme.add_argument("--staging", action="store_true")
            acme.add_argument("--webroot", type=Path)
        if acme_action == "acme-renew":
            acme.add_argument("--dry-run", action="store_true")
    tls_rotate = tls_commands.add_parser("rotate-ca")
    tls_rotate.add_argument("action", choices=("prepare", "activate", "finalize"))
    tls_rotate.add_argument("--dir", type=Path)
    tls_rotate.add_argument("--overlap-days", type=int, default=30)
    tls_rotate.add_argument("--force", action="store_true")
    tls_sync_ip = tls_commands.add_parser("sync-ip")
    tls_sync_ip.add_argument("--dir", type=Path)
    tls_sync_ip.add_argument("--ip", action="append", default=[])
    tls_backup = tls_commands.add_parser("backup")
    tls_backup.add_argument("--dir", type=Path)
    tls_backup.add_argument("--output", type=Path, required=True)
    tls_backup.add_argument("--passphrase-file", type=Path, required=True)
    tls_verify_backup = tls_commands.add_parser("verify-backup")
    tls_verify_backup.add_argument("--input", type=Path, required=True)
    tls_verify_backup.add_argument("--passphrase-file", type=Path, required=True)
    tls_node_list = tls_commands.add_parser("node-list")
    tls_node_list.add_argument("--node")
    tls_node_issue = tls_commands.add_parser("node-issue")
    tls_node_issue.add_argument("--node", required=True)
    tls_node_issue.add_argument("--domain", action="append", default=[])
    tls_node_issue.add_argument("--ip", action="append", default=[])
    tls_node_issue.add_argument("--days", type=int, default=90)
    tls_node_issue.add_argument("--renew-percent", type=int, default=67)
    tls_node_renew = tls_commands.add_parser("node-renew")
    tls_node_renew.add_argument("certificate_id")
    tls_node_renew.add_argument("--force", action="store_true")
    tls_node_replace = tls_commands.add_parser("node-replace")
    tls_node_replace.add_argument("certificate_id")
    tls_node_replace.add_argument("--cert", type=Path, required=True)
    tls_node_replace.add_argument("--key", type=Path, required=True)
    tls_node_replace.add_argument("--ca", type=Path)
    tls_node_replace.add_argument("--chain", type=Path)
    tls_node_replace.add_argument("--domain", action="append", default=[])
    tls_node_replace.add_argument("--ip", action="append", default=[])
    for action in ("node-revoke", "node-delete"):
        command = tls_commands.add_parser(action)
        command.add_argument("certificate_id")
        command.add_argument("--yes", action="store_true")
        if action == "node-delete":
            command.add_argument("--force", action="store_true")
    tls_node_assign = tls_commands.add_parser("node-assign")
    tls_node_assign.add_argument("certificate_id")
    tls_node_assign.add_argument("inbound_id")
    tls_node_assign.add_argument("--plan", action="store_true")

    key = commands.add_parser("key", aliases=["keys"])
    key_commands = key.add_subparsers(dest="key_command", required=True)
    key_create = key_commands.add_parser("create")
    key_create.add_argument("--user", required=True)
    key_create.add_argument("--device", default="default")
    key_list = key_commands.add_parser("list")
    key_list.add_argument("--user", required=True)
    key_list.add_argument("--full", action="store_true", help="show complete credentials and endpoints")
    key_show = key_commands.add_parser("show")
    key_show.add_argument("key_id")
    key_show.add_argument("--show-secrets", action="store_true")
    key_show.add_argument("--redact", action="store_true", help="hide credentials")
    key_disable = key_commands.add_parser("disable")
    key_disable.add_argument("key_id")
    key_disable.add_argument(
        "--reason",
        choices=("manual", "traffic", "expiry", "devices", "ip_limit"),
        default="manual",
    )
    key_enable = key_commands.add_parser("enable")
    key_enable.add_argument("key_id")
    key_revoke = key_commands.add_parser("revoke")
    key_revoke.add_argument("key_id")
    key_limit = key_commands.add_parser("set-limit")
    key_limit.add_argument("key_id")
    key_limit.add_argument("--traffic")
    key_limit.add_argument("--upload")
    key_limit.add_argument("--download")
    key_limit.add_argument("--duration")
    key_limit.add_argument("--expires-at")
    key_limit.add_argument("--starts", choices=("immediate", "first_connect"))
    key_limit.add_argument("--ips", type=int)
    key_limit.add_argument("--ip-window")
    key_limit.add_argument(
        "--ip-action", choices=("warn", "temporary_block", "disable")
    )
    key_limit.add_argument("--trusted-network", action="append")
    key_limit.add_argument(
        "--unlimited", action="store_true", help="remove traffic, expiry, device, and IP limits"
    )
    key_extend = key_commands.add_parser("extend")
    key_extend.add_argument("key_id")
    key_extend.add_argument("--duration", required=True)
    key_reset = key_commands.add_parser("reset-traffic")
    key_reset.add_argument("key_id")
    key_record = key_commands.add_parser("record-usage")
    key_record.add_argument("key_id")
    key_record.add_argument("--source", default="local")
    key_record.add_argument("--event-id", required=True)
    key_record.add_argument("--upload-bytes", type=int, default=0)
    key_record.add_argument("--download-bytes", type=int, default=0)
    key_record.add_argument("--connected", action="store_true")
    key_record.add_argument("--client-ip")
    key_usage = key_commands.add_parser("usage")
    key_usage_target = key_usage.add_mutually_exclusive_group(required=True)
    key_usage_target.add_argument("--key", dest="usage_key")
    key_usage_target.add_argument("--user", dest="usage_user")
    key_ips = key_commands.add_parser("ips")
    key_ips.add_argument("key_id")
    key_clear_ips = key_commands.add_parser("clear-ips")
    key_clear_ips.add_argument("key_id")

    audit = commands.add_parser("audit")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)
    audit_list = audit_commands.add_parser("list")
    audit_list.add_argument("--limit", type=int, default=100)

    legacy = commands.add_parser("newUser", help="alias for 'user create'")
    legacy.add_argument("name", nargs="?", default="default")
    commands.add_parser("status")

    inbound = commands.add_parser("inbound", aliases=["inbounds"])
    inbound_commands = inbound.add_subparsers(dest="inbound_command", required=True)
    inbound_import = inbound_commands.add_parser("import")
    inbound_import.add_argument("--node", default="local")
    inbound_import.add_argument("--file", type=Path, required=True)
    inbound_import.add_argument("--index", type=int, default=0)
    inbound_import.add_argument("--remark")
    inbound_import.add_argument("--id")
    inbound_import.add_argument("--api-endpoint", help="local Xray HandlerService endpoint")
    inbound_list = inbound_commands.add_parser("list")
    inbound_list.add_argument("--node")
    inbound_list.add_argument("--core")
    inbound_list.add_argument("--protocol")
    inbound_list.add_argument("--port", type=int)
    inbound_list.add_argument("--state", choices=("enabled", "disabled", "deleted"))
    inbound_list.add_argument("--remark")
    inbound_show = inbound_commands.add_parser("show")
    inbound_show.add_argument("inbound_id")
    inbound_show.add_argument("--node")
    inbound_show.add_argument("--show-secrets", action="store_true")
    inbound_edit = inbound_commands.add_parser("edit")
    inbound_edit.add_argument("inbound_id")
    inbound_edit.add_argument("--node")
    inbound_edit.add_argument("--set", action="append", default=[], dest="assignments", metavar="PATH=VALUE")
    inbound_edit.add_argument("--raw", type=Path)
    inbound_edit.add_argument("--editor", action="store_true")
    inbound_edit.add_argument("--remark")
    inbound_edit.add_argument("--expected-revision", type=int)
    inbound_edit.add_argument("--apply", action="store_true")
    inbound_edit.add_argument("--yes", action="store_true")
    inbound_edit.add_argument("--config-path", type=Path)
    inbound_edit.add_argument("--xray-binary")
    inbound_edit.add_argument("--tls-cert", type=Path)
    inbound_edit.add_argument("--tls-key", type=Path)
    inbound_edit.add_argument("--tls-managed", action="store_true")
    inbound_edit.add_argument("--tls-issue-domain", action="append", default=[])
    inbound_edit.add_argument("--tls-issue-ip", action="append", default=[])
    inbound_edit.add_argument("--fingerprint", choices=FINGERPRINTS)
    inbound_clone = inbound_commands.add_parser("clone")
    inbound_clone.add_argument("inbound_id")
    inbound_clone.add_argument("--source-node")
    inbound_clone.add_argument("--node", required=True)
    inbound_clone.add_argument("--port", type=int)
    inbound_clone.add_argument("--remark")
    for action in ("enable", "disable", "delete", "restore"):
        command = inbound_commands.add_parser(action)
        command.add_argument("inbound_id")
        command.add_argument("--node")
        command.add_argument("--expected-revision", type=int)
        command.add_argument("--apply", action="store_true")
        command.add_argument("--yes", action="store_true")
        command.add_argument("--config-path", type=Path)
        command.add_argument("--xray-binary")
        if action == "delete":
            command.add_argument("--grace", type=int, default=86400)
    inbound_history = inbound_commands.add_parser("history")
    inbound_history.add_argument("inbound_id")
    inbound_history.add_argument("--node")
    inbound_rollback = inbound_commands.add_parser("rollback")
    inbound_rollback.add_argument("inbound_id")
    inbound_rollback.add_argument("--node")
    inbound_rollback.add_argument("--revision", type=int, required=True)
    inbound_rollback.add_argument("--expected-revision", type=int)
    inbound_rollback.add_argument("--apply", action="store_true")
    inbound_rollback.add_argument("--yes", action="store_true")
    inbound_rollback.add_argument("--config-path", type=Path)
    inbound_rollback.add_argument("--xray-binary")
    inbound_bulk = inbound_commands.add_parser("bulk-edit")
    inbound_bulk.add_argument("--target", action="append", required=True, metavar="NODE:INBOUND")
    inbound_bulk.add_argument("--set", action="append", required=True, dest="assignments", metavar="PATH=VALUE")
    inbound_wss = inbound_commands.add_parser("wss-tls")
    inbound_wss.add_argument("--node", default="local")
    inbound_wss.add_argument("--port", type=int, required=True)
    inbound_wss.add_argument("--public-address", required=True)
    inbound_wss.add_argument("--sni", action="append", required=True)
    inbound_wss.add_argument("--http-host", required=True)
    inbound_wss.add_argument("--path")
    inbound_wss.add_argument("--alpn", action="append")
    inbound_wss.add_argument("--cert", type=Path, required=True)
    inbound_wss.add_argument("--key", type=Path, required=True)
    inbound_wss.add_argument("--remark", default="WSS/TLS")
    inbound_probe = inbound_commands.add_parser("probe-port")
    inbound_probe.add_argument("--node", default="local")
    inbound_probe.add_argument("--listen", default="0.0.0.0")
    inbound_probe.add_argument("--port", type=int, required=True)
    inbound_probe.add_argument("--transport", choices=("tcp", "udp"), default="tcp")
    inbound_rotate_plan = inbound_commands.add_parser("rotation-plan")
    inbound_rotate_plan.add_argument("inbound_id")
    inbound_rotate_plan.add_argument("--node")
    inbound_rotate_plan.add_argument("--output", type=Path)
    inbound_rotate_apply = inbound_commands.add_parser("rotation-apply")
    inbound_rotate_apply.add_argument("--plan-file", type=Path, required=True)
    inbound_rotate_apply.add_argument("--yes", action="store_true")
    inbound_rotate_apply.add_argument("--config-path", type=Path)
    inbound_rotate_apply.add_argument("--xray-binary")

    reality_cli = commands.add_parser("reality", help="scan and configure REALITY targets")
    reality_commands = reality_cli.add_subparsers(dest="reality_command", required=True)
    reality_scan = reality_commands.add_parser("scan")
    reality_scan.add_argument("--node", default="local")
    reality_scan.add_argument("--target", required=True)
    reality_scan.add_argument("--sni")
    reality_scan.add_argument("--port", type=int, default=443)
    reality_scan.add_argument("--xver", type=int, choices=(0, 1, 2), default=0)
    reality_scan.add_argument("--timeout", type=float, default=10.0)
    reality_scan.add_argument("--allow-private-single", action="store_true")
    reality_scan.add_argument("--private-confirmation")
    reality_discover = reality_commands.add_parser("discover")
    reality_discover.add_argument("--node", default="local")
    reality_discover.add_argument("--target", action="append", dest="targets", required=True)
    reality_discover.add_argument("--sni")
    reality_discover.add_argument("--xver", type=int, choices=(0, 1, 2), default=0)
    reality_discover.add_argument("--timeout", type=float, default=4.0)
    reality_discover.add_argument("--concurrency", type=int, default=32)
    reality_discover.add_argument("--max-tasks", type=int, default=512)
    reality_discover.add_argument("--max-cidr-addresses", type=int, default=256)
    reality_utls = reality_commands.add_parser("utls")
    reality_utls.add_argument("action", choices=("list",), default="list", nargs="?")
    reality_configure = reality_commands.add_parser("configure")
    reality_configure.add_argument("--node", default="local")
    reality_configure.add_argument("--user", default="default")
    reality_configure.add_argument("--access-key")
    reality_configure.add_argument("--target", default=DEFAULT_REALITY_TARGET)
    reality_configure.add_argument("--sni")
    reality_configure.add_argument("--target-port", type=int, default=443)
    reality_configure.add_argument("--listen-port", type=int, default=443)
    reality_configure.add_argument("--xver", type=int, choices=(0, 1, 2), default=0)
    reality_configure.add_argument("--server", required=True, help="public address placed in subscriptions")
    reality_configure.add_argument("--fingerprint", choices=FINGERPRINTS, default="firefox")
    reality_configure.add_argument("--force-unverified-target", action="store_true")
    reality_configure.add_argument("--force-confirmation")
    reality_configure.add_argument("--allow-private-single", action="store_true")
    reality_configure.add_argument("--private-confirmation")
    reality_configure.add_argument("--config", type=Path)
    reality_configure.add_argument("--interactive", action="store_true")
    reality_configure.add_argument("--candidate", action="append", default=[])

    core = commands.add_parser("core")
    core_commands = core.add_subparsers(dest="core_command", required=True)
    reality = core_commands.add_parser("reality")
    reality.add_argument("--user", default="default")
    reality.add_argument("--access-key")
    reality.add_argument(
        "--target", default=DEFAULT_REALITY_TARGET,
        help=f"REALITY target (default: {DEFAULT_REALITY_TARGET})",
    )
    reality.add_argument("--target-port", type=int, default=443)
    reality.add_argument("--listen-port", type=int, default=443)
    reality.add_argument("--fingerprint", choices=FINGERPRINTS, default="firefox")
    reality.add_argument("--sni")
    reality.add_argument("--xver", type=int, choices=(0, 1, 2), default=0)
    reality.add_argument("--server")
    reality.add_argument("--config", type=Path)
    hysteria = core_commands.add_parser("hysteria")
    hysteria.add_argument("--user", default="default")
    hysteria.add_argument("--access-key")
    hysteria.add_argument("--server", required=True, help="public DNS name or IP in the certificate SAN")
    hysteria.add_argument("--port", type=int, default=1935)
    hysteria.add_argument("--stats-port", type=int, default=9999)
    hysteria.add_argument("--cert", type=Path)
    hysteria.add_argument("--key", type=Path)
    hysteria.add_argument("--masquerade")
    hysteria.add_argument("--obfs", choices=("salamander", "gecko", "none"), default="salamander")
    hysteria.add_argument("--config", type=Path)
    hysteria.add_argument("--node", default="local")
    hysteria.add_argument("--listen", default="0.0.0.0")
    hysteria.add_argument("--bandwidth-up")
    hysteria.add_argument("--bandwidth-down")
    hysteria.add_argument("--congestion", choices=("bbr", "reno"), default="bbr")
    hysteria.add_argument("--ignore-client-bandwidth", action=argparse.BooleanOptionalAction, default=True)
    hysteria.add_argument("--disable-udp", action="store_true")
    hysteria.add_argument("--udp-idle-timeout", default="60s")
    hysteria.add_argument("--tls-managed", action="store_true")
    hysteria.add_argument("--acme-domain", action="append", default=[])
    hysteria.add_argument("--acme-email")
    hysteria.add_argument("--acme-ca")
    hysteria.add_argument("--acme-type", choices=("http", "tls", "dns"))
    hysteria.add_argument("--acme-dir")
    hysteria.add_argument("--ech-key", type=Path)
    hysteria.add_argument("--ech-config")
    hysteria.add_argument("--insecure", action="store_true")
    hysteria.add_argument("--remark")
    telemt = core_commands.add_parser("telemt")
    telemt.add_argument("--user", default="default")
    telemt.add_argument("--access-key")
    telemt.add_argument("--server", required=True, help="public DNS name or IP for client links")
    telemt.add_argument("--port", type=int, default=8443)
    telemt.add_argument("--api-port", type=int, default=9091)
    telemt.add_argument("--tls-domain", help="explicit Fake TLS domain; omit to scan candidates")
    telemt.add_argument("--mode", choices=("tls", "secure", "classic"), default="tls")
    telemt.add_argument("--ad-tag", help="optional Telegram ad tag (32 hexadecimal characters)")
    telemt.add_argument("--listen-ip", action="append", help="listener IP; repeat for multiple addresses")
    telemt.add_argument("--ipv4", action=argparse.BooleanOptionalAction, default=True)
    telemt.add_argument("--ipv6", action=argparse.BooleanOptionalAction, default=False)
    telemt.add_argument("--prefer-ip", type=int, choices=(4, 6), default=4)
    telemt.add_argument("--middle-proxy", action=argparse.BooleanOptionalAction, default=True)
    telemt.add_argument("--first-byte-timeout", type=int, default=300)
    telemt.add_argument("--handshake-timeout", type=int, default=60)
    telemt.add_argument("--keepalive", type=int, default=15)
    telemt.add_argument("--ack-timeout", type=int, default=90)
    telemt.add_argument("--mask-host")
    telemt.add_argument("--mask-port", type=int, default=443)
    telemt.add_argument("--unknown-sni-action", choices=("mask", "reject_handshake"),
                        default="reject_handshake")
    telemt.add_argument("--node", default="local")
    telemt.add_argument("--config", type=Path)

    telemt_admin = commands.add_parser("telemt", help="build and manage Telemt per node")
    telemt_commands = telemt_admin.add_subparsers(dest="telemt_command", required=True)
    for action in ("start", "stop", "restart", "status", "version"):
        command = telemt_commands.add_parser(action)
        command.add_argument("--node", default="local")
    telemt_logs = telemt_commands.add_parser("logs")
    telemt_logs.add_argument("--lines", type=int, default=100)
    telemt_logs.add_argument("--node", default="local")
    telemt_build = telemt_commands.add_parser("build")
    telemt_build.add_argument("--version", required=True, dest="build_ref")
    telemt_build.add_argument("--target")
    telemt_build.add_argument("--profile", choices=("release", "economy"), default="release")
    for action in ("install", "update"):
        command = telemt_commands.add_parser(action)
        command.add_argument("--node", default="local")
        command.add_argument("--version")
        command.add_argument("--artifact")
        command.add_argument("--binary-path", type=Path, default=Path("/usr/local/bin/telemt"))
    telemt_check = telemt_commands.add_parser("check-update")
    telemt_check.add_argument("--node", default="local")
    telemt_rollback = telemt_commands.add_parser("rollback")
    telemt_rollback.add_argument("--node", default="local")
    telemt_rollback.add_argument("--binary-path", type=Path, default=Path("/usr/local/bin/telemt"))
    telemt_user = telemt_commands.add_parser("user")
    telemt_user_commands = telemt_user.add_subparsers(dest="telemt_user_command", required=True)
    for action in ("add", "revoke"):
        command = telemt_user_commands.add_parser(action)
        command.add_argument("user")
        command.add_argument("--access-key")
        command.add_argument("--node", default="all")
        command.add_argument("--mode", choices=("classic", "secure", "tls"), default="tls")
    telemt_link = telemt_commands.add_parser("link")
    telemt_link.add_argument("user")
    telemt_link.add_argument("--node")
    telemt_diagnostics = telemt_commands.add_parser("diagnostics")
    telemt_diagnostics.add_argument("--node", default="local")
    telemt_diagnostics.add_argument("--api-listen")
    telemt_diagnostics.add_argument("--api-auth")
    telemt_stats = telemt_commands.add_parser("collect-stats")
    telemt_stats.add_argument("--node", default="local")
    telemt_stats.add_argument("--api-listen", default="127.0.0.1:9091")
    telemt_stats.add_argument("--api-auth", required=True)
    telemt_backup = telemt_commands.add_parser("backup")
    telemt_backup.add_argument("--node", default="local")
    telemt_backup.add_argument("--config", type=Path, required=True)
    telemt_backup.add_argument("--output", type=Path, required=True)

    hysteria_admin = commands.add_parser("hysteria", help="manage Hysteria 2 per node")
    hysteria_commands = hysteria_admin.add_subparsers(dest="hysteria_command", required=True)
    for action in ("start", "stop", "restart", "status", "version"):
        command = hysteria_commands.add_parser(action)
        command.add_argument("--node", default="local")
    hysteria_logs = hysteria_commands.add_parser("logs")
    hysteria_logs.add_argument("--lines", type=int, default=100)
    hysteria_logs.add_argument("--node", default="local")
    for action in ("install", "update"):
        command = hysteria_commands.add_parser(action)
        command.add_argument("--node", default="local")
        command.add_argument("--version")
        command.add_argument("--binary-path", type=Path, default=Path("/usr/local/bin/hysteria"))
    hysteria_check = hysteria_commands.add_parser("check-update")
    hysteria_check.add_argument("--node", default="local")
    hysteria_rollback = hysteria_commands.add_parser("rollback")
    hysteria_rollback.add_argument("--node", default="local")
    hysteria_rollback.add_argument("--version")
    hysteria_rollback.add_argument("--binary-path", type=Path, default=Path("/usr/local/bin/hysteria"))
    hysteria_capabilities = hysteria_commands.add_parser("capabilities")
    hysteria_capabilities.add_argument("--node", default="local")
    hysteria_diagnostics = hysteria_commands.add_parser("diagnostics")
    hysteria_diagnostics.add_argument("--node", default="local")
    hysteria_revoke = hysteria_commands.add_parser("revoke-key")
    hysteria_revoke.add_argument("key_id")
    hysteria_revoke.add_argument("--node", default="all")
    hysteria_collect = hysteria_commands.add_parser("collect-stats")
    hysteria_collect.add_argument("--node", default="local")
    hysteria_collect.add_argument("--listen", default="127.0.0.1:9999")
    hysteria_collect.add_argument("--secret", required=True)
    hysteria_collect.add_argument("--clear", action="store_true")
    hysteria_backup = hysteria_commands.add_parser("backup")
    hysteria_backup.add_argument("--node", default="local")
    hysteria_backup.add_argument("--config", type=Path, required=True)
    hysteria_backup.add_argument("--output", type=Path, required=True)

    xray_admin = commands.add_parser("xray", help="install and manage Xray per node")
    xray_commands = xray_admin.add_subparsers(dest="xray_command", required=True)
    for action in ("install", "update"):
        command = xray_commands.add_parser(action)
        command.add_argument("--node", default="local")
        command.add_argument("--version")
        command.add_argument("--binary-path", type=Path, default=Path("/usr/local/bin/xray"))
        command.add_argument("--canary", type=int, default=1)
        command.add_argument("--max-unavailable", type=int, default=1)
    for action in ("version", "history", "check-update"):
        command = xray_commands.add_parser(action)
        command.add_argument("--node", default="local")
    xray_rollback = xray_commands.add_parser("rollback")
    xray_rollback.add_argument("--node", default="local")
    xray_rollback.add_argument("--version")
    xray_rollback.add_argument("--binary-path", type=Path, default=Path("/usr/local/bin/xray"))
    xray_backup = xray_commands.add_parser("backup")
    xray_backup.add_argument("--node", default="local")
    xray_backup.add_argument("--config", type=Path, required=True)
    xray_backup.add_argument("--output", type=Path, required=True)
    return result


def emit(payload: object, message: str, machine: bool) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if machine else render_response(message, stream=sys.stdout)
    )


def parse_attributes(values: Sequence[str]) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for value in values:
        key, separator, item_value = value.partition("=")
        if not separator or not key:
            raise ValueError("invalid_admin_metadata")
        attributes[key] = item_value
    return attributes


def parse_size(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    if value.casefold() in {"none", "unlimited", "0"}:
        return 0
    match = re.fullmatch(r"([1-9][0-9]*)(B|KB|MB|GB|TB)", value.strip(), re.IGNORECASE)
    if match is None:
        raise ValueError("access_key_invalid_limit")
    multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    return int(match.group(1)) * multipliers[match.group(2).upper()]


def parse_duration(value: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)(s|m|h|d|w)", value.strip(), re.IGNORECASE)
    if match is None:
        raise ValueError("access_key_invalid_duration")
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86_400, "w": 604_800}
    return int(match.group(1)) * multipliers[match.group(2).casefold()]


def public_access_key(key: dict[str, object], show_secrets: bool = False) -> dict[str, object]:
    result = dict(key)
    credentials = key.get("credentials", {})
    if show_secrets:
        result["credentials"] = credentials
    else:
        result["credentials"] = {name: "***" for name in credentials}
        endpoints = key.get("endpoints", {})
        result["endpoints"] = {
            protocol: {
                "configured": True,
                "config": endpoint.get("config"),
                "access_key_id": endpoint.get("access_key_id"),
            }
            for protocol, endpoint in endpoints.items()
        }
    return result


def server_location(
    store: Store,
    language: str,
    refresh: bool = False,
) -> Optional[dict[str, str]]:
    data = store.load()
    settings = data.setdefault("settings", {})
    if not settings.get("country_auto_flags", True):
        return None
    cached = settings.get("server_location")
    location = cached if isinstance(cached, dict) else None
    country = get_country(str(location.get("country", ""))) if location else None
    if refresh or country is None or location.get("version") != "2":
        location = detect_public_location()
        if location:
            country = get_country(location["country"])
            with store.transaction() as current:
                current.setdefault("settings", {})["server_location"] = location
            if cached:
                AccessKeyService(store).relocate(location, language)
    if not location or country is None:
        return None
    return location


def localized_key_context(
    store: Store,
    device_name: str,
    language: str,
) -> tuple[str, Optional[dict[str, str]]]:
    location = server_location(store, language)
    if not location:
        return device_name, None
    country = get_country(location["country"])
    assert country is not None
    return format_key_name(device_name, country, language), location


def user_for_access_key(user: dict[str, object], key: dict[str, object]) -> dict[str, object]:
    result = dict(user)
    result["credentials"] = key["credentials"]
    result["access_key_id"] = key["id"]
    return result


def manage_local_service(
    core: str,
    display_name: str,
    action: str,
    lines: int,
    translator: Translator,
    machine: bool,
) -> None:
    if action in {"start", "stop", "restart"}:
        result = control_service(core, action)
        emit(
            {"service": result},
            translator.get("service.action_done", core=display_name, action=action),
            machine,
        )
    elif action == "status":
        result = service_status(core)
        emit(
            {"service": result},
            translator.get("service.status", core=display_name, state=result["state"]),
            machine,
        )
    elif action == "logs":
        result = service_logs(core, lines)
        emit({"service": result}, result["logs"], machine)
    elif action == "version":
        result = core_version(core)
        emit(
            {"core": result},
            translator.get("service.version", core=display_name, version=result["version"] or "unknown"),
            machine,
        )


def print_users(users: list[dict[str, object]], translator: Translator) -> None:
    if not users:
        print(translator.get("users.empty"))
    for user in users:
        state = "enabled" if user["enabled"] else "disabled"
        note = str(user.get("note") or "").replace("\n", " ").strip()
        suffix = f"  — {note}" if note else ""
        print(
            f"#{user['numeric_id']}  {user['id']}  {user['name']}  "
            f"{translator.get(f'user.state.{state}')}{suffix}"
        )


def user_admin_view(
    user: dict[str, object],
    keys: AccessKeyService,
    subscriptions: SubscriptionService,
) -> dict[str, object]:
    access_keys = [
        public_access_key(item, show_secrets=True)
        for item in keys.list(str(user["id"]))
    ]
    status = {
        "state": user.get("subscription_state", "active"),
        "enabled": user.get("enabled", True),
    }
    try:
        subscription: dict[str, object] = {
            **status,
            **subscriptions.build(str(user["id"])),
        }
    except ValueError as error:
        subscription = {**status, "error": str(error), "entries": [], "mtproto": []}
    return {
        "user": user,
        "comment": user.get("note"),
        "subscription": subscription,
        "access_keys": access_keys,
    }


def render_user_admin(view: dict[str, object], translator: Translator) -> str:
    user = view["user"]
    assert isinstance(user, dict)
    subscription = view["subscription"]
    assert isinstance(subscription, dict)
    access_keys = view["access_keys"]
    assert isinstance(access_keys, list)
    state = "enabled" if user.get("enabled", True) else "disabled"
    lines = [
        translator.get(
            "user.card.title",
            numeric_id=user.get("numeric_id"),
            name=user.get("name"),
            user_id=user.get("id"),
        ),
        translator.get("user.card.state", state=translator.get(f"user.state.{state}")),
        translator.get("user.card.comment", comment=user.get("note") or "—"),
        translator.get(
            "user.card.tags",
            tags=", ".join(user.get("tags", [])) if user.get("tags") else "—",
        ),
        "",
        translator.get(
            "user.card.subscription",
            state=subscription.get("state", "unknown"),
        ),
    ]
    if subscription.get("error"):
        lines.append(translator.get("user.card.unavailable", error=subscription["error"]))
    for entry in subscription.get("entries", []):
        if isinstance(entry, dict) and entry.get("uri"):
            lines.append(f"  {entry.get('protocol', 'key')}: {entry['uri']}")
    for entry in subscription.get("mtproto", []):
        if isinstance(entry, dict) and entry.get("uri"):
            lines.append(f"  {entry.get('format', 'mtproto')}: {entry['uri']}")
    lines.extend(["", translator.get("user.card.keys", count=len(access_keys))])
    for item in access_keys:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"  [{item.get('state', 'unknown')}] {item.get('id')} · {item.get('device_name', 'default')}"
        )
        credentials = item.get("credentials", {})
        if isinstance(credentials, dict):
            for name, value in credentials.items():
                lines.append(f"    {name}: {value}")
        endpoints = item.get("endpoints", {})
        if isinstance(endpoints, dict):
            for protocol, endpoint in endpoints.items():
                if not isinstance(endpoint, dict):
                    continue
                lines.append(f"    {protocol}:")
                for field in ("uri", "tg_uri", "https_uri", "server", "port", "tls_domain"):
                    if endpoint.get(field) not in (None, ""):
                        lines.append(f"      {field}: {endpoint[field]}")
    return "\n".join(lines) + "\n"


def read_secret_file(path: Path) -> str:
    if str(path) == "-":
        value = sys.stdin.read().strip()
    else:
        try:
            mode = path.stat().st_mode & 0o777
            if mode & 0o077:
                raise ValueError("token_file_permissions")
            value = path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise ValueError("token_file_unreadable") from error
    if not value or len(value) > 256:
        raise ValueError("subscription_token_invalid")
    return value


def print_created_qr(
    subscriptions: SubscriptionService, token: str, translator: Translator
) -> None:
    if not sys.stdout.isatty() or not subscriptions.base_url():
        return
    try:
        print_private_ca_warning(subscriptions.store, translator)
        print(subscriptions.qr(token), end="")
    except (ValueError, RuntimeError) as error:
        print(translator.get(f"error.{str(error)}"), file=sys.stderr)


def print_private_ca_warning(store: Store, translator: Translator) -> None:
    tls = store.load()["settings"].get("subscription_tls", {})
    if tls.get("mode") != "private-ca":
        return
    try:
        fingerprint = TLSManager(store).fingerprint()
    except ValueError:
        fingerprint = "unknown"
    print(
        translator.get("subscription.private_ca_warning", fingerprint=fingerprint),
        file=sys.stderr,
    )


def inbound_edit_document(current: dict[str, object], args: argparse.Namespace) -> dict[str, object]:
    if args.raw:
        proposed = load_document(args.raw)
    else:
        proposed = dict(current)
    if args.assignments:
        proposed = apply_assignments(proposed, args.assignments)
    if args.editor:
        editor = os.environ.get("EDITOR")
        if not editor:
            raise ValueError("inbound_editor_missing")
        descriptor, filename = tempfile.mkstemp(prefix="xraydo-inbound-", suffix=".json")
        path = Path(filename)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(proposed, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.chmod(path, 0o600)
            process = subprocess.run(shlex.split(editor) + [str(path)], check=False)
            if process.returncode != 0:
                raise ValueError("inbound_editor_failed")
            proposed = load_document(path)
        finally:
            path.unlink(missing_ok=True)
    if not args.raw and not args.assignments and not args.editor and sys.stdin.isatty():
        fields = (
            ("remark", None), ("listen", "listen"), ("port", "port"),
            ("protocol", "protocol"), ("transport", "streamSettings.network"),
            ("security", "streamSettings.security"),
        )
        changes = []
        for label, path in fields:
            if label == "remark":
                continue
            target: object = current
            for part in str(path).split("."):
                target = target.get(part, "") if isinstance(target, dict) else ""
            entered = input(f"{label} [{target}]: ").strip()
            if entered:
                changes.append(f"{path}={entered}")
        proposed = apply_assignments(proposed, changes)
    return proposed


def print_inbound_plan(result: dict[str, object]) -> None:
    plan = result.get("plan", {})
    if not isinstance(plan, dict):
        return
    print(f"Inbound {plan.get('node_id')}:{plan.get('inbound_id')} revision {plan.get('base_revision')}")
    for change in plan.get("diff", []):
        print(f"  {change['path']}: {change['old']!r} -> {change['new']!r}")
    impact = plan.get("impact", {})
    if isinstance(impact, dict):
        print(
            f"Impact: users={impact.get('users', 0)}, links={impact.get('subscription_links', 0)}, "
            f"restart={impact.get('restart_required')}, connections_may_drop={impact.get('connections_may_drop')}"
        )
    for warning in plan.get("warnings", []):
        print(f"WARNING: {warning}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    store = Store()
    data = store.load()
    language = requested_language(args.lang, data["settings"].get("language", installed_language()))
    translator = Translator(language)
    service = UserService(store)
    nodes = NodeService(store)
    keys = AccessKeyService(store)
    sync_queue = SyncQueueService(store)
    roles = RoleService(store)
    subscriptions = SubscriptionService(store)
    inbounds = InboundService(store)
    try:
        if args.command is None:
            return run_interactive(
                translator,
                lambda arguments: main(["--lang", translator.language, *arguments]),
                parser(),
            )
        if args.command == "role":
            if args.role_command == "set":
                result = roles.set(args.value)
            else:
                result = {"role": roles.get()}
            emit(result, translator.get("role.current", role=result["role"]), args.json)
        elif args.command == "settings" and args.settings_command == "language":
            if args.value:
                data["settings"]["language"] = args.value
                store.save(data)
                emit({"language": args.value}, translator.get("language.saved", language=args.value), args.json)
            else:
                current = data["settings"].get("language", installed_language())
                emit({"language": current}, translator.get("language.current", language=current), args.json)
        elif args.command == "settings" and args.settings_command == "country-flags":
            if args.value:
                enabled = args.value == "on"
                data["settings"]["country_auto_flags"] = enabled
                store.save(data)
            else:
                enabled = bool(data["settings"].get("country_auto_flags", True))
            emit(
                {"country_auto_flags": enabled},
                translator.get("country.flags", state="on" if enabled else "off"),
                args.json,
            )
        elif args.command == "settings" and args.settings_command == "subscription-url":
            if args.value is not None:
                value = None if args.value == "off" else args.value
                subscriptions.set_base_url(value)
            current = subscriptions.base_url()
            emit(
                {"subscription_base_url": current},
                translator.get("subscription.url", url=current or "off"),
                args.json,
            )
        elif args.command == "country":
            if args.country_command == "list":
                countries = sorted(load_countries(), key=lambda item: item["iso"])
                if args.json:
                    emit({"countries": countries}, "", True)
                else:
                    for country in countries:
                        print(f"{country['flag']} {country['iso']}  {country['names'][language]}")
            elif args.country_command == "detect":
                matches = detect_countries(args.server_name)
                state = "detected" if len(matches) == 1 else "ambiguous" if matches else "unknown"
                if args.json:
                    emit({"state": state, "matches": matches}, "", True)
                elif state == "detected":
                    country = matches[0]
                    print(
                        translator.get(
                            "country.detected",
                            flag=country["flag"],
                            iso=country["iso"],
                            name=country["names"][language],
                        )
                    )
                elif state == "ambiguous":
                    choices = ", ".join(country["iso"] for country in matches)
                    print(translator.get("country.ambiguous", choices=choices))
                else:
                    print(translator.get("country.unknown"))
            elif args.country_command == "location":
                location = server_location(store, language, args.refresh)
                if not location:
                    raise ValueError("country_location_unavailable")
                country = get_country(location["country"])
                assert country is not None
                emit(
                    {"server_location": location},
                    translator.get(
                        "country.location",
                        flag=country["flag"],
                        name=country["names"][language],
                        ip=location["ip"],
                    ),
                    args.json,
                )
        elif args.command in {"node", "nodes"}:
            if args.node_command == "add":
                item = nodes.create(
                    args.name,
                    alias=args.alias,
                    country=args.country,
                    remark=args.remark,
                )
                ambiguous_name = sum(
                    node["name"].casefold() == item["name"].casefold()
                    for node in nodes.list()
                ) > 1
                message = translator.get("node.created", name=item["name"], id=item["id"])
                if ambiguous_name:
                    message += "\n" + translator.get("node.ambiguous_name")
                if item["country_candidates"]:
                    message += "\n" + translator.get(
                        "country.ambiguous",
                        choices=", ".join(item["country_candidates"]),
                    )
                emit(
                    {"node": item, "ambiguous_name": ambiguous_name},
                    message,
                    args.json,
                )
            elif args.node_command == "list":
                items = nodes.list()
                if args.json:
                    emit({"nodes": items}, "", True)
                else:
                    for item in items:
                        print(f"{item['id']}  {item['remark']}  {item.get('alias') or '-'}")
            elif args.node_command == "show":
                item = nodes.find(args.reference)
                emit({"node": item}, json.dumps(item, ensure_ascii=False, indent=2), args.json)
            elif args.node_command == "rename":
                item = nodes.rename(args.reference, args.name, args.country)
                ambiguous_name = sum(
                    node["name"].casefold() == item["name"].casefold()
                    for node in nodes.list()
                ) > 1
                message = translator.get("node.updated", name=item["name"])
                if ambiguous_name:
                    message += "\n" + translator.get("node.ambiguous_name")
                if item["country_candidates"]:
                    message += "\n" + translator.get(
                        "country.ambiguous",
                        choices=", ".join(item["country_candidates"]),
                    )
                emit(
                    {"node": item, "ambiguous_name": ambiguous_name},
                    message,
                    args.json,
                )
            elif args.node_command == "set-country":
                item = nodes.set_country(args.reference, args.country)
                emit(
                    {"node": item},
                    translator.get("node.updated", name=item["name"]),
                    args.json,
                )
            elif args.node_command == "set-alias":
                item = nodes.set_alias(args.reference, args.alias)
                emit(
                    {"node": item},
                    translator.get("node.updated", name=item["name"]),
                    args.json,
                )
            elif args.node_command == "set-remark":
                item = nodes.set_remark(args.reference, args.remark)
                emit(
                    {"node": item},
                    translator.get("node.updated", name=item["name"]),
                    args.json,
                )
            elif args.node_command == "status":
                item = nodes.find(args.reference)
                summary = sync_queue.summary(item["id"])
                emit(
                    {"node": item, "sync": summary},
                    translator.get(
                        "node.status",
                        name=item["name"],
                        state=item["connection_state"],
                        pending=summary["states"]["pending"],
                    ),
                    args.json,
                )
            elif args.node_command == "set-enabled":
                item = nodes.set_enabled(args.reference, args.value == "on")
                emit({"node": item}, translator.get("node.updated", name=item["name"]), args.json)
            elif args.node_command == "heartbeat":
                item = nodes.heartbeat(args.reference, args.state, args.error, args.latency_ms)
                emit({"node": item}, translator.get("node.updated", name=item["name"]), args.json)
            elif args.node_command == "missed-heartbeat":
                item = nodes.missed_heartbeat(args.reference, args.error)
                emit({"node": item}, translator.get("node.updated", name=item["name"]), args.json)
            elif args.node_command == "maintenance":
                item = nodes.set_maintenance(args.reference, args.value == "on")
                emit({"node": item}, translator.get("node.updated", name=item["name"]), args.json)
            elif args.node_command == "health-policy":
                policy = nodes.set_health_policy(
                    args.failure_threshold, args.recovery_threshold
                )
                emit(
                    {"health_policy": policy},
                    translator.get(
                        "node.health_policy",
                        failure=policy["failure_threshold"],
                        recovery=policy["recovery_threshold"],
                    ),
                    args.json,
                )
            elif args.node_command == "remove":
                if not args.yes:
                    raise ValueError("confirmation_required")
                item = nodes.remove(args.reference)
                emit({"node": item}, translator.get("node.removed", name=item["name"]), args.json)
        elif args.command == "sync":
            if args.sync_command in {"list", "claim", "summary"}:
                node = nodes.find(args.node)
            if args.sync_command == "list":
                operations = [
                    public_operation(item)
                    for item in sync_queue.list(node["id"], args.state)
                ]
                emit({"operations": operations}, translator.get("sync.listed", count=len(operations)), args.json)
            elif args.sync_command == "claim":
                claimed = sync_queue.claim(node["id"], args.limit)
                operations = claimed if args.include_payload else [public_operation(item) for item in claimed]
                emit({"operations": operations}, translator.get("sync.claimed", count=len(operations)), args.json)
            elif args.sync_command == "ack":
                operation = sync_queue.finish(args.operation_id,
                    result=json.loads(args.result) if args.result else None)
                emit({"operation": public_operation(operation)}, translator.get("sync.updated", id=operation["id"], state=operation["state"]), args.json)
            elif args.sync_command == "fail":
                operation = sync_queue.finish(args.operation_id, error=args.error)
                emit({"operation": public_operation(operation)}, translator.get("sync.updated", id=operation["id"], state=operation["state"]), args.json)
            elif args.sync_command == "summary":
                summary = sync_queue.summary(node["id"])
                emit({"sync": summary}, translator.get("sync.summary", pending=summary["states"]["pending"], delivering=summary["states"]["delivering"]), args.json)
        elif args.command == "control":
            pki = ControlPKI(getattr(args, "dir", None) or store.data_dir / "control-pki")
            control_plane = ControlPlaneService(store, pki)
            if args.control_command == "init":
                result = pki.initialize(args.public_host, args.days)
                emit({"control_pki": result}, f"Control plane PKI initialized: {result['ca']}", args.json)
            elif args.control_command == "export-ca":
                pki.initialize_from_existing()
                args.output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(pki.ca_cert, args.output)
                os.chmod(args.output, 0o644)
                emit({"ca": str(args.output)}, f"Control plane CA exported: {args.output}", args.json)
            elif args.control_command == "bootstrap":
                ttl = parse_duration(args.ttl)
                result = control_plane.create_bootstrap(args.node, ttl)
                if args.token_file:
                    args.token_file.parent.mkdir(parents=True, exist_ok=True)
                    args.token_file.write_text(result["token"] + "\n", encoding="utf-8")
                    os.chmod(args.token_file, 0o600)
                    public = dict(result)
                    public.pop("token")
                    public["token_file"] = str(args.token_file)
                    emit({"bootstrap": public}, f"One-time bootstrap token written to {args.token_file}", args.json)
                else:
                    emit({"bootstrap": result}, f"One-time bootstrap token: {result['token']}", args.json)
            elif args.control_command == "serve":
                if not 1 <= args.rate_limit <= 10000:
                    raise ValueError("control_invalid_rate_limit")
                serve_control_plane(control_plane, args.listen, args.port,
                                    max_requests_per_minute=args.rate_limit)
            elif args.control_command == "revoke":
                if not args.yes:
                    raise ValueError("confirmation_required")
                result = control_plane.revoke(args.node)
                emit({"revocation": result}, f"Node trust revoked: {result['node_id']}", args.json)
        elif args.command == "agent":
            if args.agent_command == "enroll":
                token = read_secret_file(args.token_file)
                result = enroll_node_agent(args.master, args.node, token, args.dir, args.ca)
                emit({"agent": result}, f"Node agent enrolled: {result['node_id']}", args.json)
            elif args.agent_command == "run":
                agent_service = NodeAgent(args.config)
                if args.once:
                    result = agent_service.run_once()
                    emit({"agent": result}, json.dumps(result, ensure_ascii=False), args.json)
                else:
                    agent_service.run(args.interval)
            elif args.agent_command == "rotate":
                result = NodeAgent(args.config).rotate()
                emit({"agent": result}, f"Node certificate rotated: {result['node_id']}", args.json)
        elif args.command in {"user", "users"}:
            if args.user_command in {"create", "new"}:
                device_name, location = localized_key_context(store, "default", language)
                user, token = service.create(
                    args.name,
                    default_device_name=device_name,
                    location=location,
                )
                emit(
                    {"user": user, "subscription_token": token},
                    translator.get(
                        "user.created", name=user["name"], id=user["id"],
                        numeric_id=user["numeric_id"],
                    )
                    + "\n"
                    + translator.get("user.secret_once", token=token),
                    args.json,
                )
                if not args.json:
                    print_created_qr(subscriptions, token, translator)
            elif args.user_command == "list":
                users = service.list()
                if args.json:
                    emit({"users": users}, "", True)
                else:
                    print_users(users, translator)
            elif args.user_command == "show":
                user = service.find(args.reference)
                view = user_admin_view(user, keys, subscriptions)
                emit(view, render_user_admin(view, translator), args.json)
            elif args.user_command == "set-enabled":
                user = service.set_enabled(args.reference, args.value == "on")
                state = "enabled" if user["enabled"] else "disabled"
                emit(
                    {"user": user},
                    translator.get("user.updated", name=user["name"], state=translator.get(f"user.state.{state}")),
                    args.json,
                )
            elif args.user_command == "delete-plan":
                plan = service.delete_plan(args.reference, parse_duration(args.grace))
                emit({"plan": plan}, json.dumps(plan, ensure_ascii=False, indent=2), args.json)
            elif args.user_command == "delete":
                if not args.yes:
                    raise ValueError("confirmation_required")
                deletion = service.request_delete(
                    args.reference, parse_duration(args.grace), args.actor
                )
                emit({"deletion": deletion},
                     f"User deletion requested: {deletion['user_id']} ({deletion['state']})",
                     args.json)
            elif args.user_command == "delete-status":
                deletion = service.deletion_status(args.reference)
                emit({"deletion": deletion}, json.dumps(deletion, ensure_ascii=False, indent=2), args.json)
            elif args.user_command == "purge":
                if not args.yes:
                    raise ValueError("confirmation_required")
                deletion = service.purge(args.reference, args.actor)
                emit({"deletion": deletion}, f"User credentials purged: {deletion['user_id']}", args.json)
            elif args.user_command == "rename":
                user = service.rename(args.reference, args.new_name)
                emit(
                    {"user": user},
                    translator.get("user.renamed", name=user["name"]),
                    args.json,
                )
            elif args.user_command == "set-note":
                user = service.set_metadata(args.reference, note=args.note, actor=args.actor)
                emit({"user": user}, translator.get("user.metadata_saved", name=user["name"]), args.json)
            elif args.user_command == "set-tags":
                user = service.set_metadata(
                    args.reference,
                    tags=args.tags.split(","),
                    actor=args.actor,
                )
                emit({"user": user}, translator.get("user.metadata_saved", name=user["name"]), args.json)
            elif args.user_command == "set-alias":
                user = service.set_alias(args.reference, args.alias)
                emit({"user": user}, translator.get("user.metadata_saved", name=user["name"]), args.json)
            elif args.user_command == "set-admin":
                user = service.set_admin_metadata(
                    args.reference,
                    contact=args.contact,
                    source=args.source,
                    external_id=args.external_id,
                    attributes=parse_attributes(args.attribute),
                    clear_attributes=args.clear_attributes,
                    actor=args.actor,
                )
                emit(
                    {"user": user},
                    translator.get("user.metadata_saved", name=user["name"]),
                    args.json,
                )
            elif args.user_command == "search":
                users = service.search(args.query)
                if args.json:
                    emit({"users": users}, "", True)
                else:
                    print_users(users, translator)
        elif args.command == "state":
            if args.state_command == "compact":
                day_values = {
                    "audit_log": args.audit_days,
                    "usage_events": args.usage_days,
                    "ip_observations": args.ip_days,
                    "inbound_versions": args.inbound_days,
                    "node_operations": args.operation_days,
                }
                retention = {key: value for key, value in day_values.items() if value is not None}
                maximum = ({key: args.max_records for key in day_values}
                           if args.max_records is not None else {})
                compacted = StateMaintenanceService(store).compact(
                    dry_run=args.dry_run, retention_days=retention, max_records=maximum
                )
                emit({"compaction": compacted}, json.dumps(compacted, ensure_ascii=False), args.json)
        elif args.command == "system":
            lifecycle = SystemLifecycleService(args.installer)
            if args.system_command == "status":
                result = lifecycle.status()
                emit({"system": result}, json.dumps(result, ensure_ascii=False, indent=2), args.json)
            else:
                result = lifecycle.execute(
                    args.system_command, prefix=args.prefix, dry_run=args.dry_run,
                    confirmed=args.yes, language=language,
                )
                message = result["stdout"]
                if result["stderr"]:
                    message += ("\n" if message else "") + result["stderr"]
                emit({"system": result}, message.rstrip(), args.json)
                if result["returncode"]:
                    return int(result["returncode"])
        elif args.command == "subscription":
            if args.subscription_command == "status":
                status = service.subscription_status(args.user)
                emit(
                    {"subscription": status},
                    translator.get(
                        "subscription.status",
                        user=status["user_id"],
                        state=status["state"],
                    ),
                    args.json,
                )
            elif args.subscription_command == "create":
                user, token = service.create_subscription(args.user, args.actor)
                emit(
                    {"subscription": service.subscription_status(user["id"]), "token": token},
                    translator.get("subscription.created", user=user["id"])
                    + "\n"
                    + translator.get("user.secret_once", token=token),
                    args.json,
                )
                if not args.json:
                    print_created_qr(subscriptions, token, translator)
            elif args.subscription_command == "rotate":
                user, token = service.rotate_subscription(args.user, args.actor)
                emit(
                    {"subscription": service.subscription_status(user["id"]), "token": token},
                    translator.get("subscription.rotated", user=user["id"])
                    + "\n"
                    + translator.get("user.secret_once", token=token),
                    args.json,
                )
                if not args.json:
                    print_created_qr(subscriptions, token, translator)
            elif args.subscription_command == "revoke":
                user = service.revoke_subscription(args.user, args.actor)
                emit(
                    {"subscription": service.subscription_status(user["id"])},
                    translator.get("subscription.revoked", user=user["id"]),
                    args.json,
                )
            elif args.subscription_command == "show":
                user = service.find(args.user)
                if args.json:
                    emit({"subscription": subscriptions.build(user["id"])}, "", True)
                else:
                    print(subscriptions.render(user["id"], args.format), end="")
            elif args.subscription_command == "export":
                user = service.find(args.user)
                destination = subscriptions.export(user["id"], args.format, args.output)
                emit(
                    {"path": str(destination), "format": args.format},
                    translator.get("subscription.exported", path=destination),
                    args.json,
                )
            elif args.subscription_command == "update":
                user = service.find(args.user)
                built = subscriptions.build(user["id"])
                summary = {
                    "user_id": user["id"],
                    "entries": len(built["entries"]),
                    "mtproto": len(built["mtproto"]),
                }
                emit(
                    {"subscription": summary},
                    translator.get(
                        "subscription.updated",
                        entries=summary["entries"],
                        mtproto=summary["mtproto"],
                    ),
                    args.json,
                )
            elif args.subscription_command == "qr":
                user = service.find(args.user)
                token = read_secret_file(args.token_file)
                verified = service.verify_subscription_token(token)
                if verified is None or verified["id"] != user["id"]:
                    raise ValueError("subscription_token_invalid")
                output = subscriptions.qr(token, ascii_mode=args.ascii)
                if args.json:
                    tls_settings = store.load()["settings"].get("subscription_tls", {})
                    private_ca = tls_settings.get("mode") == "private-ca"
                    fingerprint = TLSManager(store).fingerprint() if private_ca else None
                    emit(
                        {
                            "url": subscriptions.url(token),
                            "qr": output,
                            "private_ca": private_ca,
                            "ca_fingerprint_sha256": fingerprint,
                        },
                        "",
                        True,
                    )
                else:
                    print_private_ca_warning(store, translator)
                    print(output, end="")
            elif args.subscription_command == "serve":
                if not args.https_only:
                    raise ValueError("subscription_https_only_required")
                print(
                    translator.get(
                        "subscription.serving", listen=args.listen, port=args.port
                    ),
                    file=sys.stderr,
                )
                serve_https(
                    store=store,
                    listen=args.listen,
                    port=args.port,
                    public_host=args.public_host,
                    cert=args.cert,
                    key=args.key,
                    max_clients=args.max_clients,
                    rate_limit_requests=args.rate_limit,
                    rate_limit_window_seconds=args.rate_window,
                    abuse_failures=args.abuse_failures,
                    abuse_ban_seconds=args.abuse_ban,
                )
        elif args.command == "automation":
            manager = AutomationService(store)
            if args.automation_command == "configure":
                result = manager.configure(enabled=args.value == "on",
                                           interval_seconds=args.interval)
            else:
                result = manager.run(once=args.once, interval_seconds=args.interval)
            emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
        elif args.command == "tls":
            manager = TLSManager(store, getattr(args, "dir", None))
            node_certificates = NodeCertificateService(store)
            if args.tls_command == "node-list":
                result = {"certificates": node_certificates.list(args.node)}
                emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
            elif args.tls_command == "node-issue":
                result = node_certificates.issue(args.node, domains=args.domain, ips=args.ip,
                    days=args.days, renew_percent=args.renew_percent)
                emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
            elif args.tls_command == "node-renew":
                result = node_certificates.renew(args.certificate_id, force=args.force)
                emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
            elif args.tls_command == "node-replace":
                result = node_certificates.replace(args.certificate_id, cert=args.cert, key=args.key,
                    domains=args.domain, ips=args.ip, ca_cert=args.ca, chain=args.chain)
                emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
            elif args.tls_command == "node-revoke":
                if not args.yes:
                    raise ValueError("confirmation_required")
                result = node_certificates.revoke(args.certificate_id)
                emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
            elif args.tls_command == "node-delete":
                if not args.yes:
                    raise ValueError("confirmation_required")
                result = node_certificates.delete(args.certificate_id, force=args.force)
                emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
            elif args.tls_command == "node-assign":
                result = node_certificates.assign(args.certificate_id, args.inbound_id,
                                                  apply=not args.plan)
                emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
            elif args.tls_command == "init-ca":
                status = manager.init_ca()
                emit(
                    {"tls": status},
                    translator.get("tls.ca_ready", fingerprint=status["ca_fingerprint_sha256"]),
                    args.json,
                )
            elif args.tls_command == "issue-self":
                status = manager.issue(
                    domains=args.domain,
                    ips=args.ip,
                    days=args.days,
                    renew_percent=args.renew_percent,
                )
                emit(
                    {"tls": status},
                    translator.get("tls.leaf_ready", cert=status["cert"], key=status["key"]),
                    args.json,
                )
            elif args.tls_command == "status":
                status = manager.status()
                emit({"tls": status}, json.dumps(status, ensure_ascii=False, indent=2), args.json)
            elif args.tls_command == "renew":
                status = manager.renew(args.force)
                emit(
                    {"tls": status},
                    translator.get("tls.renewed", renewed=str(status["renewed"]).lower()),
                    args.json,
                )
            elif args.tls_command in {"acme-issue", "acme-renew", "acme-deploy"}:
                acme = CertbotManager(
                    store, certbot=args.certbot, config_dir=args.config_dir,
                    work_dir=args.work_dir, logs_dir=args.logs_dir,
                    cert_name=args.cert_name, tls_manager=manager,
                )
                if args.tls_command == "acme-issue":
                    status = acme.issue(
                        args.identifier, email=args.email, staging=args.staging,
                        webroot=args.webroot,
                    )
                elif args.tls_command == "acme-renew":
                    status = acme.renew(args.identifier, dry_run=args.dry_run)
                else:
                    status = acme.deploy(args.identifier)
                emit(
                    {"tls": status},
                    translator.get("tls.acme_ready", identifier=status["identifier"]),
                    args.json,
                )
            elif args.tls_command == "export-ca":
                destination = manager.export_ca(args.output)
                install_command = manager.install_command(destination)
                emit(
                    {"path": str(destination), "install_command": install_command},
                    translator.get(
                        "tls.ca_exported", path=destination, command=install_command
                    ),
                    args.json,
                )
            elif args.tls_command == "renew-daemon":
                report = manager.run_renewer(
                    interval_seconds=args.interval,
                    jitter_seconds=args.jitter,
                    once=args.once,
                )
                if args.once:
                    emit(
                        {"tls_renewer": report},
                        translator.get("tls.renewer", state=report["state"]),
                        args.json,
                    )
            elif args.tls_command == "import":
                status = manager.import_leaf(
                    cert=args.cert,
                    key=args.key,
                    domains=args.domain,
                    ips=args.ip,
                    ca_cert=args.ca,
                    chain=args.chain,
                )
                emit(
                    {"tls": status},
                    translator.get("tls.imported", cert=status["cert"], key=status["key"]),
                    args.json,
                )
            elif args.tls_command == "rotate-ca":
                if args.action == "prepare":
                    if args.force:
                        raise ValueError("tls_rotation_force_finalize_only")
                    status = manager.prepare_ca_rotation(args.overlap_days)
                elif args.action == "activate":
                    if args.force:
                        raise ValueError("tls_rotation_force_finalize_only")
                    status = manager.activate_ca_rotation()
                else:
                    status = manager.finalize_ca_rotation(force=args.force)
                rotation = status["ca_rotation"]
                emit(
                    {"tls": status},
                    translator.get("tls.rotation", state=rotation["state"]),
                    args.json,
                )
            elif args.tls_command == "sync-ip":
                status = manager.reconcile_ips(args.ip or None)
                emit(
                    {"tls": status},
                    translator.get(
                        "tls.ip_synced",
                        changed=str(status["ip_changed"]).lower(),
                        ips=", ".join(status["actual_ips"]),
                        url_updated=str(status["url_updated"]).lower(),
                    ),
                    args.json,
                )
            elif args.tls_command == "backup":
                result = manager.backup(args.output, args.passphrase_file)
                manifest = result["manifest"]
                emit(
                    {
                        "tls_backup": {
                            "path": result["path"],
                            "ca_fingerprint_sha256": manifest["ca_fingerprint_sha256"],
                            "file_count": len(manifest["files"]),
                        }
                    },
                    translator.get("tls.backup_created", path=result["path"]),
                    args.json,
                )
            elif args.tls_command == "verify-backup":
                manifest = manager.verify_backup(args.input, args.passphrase_file)
                emit(
                    {"tls_backup": {"valid": True, "manifest": manifest}},
                    translator.get(
                        "tls.backup_verified", fingerprint=manifest["ca_fingerprint_sha256"]
                    ),
                    args.json,
                )
        elif args.command in {"key", "keys"}:
            if args.key_command == "create":
                user = service.find(args.user)
                device_name, location = localized_key_context(store, args.device, language)
                access_key = keys.create(user["id"], device_name, location)
                emit(
                    {"access_key": public_access_key(access_key, show_secrets=True)},
                    translator.get(
                        "access_key.created",
                        id=access_key["id"],
                        device=access_key["device_name"],
                    ),
                    args.json,
                )
            elif args.key_command == "list":
                user = service.find(args.user)
                access_keys = [
                    public_access_key(item, show_secrets=args.full)
                    for item in keys.list(user["id"])
                ]
                if args.json:
                    emit({"access_keys": access_keys}, "", True)
                elif args.full:
                    print(json.dumps(access_keys, ensure_ascii=False, indent=2))
                else:
                    for item in access_keys:
                        print(f"{item['id']}  {item['device_name']}  {item['state']}")
            elif args.key_command == "show":
                access_key = keys.find(args.key_id)
                visible = public_access_key(
                    access_key,
                    show_secrets=args.show_secrets or not args.redact,
                )
                emit(
                    {"access_key": visible},
                    json.dumps(visible, ensure_ascii=False, indent=2),
                    args.json,
                )
            elif args.key_command == "disable":
                access_key = keys.set_state(args.key_id, "disabled", args.reason)
                emit(
                    {"access_key": public_access_key(access_key)},
                    translator.get("access_key.updated", id=access_key["id"], state="disabled"),
                    args.json,
                )
            elif args.key_command == "enable":
                access_key = keys.set_state(args.key_id, "active")
                emit(
                    {"access_key": public_access_key(access_key)},
                    translator.get("access_key.updated", id=access_key["id"], state="active"),
                    args.json,
                )
            elif args.key_command == "revoke":
                access_key = keys.set_state(args.key_id, "revoked", "manual")
                emit(
                    {"access_key": public_access_key(access_key)},
                    translator.get("access_key.updated", id=access_key["id"], state="revoked"),
                    args.json,
                )
            elif args.key_command == "set-limit":
                limit_values = (
                    args.traffic, args.upload, args.download, args.duration,
                    args.expires_at, args.starts, args.ips, args.ip_window,
                    args.ip_action, args.trusted_network,
                )
                if args.unlimited:
                    if any(value is not None for value in limit_values):
                        raise ValueError("access_key_unlimited_conflict")
                    access_key = keys.clear_limits(args.key_id)
                else:
                    access_key = keys.set_limits(
                        args.key_id,
                        total_bytes=parse_size(args.traffic),
                        upload_bytes=parse_size(args.upload),
                        download_bytes=parse_size(args.download),
                        max_ips=args.ips,
                        duration_seconds=parse_duration(args.duration) if args.duration else None,
                        expires_at=args.expires_at,
                        starts=args.starts,
                        ip_window_seconds=parse_duration(args.ip_window) if args.ip_window else None,
                        ip_action=args.ip_action,
                        trusted_networks=args.trusted_network,
                    )
                emit(
                    {"access_key": public_access_key(access_key)},
                    translator.get("access_key.limits_saved", id=access_key["id"]),
                    args.json,
                )
            elif args.key_command == "extend":
                access_key = keys.extend(args.key_id, parse_duration(args.duration))
                emit(
                    {"access_key": public_access_key(access_key)},
                    translator.get("access_key.limits_saved", id=access_key["id"]),
                    args.json,
                )
            elif args.key_command == "reset-traffic":
                access_key = keys.reset_traffic(args.key_id)
                emit(
                    {"access_key": public_access_key(access_key)},
                    translator.get("access_key.traffic_reset", id=access_key["id"]),
                    args.json,
                )
            elif args.key_command == "record-usage":
                access_key, duplicate = keys.record_usage(
                    args.key_id,
                    source_id=args.source,
                    event_id=args.event_id,
                    upload_bytes=args.upload_bytes,
                    download_bytes=args.download_bytes,
                    connected=args.connected,
                    client_ip=args.client_ip,
                )
                emit(
                    {"access_key": public_access_key(access_key), "duplicate": duplicate},
                    translator.get(
                        "access_key.usage_recorded",
                        id=access_key["id"],
                        duplicate=str(duplicate).lower(),
                    ),
                    args.json,
                )
            elif args.key_command == "usage":
                if args.usage_key:
                    usage = keys.usage(args.usage_key)
                else:
                    user = service.find(args.usage_user)
                    usage = keys.user_usage(user["id"])
                emit({"usage": usage}, json.dumps(usage, ensure_ascii=False, indent=2), args.json)
            elif args.key_command == "ips":
                result = keys.active_ips(args.key_id)
                emit(
                    {"ip_usage": result},
                    translator.get("access_key.ips", count=result["count"]),
                    args.json,
                )
            elif args.key_command == "clear-ips":
                result = keys.clear_ips(args.key_id)
                emit(
                    {"ip_usage": result},
                    translator.get("access_key.ips_cleared", id=args.key_id),
                    args.json,
                )
        elif args.command == "audit" and args.audit_command == "list":
            events = service.audit_log(args.limit)
            if args.json:
                emit({"events": events}, "", True)
            else:
                for event in events:
                    print(
                        f"{event['timestamp']}  {event['actor']}  {event['action']}  "
                        f"{event['object_type']}:{event['object_id']}  {','.join(event['fields'])}"
                    )
        elif args.command == "newUser":
            device_name, location = localized_key_context(store, "default", language)
            user, token = service.create(
                args.name,
                default_device_name=device_name,
                location=location,
            )
            emit(
                {"user": user, "subscription_token": token},
                translator.get("user.created", name=user["name"], id=user["id"])
                + "\n"
                + translator.get("user.secret_once", token=token),
                args.json,
            )
        elif args.command == "status":
            count = len(service.list())
            cores = core_health()
            overall = overall_health(cores)
            if args.json:
                emit({"status": overall, "users": count, "cores": cores}, "", True)
            else:
                if overall == "degraded":
                    print(translator.get("status.degraded", count=count))
                else:
                    print(translator.get("status.ready", count=count))
                for core, health in cores.items():
                    print(translator.get(
                        "status.core", core=core, state=health["state"],
                        protocol=health["protocol"]["state"],
                    ))
        elif args.command in {"inbound", "inbounds"}:
            def inbound_node(reference: Optional[str]) -> Optional[str]:
                if reference is None or reference in {"local", "standalone"}:
                    return reference
                return nodes.find(reference)["id"]

            node_id = inbound_node(getattr(args, "node", None))
            if args.inbound_command == "import":
                document = load_document(args.file)
                configs = document.get("inbounds") if isinstance(document.get("inbounds"), list) else [document]
                if not 0 <= args.index < len(configs) or not isinstance(configs[args.index], dict):
                    raise ValueError("inbound_invalid_index")
                item = inbounds.register(node_id or "local", configs[args.index], remark=args.remark,
                                         inbound_id=args.id, config_index=args.index if "inbounds" in document else None,
                                         api_endpoint=args.api_endpoint)
                emit({"inbound": item}, f"Imported inbound {item['node_id']}:{item['id']}", args.json)
            elif args.inbound_command == "list":
                items = inbounds.list(node_id=node_id, core=args.core, protocol=args.protocol,
                                      port=args.port, state=args.state, remark=args.remark)
                if args.json:
                    emit({"inbounds": items}, "", True)
                else:
                    for item in items:
                        print(f"{item['node_id']}:{item['id']}  {item['state']}  {item['protocol']}  {item['listen']}:{item['port']}  {item['remark']}")
            elif args.inbound_command == "show":
                item = inbounds.show(args.inbound_id, node_id, secrets=args.show_secrets)
                emit({"inbound": item}, json.dumps(item, ensure_ascii=False, indent=2), args.json)
            elif args.inbound_command == "wss-tls":
                result = inbounds.guided_wss_tls(
                    node_id=node_id or "local", port=args.port,
                    public_address=args.public_address, sni=args.sni,
                    http_host=args.http_host, path=args.path, alpn=args.alpn,
                    certificate={"certificateFile": str(args.cert), "keyFile": str(args.key)},
                    remark=args.remark,
                )
                emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
            elif args.inbound_command == "probe-port":
                result = inbounds.probe_port(node_id or "local", listen=args.listen,
                                             port=args.port, transport=args.transport)
                emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
            elif args.inbound_command == "rotation-plan":
                result = inbounds.rotation_plan(args.inbound_id, node_id=node_id)
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    descriptor, filename = tempfile.mkstemp(prefix=".rotation-", dir=args.output.parent)
                    temporary = Path(filename)
                    try:
                        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                            json.dump(result, handle, ensure_ascii=False, indent=2)
                            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
                        os.chmod(temporary, 0o600); os.replace(temporary, args.output)
                    finally:
                        temporary.unlink(missing_ok=True)
                emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
            elif args.inbound_command == "rotation-apply":
                if not args.yes:
                    raise ValueError("inbound_confirmation_required")
                plan_text = read_secret_file(args.plan_file)
                try:
                    plan = json.loads(plan_text)
                except json.JSONDecodeError as error:
                    raise ValueError("inbound_invalid_document") from error
                config_root = Path(os.environ.get("XRAYDO_CONFIG", "/etc/xraydo/config.json")).parent
                binary = args.xray_binary or shutil.which("xray")
                if plan.get("node_id") == "local" and not binary:
                    raise ValueError("xray_not_installed")
                result = inbounds.rotate_reality(plan, confirm=True, binary=binary,
                    config_path=args.config_path or config_root / "cores" / "xray" / "config.json")
                emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
            elif args.inbound_command == "edit":
                current = inbounds.show(args.inbound_id, node_id, secrets=True)
                proposed = inbound_edit_document(current["config"], args)
                managed_certificate = None
                if args.tls_issue_domain or args.tls_issue_ip:
                    managed_certificate = TLSManager(store).issue(
                        domains=args.tls_issue_domain, ips=args.tls_issue_ip
                    )
                elif args.tls_managed:
                    managed_certificate = TLSManager(store).status()
                    if not managed_certificate.get("leaf_ready"):
                        raise ValueError("tls_leaf_missing")
                if managed_certificate:
                    args.tls_cert = Path(managed_certificate["cert"])
                    args.tls_key = Path(managed_certificate["key"])
                if bool(args.tls_cert) != bool(args.tls_key):
                    raise ValueError("inbound_certificate_required")
                if args.tls_cert and args.tls_key:
                    proposed = apply_assignments(proposed, [
                        "streamSettings.security=tls",
                        "streamSettings.tlsSettings.certificates=" + json.dumps([{
                            "certificateFile": str(args.tls_cert), "keyFile": str(args.tls_key)
                        }]),
                    ])
                if args.apply and not args.yes:
                    preview = inbounds.edit(args.inbound_id, proposed, node_id=node_id,
                                            expected_revision=args.expected_revision, remark=args.remark,
                                            fingerprint=args.fingerprint)
                    if args.json or not sys.stdin.isatty():
                        raise ValueError("inbound_confirmation_required")
                    print_inbound_plan(preview)
                    if input("Apply these changes? [y/N]: ").strip().casefold() not in {"y", "yes"}:
                        raise ValueError("inbound_cancelled")
                config_root = Path(os.environ.get("XRAYDO_CONFIG", "/etc/xraydo/config.json")).parent
                binary = args.xray_binary or shutil.which("xray")
                if args.apply and node_id in {None, "local"} and not binary:
                    raise ValueError("xray_not_installed")
                result = inbounds.edit(
                    args.inbound_id, proposed, node_id=node_id,
                    expected_revision=args.expected_revision, remark=args.remark,
                    fingerprint=args.fingerprint,
                    apply=args.apply, binary=binary,
                    config_path=args.config_path or config_root / "cores" / "xray" / "config.json",
                )
                if args.json:
                    emit(result, "", True)
                else:
                    print_inbound_plan(result)
                    print(f"State: {result['state']}")
            elif args.inbound_command == "clone":
                item = inbounds.clone(args.inbound_id, node_id or "local", node_id=inbound_node(args.source_node),
                                      port=args.port, remark=args.remark)
                emit({"inbound": item}, f"Cloned inbound {item['node_id']}:{item['id']}", args.json)
            elif args.inbound_command in {"enable", "disable", "delete"}:
                state = {"enable": "enabled", "disable": "disabled", "delete": "deleted"}[args.inbound_command]
                if args.apply and not args.yes:
                    raise ValueError("inbound_confirmation_required")
                config_root = Path(os.environ.get("XRAYDO_CONFIG", "/etc/xraydo/config.json")).parent
                binary = args.xray_binary or shutil.which("xray")
                if args.apply and node_id in {None, "local"} and not binary:
                    raise ValueError("xray_not_installed")
                item = inbounds.set_state(args.inbound_id, state, node_id=node_id,
                                          expected_revision=args.expected_revision,
                                          grace_seconds=getattr(args, "grace", 86400),
                                          apply=args.apply, binary=binary,
                                          config_path=args.config_path or config_root / "cores" / "xray" / "config.json")
                emit({"inbound": item}, f"Inbound state: {item['state']}", args.json)
            elif args.inbound_command == "restore":
                if args.apply and not args.yes:
                    raise ValueError("inbound_confirmation_required")
                config_root = Path(os.environ.get("XRAYDO_CONFIG", "/etc/xraydo/config.json")).parent
                binary = args.xray_binary or shutil.which("xray")
                if args.apply and node_id in {None, "local"} and not binary:
                    raise ValueError("xray_not_installed")
                item = inbounds.restore(args.inbound_id, node_id=node_id, apply=args.apply,
                                        binary=binary,
                                        config_path=args.config_path or config_root / "cores" / "xray" / "config.json")
                emit({"inbound": item}, f"Restored inbound as {item['state']}", args.json)
            elif args.inbound_command == "history":
                history = inbounds.history(args.inbound_id, node_id=node_id)
                emit({"history": history}, json.dumps(history, ensure_ascii=False, indent=2), args.json)
            elif args.inbound_command == "rollback":
                if args.apply and not args.yes:
                    raise ValueError("inbound_confirmation_required")
                config_root = Path(os.environ.get("XRAYDO_CONFIG", "/etc/xraydo/config.json")).parent
                binary = args.xray_binary or shutil.which("xray")
                if args.apply and node_id in {None, "local"} and not binary:
                    raise ValueError("xray_not_installed")
                result = inbounds.rollback(args.inbound_id, args.revision, node_id=node_id,
                                           expected_revision=args.expected_revision,
                                           apply=args.apply, binary=binary,
                                           config_path=args.config_path or config_root / "cores" / "xray" / "config.json")
                if args.json:
                    emit(result, "", True)
                else:
                    print_inbound_plan(result)
            elif args.inbound_command == "bulk-edit":
                targets = []
                for target in args.target:
                    node_reference, separator, inbound_id = target.partition(":")
                    if not separator:
                        raise ValueError("inbound_invalid_assignment")
                    targets.append((inbound_node(node_reference) or "local", inbound_id))
                report = inbounds.bulk_plan(
                    targets, lambda config: apply_assignments(config, args.assignments)
                )
                emit(report, json.dumps(report, ensure_ascii=False, indent=2), args.json)
        elif args.command == "reality":
            if args.reality_command == "utls":
                result = fingerprint_profiles(shutil.which("xray"))
                if args.json:
                    emit(result, "", True)
                else:
                    print("Fixed profiles:")
                    for item in result["profiles"]:
                        if item["group"] == "fixed":
                            suffix = " (recommended)" if item["recommended"] else ""
                            if not item["supported"]:
                                suffix += " (unsupported by REALITY)"
                            print(f"  {item['name']}{suffix}: {item['explanation']}")
                    print("Randomization:")
                    for item in result["profiles"]:
                        if item["group"] == "randomization":
                            print(f"  {item['name']}: {item['explanation']}")
                return 0
            is_local = args.node in {"local", "standalone"}
            node_id = "local"
            if not is_local:
                node = nodes.find(args.node)
                node_id = node["id"]
                data = store.load()
                payload = {
                    key: value for key, value in vars(args).items()
                    if key not in {"lang", "json", "command", "reality_command"}
                    and value is not None
                }
                payload = json.loads(json.dumps(payload, default=str))
                operation = enqueue_node_operation(
                    data, node_id, "reality", args.reality_command, payload
                )
                store.save(data)
                emit(
                    {"state": "pending_node_execution", "operation": public_operation(operation)},
                    f"REALITY {args.reality_command} queued for node {node_id}",
                    args.json,
                )
                return 0
            if args.reality_command == "scan":
                exact = f"{args.target}/{args.sni or args.target}"
                if args.allow_private_single and args.private_confirmation != exact:
                    raise ValueError("private_confirmation_required")
                result = scan_result(
                    args.target, args.port, args.timeout, sni=args.sni, xver=args.xver,
                    allow_private=args.allow_private_single, node_id=node_id,
                )
                current = store.load()
                current.setdefault("reality_checks", []).append({
                    key: result.get(key) for key in (
                        "checked_at", "node_id", "target", "resolved_ip",
                        "latency_ms", "suitable", "reason"
                    )
                })
                if args.allow_private_single:
                    current.setdefault("audit_log", []).append({
                        "id": __import__("uuid").uuid4().hex,
                        "timestamp": result["checked_at"], "actor": "local-admin",
                        "object_type": "reality_target", "object_id": exact,
                        "action": "reality.private_target_allowed", "fields": ["target", "sni"],
                    })
                store.save(current)
                emit({"result": result}, json.dumps(result, ensure_ascii=False, indent=2), args.json)
            elif args.reality_command == "discover":
                report = discover_targets(
                    args.targets, sni=args.sni, xver=args.xver, timeout=args.timeout,
                    concurrency=args.concurrency, node_id=node_id,
                    max_tasks=args.max_tasks, max_cidr_addresses=args.max_cidr_addresses,
                )
                if args.json:
                    emit(report, "", True)
                else:
                    print("Recommended (verified):")
                    for index, item in enumerate(report["recommended"], 1):
                        print(f"  {index}) {item['target']}  SNI={item['sni']}  {item['latency_ms']} ms  SAN={','.join(item['sans'])}")
                    print("Rejected:")
                    for item in report["rejected"]:
                        print(f"  {item['target']}  reason={item['reason']}")
            elif args.reality_command == "configure":
                binary = shutil.which("xray")
                if binary is None:
                    raise ValueError("xray_not_installed")
                config_root = Path(os.environ.get("XRAYDO_CONFIG", "/etc/xraydo/config.json")).parent
                user = service.find(args.user)
                access_key = keys.default_for_user(user["id"], args.access_key)
                selected_target, selected_sni = args.target, args.sni
                if args.interactive:
                    if args.json or not sys.stdin.isatty():
                        raise ValueError("reality_interactive_tty_required")
                    candidates = args.candidate or [
                        f"{item['host']}:{item.get('port', 443)}"
                        for item in json.loads(
                            ((Path(__file__).with_name("data") / "reality_targets.json").read_text(encoding="utf-8"))
                        )
                    ]
                    report = discover_targets(candidates, xver=args.xver, node_id=node_id)
                    if not report["recommended"]:
                        raise ValueError("no_verified_target")
                    print("Verified REALITY targets:")
                    for index, item in enumerate(report["recommended"], 1):
                        print(f"  {index}) {item['target']}  {item['latency_ms']} ms  SNI={item['sni']}")
                    raw_choice = input("Target [1]: ").strip() or "1"
                    if not raw_choice.isdigit() or not 1 <= int(raw_choice) <= len(report["recommended"]):
                        raise ValueError("invalid_target")
                    selected = report["recommended"][int(raw_choice) - 1]
                    sans = [item for item in selected.get("sans", []) if "*" not in item]
                    if sans:
                        print("Confirmed SNI/SAN:")
                        for index, item in enumerate(sans, 1):
                            print(f"  {index}) {item}")
                        raw_sni = input("SNI [1]: ").strip() or "1"
                        if not raw_sni.isdigit() or not 1 <= int(raw_sni) <= len(sans):
                            raise ValueError("sni")
                        selected_sni = sans[int(raw_sni) - 1]
                    selected_target = selected["host"]
                    args.target_port = selected["port"]
                endpoint = configure_reality(
                    binary=binary, destination=args.config or config_root / "cores" / "xray" / "config.json",
                    user=user_for_access_key(user, access_key), target_host=selected_target,
                    target_port=args.target_port, listen_port=args.listen_port,
                    fingerprint=args.fingerprint, sni=selected_sni, xver=args.xver,
                    server=args.server, node_id=node_id,
                    force_unverified_target=args.force_unverified_target,
                    force_confirmation=args.force_confirmation,
                    allow_private_single=args.allow_private_single,
                    private_confirmation=args.private_confirmation, store=store,
                )
                endpoint["access_key_id"] = access_key["id"]
                keys.set_endpoint(access_key["id"], "xray_reality", endpoint)
                emit({"endpoint": endpoint}, endpoint["uri"], args.json)
        elif args.command == "hysteria":
            manager = HysteriaManager(store)
            command_name = args.hysteria_command
            if command_name in {"install", "update"}:
                result = manager.install_many(args.node, version=args.version,
                                              binary_path=args.binary_path)
            elif command_name == "check-update":
                result = manager.check_updates(args.node)
            elif command_name == "rollback":
                result = manager.rollback(args.node, args.version, args.binary_path)
            elif command_name == "version":
                result = manager.versions(args.node)
            elif command_name == "capabilities":
                result = manager.capabilities(args.node)
            elif command_name == "diagnostics":
                result = manager.diagnostics(args.node)
            elif command_name == "revoke-key":
                result = manager.revoke_key(args.key_id, args.node)
            elif command_name == "collect-stats":
                result = manager.collect_usage(args.node, args.listen, args.secret, clear=args.clear)
            elif command_name == "backup":
                result = manager.backup(args.node, args.config, args.output)
            else:
                result = manager.service_many(command_name, args.node, getattr(args, "lines", 100))
            emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
        elif args.command == "telemt":
            manager = TelemtManager(store)
            command_name = args.telemt_command
            if command_name == "build":
                result = manager.build(args.build_ref, target=args.target, profile=args.profile)
            elif command_name in {"install", "update"}:
                result = manager.install_many(args.node, version=args.version,
                    artifact_id=args.artifact, binary_path=args.binary_path)
            elif command_name == "version":
                result = manager.versions(args.node)
            elif command_name == "check-update":
                result = manager.check_updates(args.node)
            elif command_name == "rollback":
                result = manager.rollback(args.node, binary_path=args.binary_path)
            elif command_name == "user":
                user = service.find(args.user)
                access_key = keys.default_for_user(user["id"], args.access_key)
                action = "upsert" if args.telemt_user_command == "add" else "delete"
                result = manager.sync_key(access_key["id"], args.node, action, args.mode)
            elif command_name == "link":
                user = service.find(args.user)
                subscription = SubscriptionService(store).build(user["id"])
                selected_node = (None if args.node is None else
                                 ("local" if args.node in {"local", "standalone"} else nodes.find(args.node)["id"]))
                links = [item for item in subscription["mtproto"]
                         if selected_node is None or item["node_id"] == selected_node]
                result = {"user_id": user["id"], "links": links}
            elif command_name == "diagnostics":
                result = manager.diagnostics(args.node, listen=args.api_listen,
                                             auth_header=args.api_auth)
            elif command_name == "collect-stats":
                result = manager.collect_usage(args.node, args.api_listen, args.api_auth)
            elif command_name == "backup":
                result = manager.backup(args.node, args.config, args.output)
            else:
                result = manager.service_many(command_name, args.node, getattr(args, "lines", 100))
            emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
        elif args.command == "xray":
            manager = XrayManager(store)
            command_name = args.xray_command
            if command_name in {"install", "update"}:
                if args.node == "all":
                    result = manager.install_many("all", version=args.version,
                        canary=args.canary, max_unavailable=args.max_unavailable)
                else:
                    result = manager.install(args.node, version=args.version,
                                             binary_path=args.binary_path)
            elif command_name == "version":
                result = manager.versions(args.node)
            elif command_name == "history":
                result = {"history": manager.history(args.node)}
            elif command_name == "check-update":
                result = manager.check_updates(args.node)
            elif command_name == "rollback":
                result = manager.rollback(args.node, version=args.version,
                                          binary_path=args.binary_path)
            else:
                result = manager.backup(args.node, args.config, args.output)
            emit(result, json.dumps(result, ensure_ascii=False, indent=2), args.json)
        elif args.command == "core" and args.core_command == "reality":
            binary = shutil.which("xray")
            if binary is None:
                raise ValueError("xray_not_installed")
            config_root = Path(os.environ.get("XRAYDO_CONFIG", "/etc/xraydo/config.json")).parent
            destination = args.config or config_root / "cores" / "xray" / "config.json"
            user = service.find(args.user)
            access_key = keys.default_for_user(user["id"], args.access_key)
            runtime_user = user_for_access_key(user, access_key)
            endpoint = configure_reality(
                binary=binary,
                destination=destination,
                user=runtime_user,
                target_host=args.target,
                target_port=args.target_port,
                listen_port=args.listen_port,
                fingerprint=args.fingerprint,
                sni=args.sni,
                xver=args.xver,
                server=args.server,
                store=store,
            )
            endpoint["access_key_id"] = access_key["id"]
            keys.set_endpoint(access_key["id"], "xray_reality", endpoint)
            emit(
                {"endpoint": endpoint},
                translator.get("reality.configured", path=endpoint["config"]),
                args.json,
            )
        elif args.command == "core" and args.core_command == "hysteria":
            target_node = "local" if args.node in {"local", "standalone"} else nodes.find(args.node)["id"]
            if target_node != "local":
                user = service.find(args.user)
                access_key = keys.default_for_user(user["id"], args.access_key)
                data = store.load()
                operation = enqueue_node_operation(data, target_node, "hysteria_inbound", "configure", {
                    "access_key_id": access_key["id"], "server": args.server, "port": args.port,
                    "stats_port": args.stats_port, "listen": args.listen,
                    "bandwidth_up": args.bandwidth_up, "bandwidth_down": args.bandwidth_down,
                    "congestion": args.congestion, "ignore_client_bandwidth": args.ignore_client_bandwidth,
                    "disable_udp": args.disable_udp, "udp_idle_timeout": args.udp_idle_timeout,
                    "obfs": args.obfs, "masquerade": args.masquerade,
                    "tls_managed": args.tls_managed, "acme_domains": args.acme_domain,
                    "acme_email": args.acme_email, "acme_ca": args.acme_ca,
                    "acme_type": args.acme_type, "acme_dir": args.acme_dir,
                    "ech_key": str(args.ech_key) if args.ech_key else None,
                    "ech_config": args.ech_config, "allow_insecure": args.insecure,
                    "publish_endpoint_after_ack": True,
                }, resource_id=access_key["id"])
                store.save(data)
                HysteriaManager(store).audit_configuration(target_node, access_key["id"], queued=True)
                emit({"state": "pending_node_execution", "operation": public_operation(operation)},
                     f"Hysteria configuration queued for node {target_node}", args.json)
                return 0
            binary = shutil.which("hysteria")
            if binary is None:
                raise ValueError("hysteria_not_installed")
            config_root = Path(os.environ.get("XRAYDO_CONFIG", "/etc/xraydo/config.json")).parent
            destination = args.config or config_root / "cores" / "hysteria" / "config.yaml"
            pki_dir = destination.parent / "pki"
            user = service.find(args.user)
            access_key = keys.default_for_user(user["id"], args.access_key)
            runtime_user = user_for_access_key(user, access_key)
            cert, key = args.cert, args.key
            if args.tls_managed:
                managed_tls = TLSManager(store).status()
                if not managed_tls.get("leaf_ready"):
                    raise ValueError("tls_leaf_missing")
                cert, key = Path(managed_tls["cert"]), Path(managed_tls["key"])
            acme = None
            if args.acme_domain:
                acme = {key: value for key, value in {
                    "domains": args.acme_domain, "email": args.acme_email,
                    "ca": args.acme_ca, "type": args.acme_type, "dir": args.acme_dir,
                }.items() if value is not None}
            all_users = {
                "k_" + item["id"].replace("-", ""): item["credentials"]["hysteria_password"]
                for item in store.load().get("access_keys", []) if item.get("state") == "active"
            }
            endpoint = configure_hysteria(
                binary=binary,
                destination=destination,
                pki_dir=pki_dir,
                user=runtime_user,
                server=args.server,
                port=args.port,
                stats_port=args.stats_port,
                cert=cert,
                key=key,
                masquerade=args.masquerade,
                obfs_type=args.obfs,
                listen=args.listen, bandwidth_up=args.bandwidth_up,
                bandwidth_down=args.bandwidth_down, congestion=args.congestion,
                ignore_client_bandwidth=args.ignore_client_bandwidth,
                disable_udp=args.disable_udp, udp_idle_timeout=args.udp_idle_timeout,
                acme=acme, ech_key_path=args.ech_key, ech_config=args.ech_config,
                existing_users=all_users, allow_insecure=args.insecure,
                remark=args.remark,
            )
            endpoint["access_key_id"] = access_key["id"]
            endpoint["node_id"] = target_node
            keys.set_endpoint(access_key["id"], "hysteria2", endpoint)
            HysteriaManager(store).audit_configuration(target_node, access_key["id"], queued=False)
            message = translator.get("hysteria.configured", path=endpoint["config"])
            message += "\n" + translator.get("hysteria.uri", uri=endpoint["uri"])
            if endpoint["ca_cert"]:
                message += "\n" + translator.get("hysteria.ca", path=endpoint["ca_cert"])
            emit({"endpoint": endpoint}, message, args.json)
        elif args.command == "core" and args.core_command == "telemt":
            target_node = "local" if args.node in {"local", "standalone"} else nodes.find(args.node)["id"]
            user = service.find(args.user)
            access_key = keys.default_for_user(user["id"], args.access_key)
            if target_node != "local":
                data = store.load()
                operation = enqueue_node_operation(data, target_node, "telemt_inbound", "configure", {
                    "access_key_id": access_key["id"], "server": args.server, "port": args.port,
                    "api_port": args.api_port, "tls_domain": args.tls_domain, "mode": args.mode,
                    "ad_tag": args.ad_tag, "listener_ips": args.listen_ip,
                    "ipv4": args.ipv4, "ipv6": args.ipv6, "prefer_ip": args.prefer_ip,
                    "use_middle_proxy": args.middle_proxy,
                    "timeouts": {"first_byte": args.first_byte_timeout,
                                 "handshake": args.handshake_timeout,
                                 "keepalive": args.keepalive, "ack": args.ack_timeout},
                    "mask_host": args.mask_host, "mask_port": args.mask_port,
                    "unknown_sni_action": args.unknown_sni_action,
                    "api_preferred": True, "atomic_config_fallback": True,
                    "publish_links_after_ack": True,
                }, resource_id=access_key["id"])
                store.save(data)
                TelemtManager(store).audit_configuration(target_node, access_key["id"], queued=True)
                emit({"state": "pending_node_execution", "operation": public_operation(operation)},
                     f"Telemt configuration queued for node {target_node}", args.json)
                return 0
            binary = shutil.which("telemt")
            if binary is None:
                raise ValueError("telemt_not_installed")
            config_root = Path(os.environ.get("XRAYDO_CONFIG", "/etc/xraydo/config.json")).parent
            destination = args.config or config_root / "cores" / "telemt" / "telemt.toml"
            data_dir = destination.parent / "runtime" if args.config else Path("/var/lib/xraydo/telemt")
            runtime_user = user_for_access_key(user, access_key)
            all_users = {
                "k_" + item["id"].replace("-", ""): item["credentials"]["telemt_secret"]
                for item in store.load().get("access_keys", []) if item.get("state") == "active"
            }
            endpoint = configure_telemt(
                binary=binary,
                destination=destination,
                data_dir=data_dir,
                user=runtime_user,
                public_host=args.server,
                port=args.port,
                api_port=args.api_port,
                tls_domain=args.tls_domain,
                mode=args.mode,
                ad_tag=args.ad_tag,
                listener_ips=args.listen_ip,
                ipv4=args.ipv4,
                ipv6=args.ipv6,
                prefer_ip=args.prefer_ip,
                use_middle_proxy=args.middle_proxy,
                first_byte_timeout=args.first_byte_timeout,
                handshake_timeout=args.handshake_timeout,
                keepalive=args.keepalive,
                ack_timeout=args.ack_timeout,
                existing_users=all_users, mask_host=args.mask_host, mask_port=args.mask_port,
                unknown_sni_action=args.unknown_sni_action,
            )
            endpoint["access_key_id"] = access_key["id"]
            endpoint["node_id"] = target_node
            keys.set_endpoint(access_key["id"], "telemt", endpoint)
            TelemtManager(store).audit_configuration(target_node, access_key["id"], queued=False)
            message = translator.get("telemt.configured", path=endpoint["config"])
            message += "\n" + translator.get("telemt.tg_uri", uri=endpoint["tg_uri"])
            message += "\n" + translator.get("telemt.https_uri", uri=endpoint["https_uri"])
            emit({"endpoint": endpoint}, message, args.json)
        return 0
    except ValueError as error:
        code = str(error)
        if args.json:
            print(json.dumps({"error": {"code": code, "message": translator.get(f"error.{code}")}}, ensure_ascii=False), file=sys.stderr)
        else:
            print(render_error(translator.get(f"error.{code}"), stream=sys.stderr), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
