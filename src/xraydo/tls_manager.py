from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import random
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .store import Store
from .subscription_server import tls_context


_BACKUP_MAGIC = b"XRAYDO-TLS-BACKUP\x00\x01"
_BACKUP_ITERATIONS = 600_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run(arguments: list[str]) -> bytes:
    try:
        result = subprocess.run(
            arguments, capture_output=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("tls_tool_failed") from error
    if result.returncode != 0:
        raise ValueError("tls_tool_failed")
    return result.stdout


def _backup_passphrase(path: Path) -> bytes:
    path = path.expanduser().resolve()
    try:
        mode = path.stat().st_mode
        if not path.is_file() or mode & 0o077:
            raise ValueError("tls_backup_passphrase_permissions")
        value = path.read_bytes().rstrip(b"\r\n")
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("tls_backup_passphrase_unreadable") from error
    if not 12 <= len(value) <= 4096:
        raise ValueError("tls_backup_passphrase_invalid")
    return value


def _openssl_backup_cipher(
    source: Path, destination: Path, passphrase: bytes, decrypt: bool = False
) -> None:
    arguments = [
        "openssl",
        "enc",
        "-aes-256-cbc",
        "-pbkdf2",
        "-iter",
        str(_BACKUP_ITERATIONS),
        "-md",
        "sha256",
        "-pass",
        "stdin",
        "-in",
        str(source),
        "-out",
        str(destination),
    ]
    if decrypt:
        arguments.insert(2, "-d")
    try:
        result = subprocess.run(
            arguments,
            input=passphrase + b"\n",
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("tls_backup_crypto_failed") from error
    if result.returncode != 0:
        raise ValueError("tls_backup_crypto_failed")


def _transfer_tls(source: ssl.MemoryBIO, destination: ssl.MemoryBIO) -> bool:
    transferred = False
    while True:
        chunk = source.read()
        if not chunk:
            return transferred
        destination.write(chunk)
        transferred = True


def _local_https_probe(cert: Path, key: Path, ca_cert: Path, host: str) -> None:
    try:
        server_context = tls_context(cert, key, host)
        client_context = ssl.create_default_context(cafile=str(ca_cert))
        client_context.minimum_version = ssl.TLSVersion.TLSv1_2
        client_in = ssl.MemoryBIO()
        client_out = ssl.MemoryBIO()
        server_in = ssl.MemoryBIO()
        server_out = ssl.MemoryBIO()
        client = client_context.wrap_bio(
            client_in, client_out, server_side=False, server_hostname=host
        )
        server = server_context.wrap_bio(server_in, server_out, server_side=True)
        client_done = False
        server_done = False
        for _attempt in range(100):
            progressed = False
            if not client_done:
                try:
                    client.do_handshake()
                    client_done = True
                    progressed = True
                except ssl.SSLWantReadError:
                    pass
            if _transfer_tls(client_out, server_in):
                progressed = True
            if not server_done:
                try:
                    server.do_handshake()
                    server_done = True
                    progressed = True
                except ssl.SSLWantReadError:
                    pass
            if _transfer_tls(server_out, client_in):
                progressed = True
            if client_done and server_done:
                break
            if not progressed:
                raise ValueError("tls_local_probe_failed")
        else:
            raise ValueError("tls_local_probe_failed")

        request = b"GET /healthz HTTP/1.1\r\nHost: local-probe\r\nConnection: close\r\n\r\n"
        if client.write(request) != len(request):
            raise ValueError("tls_local_probe_failed")
        _transfer_tls(client_out, server_in)
        if server.read(len(request)) != request:
            raise ValueError("tls_local_probe_failed")
        response = b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n"
        if server.write(response) != len(response):
            raise ValueError("tls_local_probe_failed")
        _transfer_tls(server_out, client_in)
        if client.read(len(response)) != response:
            raise ValueError("tls_local_probe_failed")
    except (OSError, ssl.SSLError) as error:
        raise ValueError("tls_local_probe_failed") from error
    except ValueError:
        raise


def _switch_link(parent: Path, name: str, target: Path) -> None:
    temporary = parent / f".{name}-{uuid.uuid4().hex}"
    os.symlink(target.name, temporary)
    os.replace(temporary, parent / name)


def _domain(value: str) -> str:
    value = value.strip().rstrip(".")
    try:
        encoded = value.encode("idna").decode().lower()
    except UnicodeError as error:
        raise ValueError("tls_invalid_san") from error
    if len(encoded) > 253 or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in encoded.split(".")
    ):
        raise ValueError("tls_invalid_san")
    return encoded


class TLSManager:
    def __init__(self, store: Store, root: Optional[Path] = None) -> None:
        self.store = store
        self.root = (root or store.data_dir / "tls").resolve()
        self.ca_root = self.root / "ca"
        self.leaf_root = self.root / "leaf"

    def _prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ca_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.leaf_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path in (self.root, self.ca_root, self.leaf_root):
            os.chmod(path, 0o700)

    def init_ca(self) -> dict[str, Any]:
        self._prepare()
        current = self.ca_root / "current"
        if current.is_symlink() and (current / "ca.key").is_file() and (current / "ca.crt").is_file():
            return self.status()
        version = self._create_ca_version()
        _switch_link(self.ca_root, "current", version)
        return self.status()

    def _create_ca_version(self) -> Path:
        self._prepare()
        version = self.ca_root / f"v-{uuid.uuid4().hex}"
        version.mkdir(mode=0o700)
        key = version / "ca.key"
        cert = version / "ca.crt"
        config = version / "ca.cnf"
        config.write_text(
            """[req]
prompt = no
distinguished_name = dn
x509_extensions = v3_ca
[dn]
CN = xraydo private subscription CA
[v3_ca]
basicConstraints = critical, CA:true, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
""",
            encoding="utf-8",
        )
        try:
            _run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(key)])
            _run([
                "openssl", "req", "-new", "-x509", "-sha256", "-days", "3650",
                "-key", str(key), "-config", str(config), "-out", str(cert),
            ])
            os.chmod(key, 0o600)
            os.chmod(cert, 0o644)
            config.unlink()
        except Exception:
            shutil.rmtree(version, ignore_errors=True)
            raise
        return version

    def issue(
        self,
        *,
        domains: list[str],
        ips: list[str],
        days: int = 90,
        renew_percent: int = 67,
    ) -> dict[str, Any]:
        if not 1 <= days <= 825:
            raise ValueError("tls_invalid_days")
        if not 50 <= renew_percent <= 90:
            raise ValueError("tls_invalid_renew_percent")
        normalized_domains = sorted({_domain(value) for value in domains})
        try:
            normalized_ips = sorted({str(ipaddress.ip_address(value)) for value in ips})
        except ValueError as error:
            raise ValueError("tls_invalid_san") from error
        if not normalized_domains and not normalized_ips:
            raise ValueError("tls_san_required")
        self.init_ca()
        version = self.leaf_root / f"v-{uuid.uuid4().hex}"
        version.mkdir(mode=0o700)
        key = version / "server.key"
        cert = version / "server.crt"
        csr = version / "server.csr"
        config = version / "server.cnf"
        common_name = (normalized_domains + normalized_ips)[0]
        alt_lines = []
        alt_lines.extend(f"DNS.{index} = {value}" for index, value in enumerate(normalized_domains, 1))
        alt_lines.extend(f"IP.{index} = {value}" for index, value in enumerate(normalized_ips, 1))
        config.write_text(
            "[req]\nprompt = no\ndistinguished_name = dn\n"
            f"[dn]\nCN = {common_name}\n"
            "[v3_server]\nbasicConstraints = critical, CA:false\n"
            "keyUsage = critical, digitalSignature, keyEncipherment\n"
            "extendedKeyUsage = serverAuth\n"
            "subjectKeyIdentifier = hash\n"
            "authorityKeyIdentifier = keyid,issuer\n"
            "subjectAltName = @alt_names\n"
            "[alt_names]\n" + "\n".join(alt_lines) + "\n",
            encoding="utf-8",
        )
        ca_key = self.ca_root / "current" / "ca.key"
        ca_cert = self.ca_root / "current" / "ca.crt"
        issued_at = _now()
        expires_at = issued_at + timedelta(days=days)
        try:
            _run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(key)])
            _run(["openssl", "req", "-new", "-sha256", "-key", str(key), "-config", str(config), "-out", str(csr)])
            _run([
                "openssl", "x509", "-req", "-sha256", "-days", str(days),
                "-in", str(csr), "-CA", str(ca_cert), "-CAkey", str(ca_key),
                "-set_serial", "0x" + uuid.uuid4().hex,
                "-extfile", str(config), "-extensions", "v3_server", "-out", str(cert),
            ])
            os.chmod(key, 0o600)
            os.chmod(cert, 0o644)
            for name in normalized_domains + normalized_ips:
                tls_context(cert, key, name)
                _local_https_probe(cert, key, ca_cert, name)
            metadata = {
                "managed": True,
                "source": "private-ca",
                "domains": normalized_domains,
                "ips": normalized_ips,
                "issued_at": issued_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "renew_after": (
                    issued_at + (expires_at - issued_at) * renew_percent / 100
                ).isoformat(),
                "days": days,
                "renew_percent": renew_percent,
                "local_https_probe_at": _now().isoformat(),
                "local_https_probe_hosts": normalized_domains + normalized_ips,
            }
            (version / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            os.chmod(version / "metadata.json", 0o600)
            csr.unlink()
            config.unlink()
            _switch_link(self.leaf_root, "current", version)
            data = self.store.load()
            data["settings"]["subscription_tls"] = {
                "mode": "private-ca",
                "ca_cert": str(self.ca_root / "current" / "ca.crt"),
                "cert": str(self.leaf_root / "current" / "server.crt"),
                "key": str(self.leaf_root / "current" / "server.key"),
            }
            self.store.save(data)
        except Exception:
            shutil.rmtree(version, ignore_errors=True)
            raise
        return self.status()

    def fingerprint(self) -> str:
        cert = self.ca_root / "current" / "ca.crt"
        if not cert.is_file():
            raise ValueError("tls_ca_missing")
        der = _run(["openssl", "x509", "-in", str(cert), "-outform", "DER"])
        return hashlib.sha256(der).hexdigest()

    def status(self) -> dict[str, Any]:
        ca_cert = self.ca_root / "current" / "ca.crt"
        leaf = self.leaf_root / "current"
        metadata_path = leaf / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else None
        return {
            "root": str(self.root),
            "ca_ready": ca_cert.is_file(),
            "ca_cert": str(ca_cert) if ca_cert.is_file() else None,
            "ca_fingerprint_sha256": self.fingerprint() if ca_cert.is_file() else None,
            "leaf_ready": (leaf / "server.crt").is_file() and (leaf / "server.key").is_file(),
            "cert": str(leaf / "server.crt") if (leaf / "server.crt").is_file() else None,
            "key": str(leaf / "server.key") if (leaf / "server.key").is_file() else None,
            "metadata": metadata,
            "ca_rotation": self.store.load()["settings"].get("tls_ca_rotation"),
        }

    def renew(self, force: bool = False) -> dict[str, Any]:
        status = self.status()
        metadata = status.get("metadata")
        if not metadata:
            raise ValueError("tls_leaf_missing")
        if metadata.get("managed") is False:
            raise ValueError("tls_imported_not_renewable")
        renew_after = datetime.fromisoformat(metadata["renew_after"])
        if not force and _now() < renew_after:
            return {**status, "renewed": False}
        renewed = self.issue(
            domains=metadata["domains"],
            ips=metadata["ips"],
            days=int(metadata["days"]),
            renew_percent=int(metadata.get("renew_percent", 67)),
        )
        return {**renewed, "renewed": True}

    def reconcile_ips(self, actual_ips: Optional[list[str]] = None) -> dict[str, Any]:
        data = self.store.load()
        base_url = data["settings"].get("subscription_base_url")
        inferred_from_url = not actual_ips
        if not actual_ips:
            hostname = urllib.parse.urlsplit(base_url).hostname if base_url else None
            try:
                actual_ips = [str(ipaddress.ip_address(hostname))] if hostname else []
            except ValueError:
                actual_ips = []
        if not actual_ips:
            raise ValueError("tls_ip_source_required")
        try:
            normalized = sorted({str(ipaddress.ip_address(value)) for value in actual_ips})
        except ValueError as error:
            raise ValueError("tls_invalid_san") from error

        status = self.status()
        metadata = status.get("metadata")
        if not metadata:
            raise ValueError("tls_leaf_missing")
        if metadata.get("managed") is False:
            raise ValueError("tls_ip_sync_requires_managed_leaf")
        previous = sorted(metadata.get("ips", []))
        if inferred_from_url:
            url_address = ipaddress.ip_address(normalized[0])
            normalized = sorted(
                {
                    *(
                        value
                        for value in previous
                        if ipaddress.ip_address(value).version != url_address.version
                    ),
                    str(url_address),
                }
            )
        if previous == normalized:
            return {
                **status,
                "ip_changed": False,
                "previous_ips": previous,
                "actual_ips": normalized,
                "url_updated": False,
            }

        self.issue(
            domains=metadata["domains"],
            ips=normalized,
            days=int(metadata["days"]),
            renew_percent=int(metadata.get("renew_percent", 67)),
        )
        url_updated = False
        data = self.store.load()
        base_url = data["settings"].get("subscription_base_url")
        if base_url:
            parsed = urllib.parse.urlsplit(base_url)
            try:
                old_address = ipaddress.ip_address(parsed.hostname or "")
            except ValueError:
                old_address = None
            if old_address and str(old_address) in previous and str(old_address) not in normalized:
                addresses = [ipaddress.ip_address(value) for value in normalized]
                candidates = [item for item in addresses if item.version == old_address.version]
                replacement = candidates[0] if candidates else addresses[0]
                host = f"[{replacement}]" if replacement.version == 6 else str(replacement)
                if parsed.port is not None:
                    host += f":{parsed.port}"
                data["settings"]["subscription_base_url"] = urllib.parse.urlunsplit(
                    parsed._replace(netloc=host)
                )
                self.store.save(data)
                url_updated = True
        return {
            **self.status(),
            "ip_changed": True,
            "previous_ips": previous,
            "actual_ips": normalized,
            "url_updated": url_updated,
        }

    def import_leaf(
        self,
        *,
        cert: Path,
        key: Path,
        domains: list[str],
        ips: list[str],
        ca_cert: Optional[Path] = None,
        chain: Optional[Path] = None,
    ) -> dict[str, Any]:
        normalized_domains = sorted({_domain(value) for value in domains})
        try:
            normalized_ips = sorted({str(ipaddress.ip_address(value)) for value in ips})
        except ValueError as error:
            raise ValueError("tls_invalid_san") from error
        if not normalized_domains and not normalized_ips:
            raise ValueError("tls_san_required")
        cert = cert.expanduser().resolve()
        key = key.expanduser().resolve()
        if ca_cert is not None:
            ca_cert = ca_cert.expanduser().resolve()
        if chain is not None:
            chain = chain.expanduser().resolve()
        for host in normalized_domains + normalized_ips:
            tls_context(cert, key, host)
        verify = ["openssl", "verify", "-purpose", "sslserver"]
        if ca_cert is not None:
            if not ca_cert.is_file():
                raise ValueError("tls_ca_missing")
            verify.extend(["-CAfile", str(ca_cert)])
        if chain is not None:
            if not chain.is_file():
                raise ValueError("tls_chain_invalid")
            verify.extend(["-untrusted", str(chain)])
        verify.append(str(cert))
        try:
            _run(verify)
        except ValueError as error:
            raise ValueError("tls_chain_invalid") from error
        try:
            decoded = ssl._ssl._test_decode_cert(str(cert))  # type: ignore[attr-defined]
            issuer = decoded["issuer"]
            serial = decoded["serialNumber"]
            expires = datetime.fromtimestamp(
                ssl.cert_time_to_seconds(decoded["notAfter"]), timezone.utc
            ).isoformat()
        except (KeyError, OSError, ValueError) as error:
            raise ValueError("tls_certificate_invalid") from error
        self._prepare()
        version = self.leaf_root / f"v-{uuid.uuid4().hex}"
        version.mkdir(mode=0o700)
        destination_cert = version / "server.crt"
        destination_key = version / "server.key"
        try:
            with destination_cert.open("wb") as output:
                output.write(cert.read_bytes())
                if chain is not None:
                    output.write(b"\n")
                    output.write(chain.read_bytes())
            shutil.copyfile(key, destination_key)
            os.chmod(destination_cert, 0o644)
            os.chmod(destination_key, 0o600)
            if ca_cert is not None:
                shutil.copyfile(ca_cert, version / "issuer-ca.crt")
                os.chmod(version / "issuer-ca.crt", 0o644)
            if chain is not None:
                shutil.copyfile(chain, version / "chain.crt")
                os.chmod(version / "chain.crt", 0o644)
            for host in normalized_domains + normalized_ips:
                tls_context(destination_cert, destination_key, host)
            metadata = {
                "managed": False,
                "source": "import",
                "domains": normalized_domains,
                "ips": normalized_ips,
                "expires_at": expires,
                "issuer": issuer,
                "serial": serial,
            }
            (version / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            os.chmod(version / "metadata.json", 0o600)
            _switch_link(self.leaf_root, "current", version)
            data = self.store.load()
            data["settings"]["subscription_tls"] = {
                "mode": "imported",
                "ca_cert": str(version / "issuer-ca.crt") if ca_cert is not None else None,
                "cert": str(self.leaf_root / "current" / "server.crt"),
                "key": str(self.leaf_root / "current" / "server.key"),
            }
            self.store.save(data)
        except Exception:
            shutil.rmtree(version, ignore_errors=True)
            raise
        return self.status()

    def run_renewer(
        self,
        *,
        interval_seconds: int = 3600,
        jitter_seconds: int = 300,
        once: bool = False,
    ) -> dict[str, Any]:
        if not 60 <= interval_seconds <= 604_800:
            raise ValueError("tls_invalid_renew_interval")
        if not 0 <= jitter_seconds <= interval_seconds // 2:
            raise ValueError("tls_invalid_renew_jitter")
        while True:
            try:
                result = self.renew()
                state = "renewed" if result["renewed"] else "current"
            except ValueError as error:
                if str(error) == "tls_leaf_missing":
                    result = {"renewed": False}
                    state = "waiting"
                else:
                    result = {"renewed": False, "error": str(error)}
                    state = "error"
            report = {"state": state, **result}
            if once:
                return report
            print(json.dumps({"tls_renewer": {"state": state}}, sort_keys=True), flush=True)
            delay = interval_seconds + random.uniform(-jitter_seconds, jitter_seconds)
            try:
                time.sleep(max(1, delay))
            except KeyboardInterrupt:
                return report

    def _write_ca_bundle(self, certificates: list[Path]) -> Path:
        bundle = self.ca_root / "trust-bundle.crt"
        temporary = self.ca_root / f".bundle-{uuid.uuid4().hex}"
        with temporary.open("wb") as output:
            for certificate in certificates:
                output.write(certificate.read_bytes())
                output.write(b"\n")
        os.chmod(temporary, 0o644)
        os.replace(temporary, bundle)
        return bundle

    def prepare_ca_rotation(self, overlap_days: int = 30) -> dict[str, Any]:
        if not 1 <= overlap_days <= 365:
            raise ValueError("tls_invalid_overlap")
        status = self.status()
        if not status["ca_ready"]:
            raise ValueError("tls_ca_missing")
        if not status["metadata"] or status["metadata"].get("managed") is False:
            raise ValueError("tls_rotation_requires_managed_leaf")
        existing = status.get("ca_rotation") or {}
        if existing.get("state") in {"prepared", "active"}:
            raise ValueError("tls_rotation_in_progress")
        old_version = (self.ca_root / "current").resolve()
        new_version = self._create_ca_version()
        self._write_ca_bundle([old_version / "ca.crt", new_version / "ca.crt"])
        data = self.store.load()
        data["settings"]["tls_ca_rotation"] = {
            "state": "prepared",
            "old_version": old_version.name,
            "new_version": new_version.name,
            "prepared_at": _now().isoformat(),
            "overlap_days": overlap_days,
            "activated_at": None,
            "overlap_until": None,
        }
        self.store.save(data)
        return self.status()

    def activate_ca_rotation(self) -> dict[str, Any]:
        data = self.store.load()
        rotation = data["settings"].get("tls_ca_rotation") or {}
        if rotation.get("state") != "prepared":
            raise ValueError("tls_rotation_not_prepared")
        metadata = self.status().get("metadata")
        if not metadata or metadata.get("managed") is False:
            raise ValueError("tls_rotation_requires_managed_leaf")
        old_version = self.ca_root / rotation["old_version"]
        new_version = self.ca_root / rotation["new_version"]
        if not (old_version / "ca.crt").is_file() or not (new_version / "ca.crt").is_file():
            raise ValueError("tls_rotation_invalid")
        _switch_link(self.ca_root, "current", new_version)
        try:
            self.issue(
                domains=metadata["domains"],
                ips=metadata["ips"],
                days=int(metadata["days"]),
                renew_percent=int(metadata.get("renew_percent", 67)),
            )
        except Exception:
            _switch_link(self.ca_root, "current", old_version)
            raise
        activated = _now()
        data = self.store.load()
        rotation = data["settings"]["tls_ca_rotation"]
        rotation["state"] = "active"
        rotation["activated_at"] = activated.isoformat()
        rotation["overlap_until"] = (
            activated + timedelta(days=int(rotation["overlap_days"]))
        ).isoformat()
        self.store.save(data)
        return self.status()

    def finalize_ca_rotation(self, force: bool = False) -> dict[str, Any]:
        data = self.store.load()
        rotation = data["settings"].get("tls_ca_rotation") or {}
        if rotation.get("state") != "active":
            raise ValueError("tls_rotation_not_active")
        overlap_until = datetime.fromisoformat(rotation["overlap_until"])
        if not force and _now() < overlap_until:
            raise ValueError("tls_rotation_overlap_active")
        new_version = self.ca_root / rotation["new_version"]
        self._write_ca_bundle([new_version / "ca.crt"])
        rotation["state"] = "finalized"
        rotation["finalized_at"] = _now().isoformat()
        self.store.save(data)
        return self.status()

    def export_ca(self, destination: Path) -> Path:
        rotation = self.store.load()["settings"].get("tls_ca_rotation") or {}
        bundle = self.ca_root / "trust-bundle.crt"
        source = (
            bundle
            if rotation.get("state") in {"prepared", "active", "finalized"} and bundle.is_file()
            else self.ca_root / "current" / "ca.crt"
        )
        if not source.is_file():
            raise ValueError("tls_ca_missing")
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = destination.parent / f".ca-{uuid.uuid4().hex}"
        shutil.copyfile(source, temporary)
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
        return destination

    def _backup_manifest(self) -> dict[str, Any]:
        files = []
        for path in sorted(self.root.rglob("*")):
            relative = path.relative_to(self.root).as_posix()
            stat = path.lstat()
            record: dict[str, Any] = {
                "path": relative,
                "mode": oct(stat.st_mode & 0o777),
            }
            if path.is_symlink():
                record.update({"type": "symlink", "target": os.readlink(path)})
            elif path.is_file():
                record.update(
                    {
                        "type": "file",
                        "size": stat.st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
            elif path.is_dir():
                record["type"] = "directory"
            else:
                raise ValueError("tls_backup_unsupported_file")
            files.append(record)
        status = self.status()
        return {
            "format": "xraydo-tls-backup-v1",
            "created_at": _now().isoformat(),
            "encrypted": True,
            "contains_private_keys": True,
            "ca_fingerprint_sha256": status["ca_fingerprint_sha256"],
            "ca_rotation": status.get("ca_rotation"),
            "leaf_metadata": status.get("metadata"),
            "files": files,
        }

    def backup(self, destination: Path, passphrase_file: Path) -> dict[str, Any]:
        if not self.status()["ca_ready"]:
            raise ValueError("tls_ca_missing")
        passphrase = _backup_passphrase(passphrase_file)
        destination = destination.expanduser().resolve()
        if destination == self.root or self.root in destination.parents:
            raise ValueError("tls_backup_destination_inside_pki")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        manifest = self._backup_manifest()
        with tempfile.TemporaryDirectory(prefix="xraydo-tls-backup-") as directory:
            workspace = Path(directory)
            archive_path = workspace / "backup.tar"
            manifest_path = workspace / "manifest.json"
            cipher_path = workspace / "backup.enc"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.chmod(manifest_path, 0o600)
            with tarfile.open(archive_path, "w") as archive:
                archive.add(manifest_path, arcname="manifest.json", recursive=False)
                for path in sorted(self.root.rglob("*")):
                    archive.add(
                        path,
                        arcname="tls/" + path.relative_to(self.root).as_posix(),
                        recursive=False,
                    )
            os.chmod(archive_path, 0o600)
            _openssl_backup_cipher(archive_path, cipher_path, passphrase)
            ciphertext = cipher_path.read_bytes()
            authentication_salt = os.urandom(16)
            authentication_key = hashlib.pbkdf2_hmac(
                "sha256",
                passphrase,
                b"xraydo-tls-backup-auth\x00" + authentication_salt,
                _BACKUP_ITERATIONS,
                dklen=32,
            )
            tag = hmac.new(
                authentication_key,
                _BACKUP_MAGIC + authentication_salt + ciphertext,
                hashlib.sha256,
            ).digest()
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".tls-backup-", dir=destination.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(_BACKUP_MAGIC)
                    output.write(authentication_salt)
                    output.write(tag)
                    output.write(ciphertext)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, destination)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return {"path": str(destination), "manifest": manifest}

    def verify_backup(self, source: Path, passphrase_file: Path) -> dict[str, Any]:
        passphrase = _backup_passphrase(passphrase_file)
        source = source.expanduser().resolve()
        try:
            if not source.is_file() or source.stat().st_size > 128 * 1024 * 1024:
                raise ValueError("tls_backup_invalid")
            envelope = source.read_bytes()
        except ValueError:
            raise
        except OSError as error:
            raise ValueError("tls_backup_invalid") from error
        header_size = len(_BACKUP_MAGIC) + 16 + 32
        if len(envelope) <= header_size or not envelope.startswith(_BACKUP_MAGIC):
            raise ValueError("tls_backup_invalid")
        offset = len(_BACKUP_MAGIC)
        authentication_salt = envelope[offset : offset + 16]
        tag = envelope[offset + 16 : offset + 48]
        ciphertext = envelope[offset + 48 :]
        authentication_key = hashlib.pbkdf2_hmac(
            "sha256",
            passphrase,
            b"xraydo-tls-backup-auth\x00" + authentication_salt,
            _BACKUP_ITERATIONS,
            dklen=32,
        )
        expected = hmac.new(
            authentication_key,
            _BACKUP_MAGIC + authentication_salt + ciphertext,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("tls_backup_auth_failed")
        with tempfile.TemporaryDirectory(prefix="xraydo-tls-verify-") as directory:
            workspace = Path(directory)
            cipher_path = workspace / "backup.enc"
            archive_path = workspace / "backup.tar"
            cipher_path.write_bytes(ciphertext)
            os.chmod(cipher_path, 0o600)
            _openssl_backup_cipher(cipher_path, archive_path, passphrase, decrypt=True)
            try:
                with tarfile.open(archive_path, "r") as archive:
                    member = archive.getmember("manifest.json")
                    extracted = archive.extractfile(member)
                    if extracted is None or member.size > 1024 * 1024:
                        raise ValueError("tls_backup_invalid")
                    manifest = json.loads(extracted.read().decode("utf-8"))
            except (KeyError, OSError, tarfile.TarError, UnicodeError, json.JSONDecodeError) as error:
                raise ValueError("tls_backup_invalid") from error
        if manifest.get("format") != "xraydo-tls-backup-v1":
            raise ValueError("tls_backup_invalid")
        return manifest

    @staticmethod
    def install_command(certificate: Path) -> str:
        quoted = str(certificate.expanduser().resolve()).replace("'", "'\\''")
        if shutil.which("update-ca-certificates"):
            return (
                f"sudo install -m 0644 '{quoted}' /usr/local/share/ca-certificates/xraydo.crt "
                "&& sudo update-ca-certificates"
            )
        if shutil.which("update-ca-trust"):
            return (
                f"sudo install -m 0644 '{quoted}' /etc/pki/ca-trust/source/anchors/xraydo.crt "
                "&& sudo update-ca-trust"
            )
        return f"install the public CA certificate '{quoted}' in the client trust store"
