from __future__ import annotations

import socket
import socketserver
import ssl
import threading
import time
import urllib.parse
import ipaddress
import hashlib
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Optional

from .store import Store
from .subscriptions import SubscriptionService
from .users import UserService


MAX_REQUEST_LINE = 8192
MAX_HEADERS = 16_384


class SubscriptionRateLimiter:
    """Bounded sliding-window limiter; identifiers are held as hashes only."""

    def __init__(self, requests: int = 30, window_seconds: int = 60,
                 failures: int = 10, ban_seconds: int = 300) -> None:
        if not 1 <= requests <= 10_000 or not 1 <= window_seconds <= 3600:
            raise ValueError("subscription_invalid_rate_limit")
        if not 1 <= failures <= requests or not 1 <= ban_seconds <= 86_400:
            raise ValueError("subscription_invalid_abuse_limit")
        self.requests = requests; self.window_seconds = window_seconds
        self.failures = failures; self.ban_seconds = ban_seconds
        self.events: dict[str, deque[float]] = defaultdict(deque)
        self.failed: dict[str, deque[float]] = defaultdict(deque)
        self.banned_until: dict[str, float] = {}
        self.lock = threading.Lock()

    @staticmethod
    def _key(kind: str, value: str) -> str:
        return kind + ":" + hashlib.sha256(value.encode()).hexdigest()

    def allow(self, address: str, token: Optional[str] = None) -> tuple[bool, int]:
        now = time.monotonic()
        keys = [self._key("ip", address)]
        if token:
            keys.append(self._key("token", token))
        with self.lock:
            retry = 0
            for key in keys:
                banned = self.banned_until.get(key, 0)
                if banned > now:
                    retry = max(retry, int(banned - now) + 1)
                queue = self.events[key]
                while queue and queue[0] <= now - self.window_seconds:
                    queue.popleft()
                if len(queue) >= self.requests:
                    retry = max(retry, int(queue[0] + self.window_seconds - now) + 1)
            if retry:
                return False, retry
            for key in keys:
                self.events[key].append(now)
            # Prevent unbounded growth under address spraying.
            if len(self.events) > 20_000:
                self._compact(now)
            return True, 0

    def failure(self, address: str) -> None:
        now = time.monotonic(); key = self._key("ip", address)
        with self.lock:
            queue = self.failed[key]
            while queue and queue[0] <= now - self.window_seconds:
                queue.popleft()
            queue.append(now)
            if len(queue) >= self.failures:
                self.banned_until[key] = now + self.ban_seconds

    def success(self, address: str) -> None:
        key = self._key("ip", address)
        with self.lock:
            self.failed.pop(key, None)

    def _compact(self, now: float) -> None:
        cutoff = now - max(self.window_seconds, self.ban_seconds)
        for mapping in (self.events, self.failed):
            for key in list(mapping):
                queue = mapping[key]
                while queue and queue[0] <= cutoff:
                    queue.popleft()
                if not queue:
                    del mapping[key]
        self.banned_until = {key: value for key, value in self.banned_until.items()
                             if value > now}
        for mapping in (self.events, self.failed):
            while len(mapping) > 20_000:
                del mapping[next(iter(mapping))]
        while len(self.banned_until) > 20_000:
            del self.banned_until[next(iter(self.banned_until))]


def _certificate_name_matches(decoded: dict[str, Any], public_host: str) -> bool:
    names = decoded.get("subjectAltName", ())
    try:
        expected_ip = ipaddress.ip_address(public_host)
    except ValueError:
        expected_ip = None
    if expected_ip is not None:
        for kind, value in names:
            if kind == "IP Address":
                try:
                    if ipaddress.ip_address(value) == expected_ip:
                        return True
                except ValueError:
                    continue
        return False
    expected = public_host.rstrip(".").encode("idna").decode().casefold()
    for kind, value in names:
        if kind != "DNS":
            continue
        candidate = value.rstrip(".").encode("idna").decode().casefold()
        if candidate == expected:
            return True
        if candidate.startswith("*."):
            suffix = candidate[2:]
            if expected.endswith("." + suffix) and expected.count(".") == suffix.count(".") + 1:
                return True
    return False


def tls_context(cert: Path, key: Path, public_host: str) -> ssl.SSLContext:
    if not cert.is_file() or not key.is_file():
        raise ValueError("subscription_tls_missing")
    if key.stat().st_mode & 0o077:
        raise ValueError("subscription_key_permissions")
    try:
        decoded = ssl._ssl._test_decode_cert(str(cert))  # type: ignore[attr-defined]
        not_before = decoded.get("notBefore")
        not_after = decoded.get("notAfter")
        if not not_before or ssl.cert_time_to_seconds(not_before) > time.time():
            raise ValueError("subscription_tls_not_yet_valid")
        if not not_after or ssl.cert_time_to_seconds(not_after) <= time.time():
            raise ValueError("subscription_tls_expired")
        if not _certificate_name_matches(decoded, public_host):
            raise ValueError("subscription_tls_name_mismatch")
    except ValueError:
        raise
    except (OSError, ssl.CertificateError, ssl.SSLError) as error:
        raise ValueError("subscription_tls_name_mismatch") from error
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    try:
        context.load_cert_chain(str(cert), str(key), password=lambda: "")
    except (OSError, ssl.SSLError) as error:
        raise ValueError("subscription_tls_pair_invalid") from error
    return context


class SubscriptionTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 128

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[socketserver.BaseRequestHandler],
        context: ssl.SSLContext,
        store: Store,
        max_clients: int,
        cert: Path,
        key: Path,
        public_host: str,
        rate_limiter: SubscriptionRateLimiter,
    ) -> None:
        self.context = context
        self.store = store
        self.client_slots = threading.BoundedSemaphore(max_clients)
        self.cert = cert
        self.key = key
        self.public_host = public_host
        self.rate_limiter = rate_limiter
        self.context_lock = threading.Lock()
        self.tls_revision = self._tls_revision()
        super().__init__(address, handler)

    def _tls_revision(self) -> tuple[str, str, int, int]:
        return (
            str(self.cert.resolve()),
            str(self.key.resolve()),
            self.cert.stat().st_mtime_ns,
            self.key.stat().st_mtime_ns,
        )

    def _reload_tls_if_changed(self) -> None:
        try:
            revision = self._tls_revision()
        except OSError:
            return
        if revision == self.tls_revision:
            return
        try:
            replacement = tls_context(self.cert, self.key, self.public_host)
        except ValueError:
            return
        with self.context_lock:
            self.context = replacement
            self.tls_revision = revision

    def get_request(self) -> tuple[socket.socket, Any]:
        connection, address = super().get_request()
        connection.settimeout(10)
        try:
            self._reload_tls_if_changed()
            with self.context_lock:
                context = self.context
            secured = context.wrap_socket(connection, server_side=True)
        except Exception:
            connection.close()
            raise
        return secured, address

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self.client_slots.acquire(blocking=False):
            request.close()
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.client_slots.release()


class SubscriptionHandler(socketserver.BaseRequestHandler):
    server: SubscriptionTCPServer

    def _response(self, status: int, body: bytes = b"", *, retry_after: int = 0) -> None:
        reasons = {
            200: "OK", 204: "No Content", 400: "Bad Request",
            404: "Not Found", 405: "Method Not Allowed", 414: "URI Too Long",
            429: "Too Many Requests",
        }
        headers = [
            f"HTTP/1.1 {status} {reasons[status]}",
            "Content-Type: text/plain; charset=utf-8",
            "Cache-Control: no-store",
            "X-Content-Type-Options: nosniff",
            "Referrer-Policy: no-referrer",
            "Connection: close",
            f"Content-Length: {len(body)}",
            "",
            "",
        ]
        if retry_after:
            headers.insert(-2, f"Retry-After: {retry_after}")
        self.request.sendall("\r\n".join(headers).encode("ascii") + body)

    def handle(self) -> None:
        stream = self.request.makefile("rb")
        line = stream.readline(MAX_REQUEST_LINE + 1)
        if len(line) > MAX_REQUEST_LINE:
            self._response(414)
            return
        try:
            method, target, version = line.decode("ascii").strip().split(" ")
        except (UnicodeDecodeError, ValueError):
            self._response(400)
            return
        header_bytes = 0
        while True:
            header = stream.readline(MAX_HEADERS + 1)
            header_bytes += len(header)
            if header in {b"\r\n", b"\n"}:
                break
            if not header or header_bytes > MAX_HEADERS or b":" not in header:
                self._response(400)
                return
        if method != "GET":
            self._response(405)
            return
        if version not in {"HTTP/1.0", "HTTP/1.1"}:
            self._response(400)
            return
        parsed = urllib.parse.urlsplit(target)
        if not parsed.query and not parsed.fragment and parsed.path == "/healthz":
            self._response(204)
            return
        if parsed.query or parsed.fragment or not parsed.path.startswith("/sub/"):
            self._response(404)
            return
        token = urllib.parse.unquote(parsed.path[len("/sub/"):])
        address = str(self.client_address[0])
        allowed, retry_after = self.server.rate_limiter.allow(address, token)
        if not allowed:
            self._response(429, retry_after=retry_after)
            return
        users = UserService(self.server.store)
        user = users.verify_subscription_token(token)
        if user is None:
            self.server.rate_limiter.failure(address)
            self._response(404)
            return
        try:
            body = SubscriptionService(self.server.store).render(user["id"], "base64").encode()
        except ValueError:
            self.server.rate_limiter.failure(address)
            self._response(404)
            return
        self.server.rate_limiter.success(address)
        self._response(200, body)


def create_subscription_server(
    *,
    store: Store,
    listen: str,
    port: int,
    public_host: str,
    cert: Path,
    key: Path,
    max_clients: int = 8,
    rate_limit_requests: int = 30,
    rate_limit_window_seconds: int = 60,
    abuse_failures: int = 10,
    abuse_ban_seconds: int = 300,
) -> SubscriptionTCPServer:
    if not 1 <= port <= 65_535:
        raise ValueError("subscription_invalid_port")
    if not 1 <= max_clients <= 64:
        raise ValueError("subscription_invalid_clients")
    context = tls_context(cert, key, public_host)
    limiter = SubscriptionRateLimiter(rate_limit_requests, rate_limit_window_seconds,
                                      abuse_failures, abuse_ban_seconds)
    try:
        return SubscriptionTCPServer(
            (listen, port), SubscriptionHandler, context, store, max_clients,
            cert, key, public_host, limiter,
        )
    except OSError as error:
        raise ValueError("subscription_bind_failed") from error


def serve_https(**kwargs: Any) -> None:
    server = create_subscription_server(**kwargs)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
