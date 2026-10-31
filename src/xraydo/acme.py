from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from .store import Store
from .tls_manager import TLSManager


IP_CERTBOT_MINIMUM = (5, 4, 0)
CERT_NAME = "xraydo-subscription"


def _certbot_version(value: str) -> tuple[int, int, int]:
    match = re.search(r"(?:certbot\s+)?(\d+)\.(\d+)(?:\.(\d+))?", value)
    if match is None:
        raise ValueError("acme_certbot_version_invalid")
    return tuple(int(item or 0) for item in match.groups())  # type: ignore[return-value]


def _identifier(value: str) -> tuple[str, bool]:
    value = value.strip().rstrip(".")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        try:
            domain = value.encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise ValueError("acme_identifier_invalid") from error
        if (len(domain) > 253 or "." not in domain
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in domain.split("."))):
            raise ValueError("acme_identifier_invalid")
        return domain, False
    if not address.is_global:
        raise ValueError("acme_ip_not_public")
    return str(address), True


class CertbotManager:
    def __init__(
        self,
        store: Store,
        *,
        certbot: Path = Path("certbot"),
        config_dir: Path = Path("/etc/letsencrypt"),
        work_dir: Path = Path("/var/lib/letsencrypt"),
        logs_dir: Path = Path("/var/log/letsencrypt"),
        cert_name: str = CERT_NAME,
        tls_manager: Optional[TLSManager] = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", cert_name):
            raise ValueError("acme_cert_name_invalid")
        self.store = store
        self.certbot = certbot
        self.config_dir = config_dir
        self.work_dir = work_dir
        self.logs_dir = logs_dir
        self.cert_name = cert_name
        self.tls = tls_manager or TLSManager(store)

    def version(self) -> tuple[int, int, int]:
        try:
            completed = subprocess.run(
                [str(self.certbot), "--version"], check=True, text=True,
                capture_output=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("acme_certbot_unavailable") from error
        return _certbot_version(completed.stdout + completed.stderr)

    def _directories(self) -> list[str]:
        return [
            "--config-dir", str(self.config_dir),
            "--work-dir", str(self.work_dir),
            "--logs-dir", str(self.logs_dir),
        ]

    def issue(
        self, identifier: str, *, email: Optional[str] = None,
        staging: bool = False, webroot: Optional[Path] = None,
    ) -> dict[str, Any]:
        identifier, is_ip = _identifier(identifier)
        if email is not None and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("acme_email_invalid")
        version = self.version()
        if is_ip and version < IP_CERTBOT_MINIMUM:
            raise ValueError("acme_certbot_ip_version")
        command = [
            str(self.certbot), "certonly", "--non-interactive",
            "--agree-tos", "--preferred-challenges", "http",
            "--cert-name", self.cert_name, *self._directories(),
        ]
        if webroot is None:
            command.append("--standalone")
        else:
            webroot = webroot.expanduser().resolve()
            if not webroot.is_dir():
                raise ValueError("acme_webroot_invalid")
            command.extend(["--webroot", "--webroot-path", str(webroot)])
        command.extend(["--email", email] if email else ["--register-unsafely-without-email"])
        if staging:
            command.append("--staging")
        if is_ip:
            command.extend(["--preferred-profile", "shortlived", "--ip-address", identifier])
        else:
            command.extend(["--domain", identifier])
        self._run(command)
        return self.deploy(identifier)

    def renew(self, identifier: str, *, dry_run: bool = False) -> dict[str, Any]:
        identifier, _is_ip = _identifier(identifier)
        command = [
            str(self.certbot), "renew", "--cert-name", self.cert_name,
            *self._directories(),
        ]
        if dry_run:
            command.append("--dry-run")
        self._run(command)
        return self.deploy(identifier)

    def deploy(self, identifier: str) -> dict[str, Any]:
        identifier, is_ip = _identifier(identifier)
        lineage = self.config_dir / "live" / self.cert_name
        cert = lineage / "cert.pem"
        chain = lineage / "chain.pem"
        key = lineage / "privkey.pem"
        if not all(path.is_file() for path in (cert, chain, key)):
            raise ValueError("acme_lineage_missing")
        status = self.tls.import_leaf(
            cert=cert,
            key=key,
            chain=chain,
            domains=[] if is_ip else [identifier],
            ips=[identifier] if is_ip else [],
            source="certbot",
        )
        data = self.store.load()
        data["settings"].setdefault("subscription_tls", {}).update({
            "mode": "acme",
            "identifier": identifier,
            "cert_name": self.cert_name,
            "certbot": str(self.certbot),
            "short_lived": is_ip,
        })
        self.store.save(data)
        return {**status, "identifier": identifier, "short_lived": is_ip}

    @staticmethod
    def _run(command: list[str]) -> None:
        try:
            subprocess.run(command, check=True, text=True, timeout=600)
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("acme_certbot_failed") from error
