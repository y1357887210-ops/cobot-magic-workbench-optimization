"""Loopback compatibility facade. No model, robot or lifecycle mutation code."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import socket
import threading
import time
from urllib.parse import unquote, urlsplit
import uuid

from .connect_orchestrator import ConnectOrchestrator
from .contracts import project_status
from .release_identity import verify_startup_identity

MAX_REQUEST = 16 * 1024
MAX_STATE = 4 * 1024 * 1024
MAX_PROXY = 256 * 1024 * 1024
POST_ROUTES = frozenset((
    "/api/jobs", "/api/stop", "/api/v2/speed", "/api/storage/scan",
    "/api/storage/select", "/api/semantic/replay", "/api/semantic/cancel",
    "/platform/connect"))
SCHEMA = "agilex-platform-health-v1"


class UpstreamError(Exception):
    def __init__(self, code, protocol=False):
        self.code, self.protocol = code, protocol
        super().__init__(code)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_origin(value):
    p = urlsplit(value)
    if (p.scheme != "http" or p.hostname != "127.0.0.1" or p.username or p.password
            or p.path or p.query or p.fragment or p.port is None
            or not 1 <= p.port <= 65535):
        raise ValueError("legacy-origin must be http://127.0.0.1:<port>")
    return p.hostname, p.port


def safe_target(target):
    if not isinstance(target, str) or any(ord(c) < 32 or ord(c) == 127 for c in target):
        raise ValueError("Invalid request target")
    p = urlsplit(target)
    path = unquote(p.path, errors="strict")
    if (p.scheme or p.netloc or p.fragment or not path.startswith("/") or path.startswith("//")
            or "\\" in path or "%" in path or "\x00" in path
            or any(part in (".", "..") for part in path.split("/"))):
        raise ValueError("Unsafe request target")
    return path, p.query


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("Non-finite JSON value")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


class LegacyClient:
    def __init__(self, origin, timeout=3.0):
        self.host, self.port = parse_origin(origin)
        self.origin = origin
        self.timeout = timeout

    def open(self, method, target, body=None, headers=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        safe = {"Host": f"{self.host}:{self.port}", "Connection": "close"}
        safe.update(headers or {})
        try:
            conn.request(method, target, body=body, headers=safe)
            response = conn.getresponse()
            if 300 <= response.status < 400:
                raise UpstreamError("UPSTREAM_REDIRECT_REFUSED", True)
            return conn, response
        except UpstreamError:
            conn.close()
            raise
        except (OSError, ValueError, http.client.HTTPException):
            conn.close()
            raise UpstreamError("UPSTREAM_UNAVAILABLE") from None

    def state(self):
        conn, response = self.open("GET", "/api/state")
        try:
            if response.status != 200:
                raise UpstreamError("UPSTREAM_STATE_HTTP_ERROR")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].lower()
            if content_type != "application/json":
                raise UpstreamError("UPSTREAM_STATE_NOT_JSON", True)
            chunks, size, deadline = [], 0, time.monotonic() + self.timeout
            while True:
                if time.monotonic() > deadline:
                    raise UpstreamError("UPSTREAM_STATE_DEADLINE")
                block = response.read1(min(65536, MAX_STATE + 1 - size))
                if not block:
                    break
                chunks.append(block)
                size += len(block)
                if size > MAX_STATE:
                    raise UpstreamError("UPSTREAM_STATE_TOO_LARGE", True)
            raw = b"".join(chunks)
            value = strict_json(raw)
            if (not isinstance(value, dict) or type(value.get("connected")) is not bool
                    or not isinstance(value.get("capabilities"), dict)
                    or not isinstance(value.get("jobs"), list)
                    or not isinstance(value.get("hub"), dict)
                    or not isinstance(value.get("precision_workbench"), dict)):
                raise UpstreamError("UPSTREAM_STATE_SCHEMA_MISMATCH", True)
            return value
        except UpstreamError:
            raise
        except (ValueError, UnicodeError, OSError, http.client.HTTPException):
            raise UpstreamError("UPSTREAM_STATE_PROTOCOL_ERROR", True) from None
        finally:
            conn.close()


class StatusCache:
    """One bounded read at a time; expired success is never served on failure."""
    def __init__(self, client, ttl=1.0, clock=time.monotonic):
        if not 0 <= ttl <= 1.0:
            raise ValueError("Status TTL must be at most one second")
        self.client, self.ttl, self.clock = client, ttl, clock
        self.lock = threading.Lock()
        self.value = None
        self.at = None
        self.observed_at = None
        self.error = None

    def read(self):
        if not self.lock.acquire(timeout=self.client.timeout + 0.5):
            return None, None, utc_now(), UpstreamError("UPSTREAM_READ_BUSY")
        try:
            now = self.clock()
            if self.at is not None and 0 <= now - self.at < self.ttl:
                return self.value, now - self.at, self.observed_at, self.error
            try:
                value = self.client.state()
                error = None
            except UpstreamError as exc:
                value, error = None, exc
            self.value, self.error = value, error
            self.at, self.observed_at = self.clock(), utc_now()
            return value, 0.0, self.observed_at, error
        finally:
            self.lock.release()


class PlatformServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, legacy_origin, web_root, release_id="development-unreleased",
                 timeout=3.0, cache_ttl=1.0, release_manifest=None, expected_manifest_sha256=None):
        self.web_root = Path(web_root).resolve(strict=True)
        if not self.web_root.is_dir():
            raise ValueError("web-root must be a directory")
        self.legacy = LegacyClient(legacy_origin, timeout)
        self.cache = StatusCache(self.legacy, cache_ttl)
        self.connector = ConnectOrchestrator(self.legacy)
        self.release_info = verify_startup_identity(release_manifest, self.web_root,
                                                    expected_manifest_sha256, release_id)
        self.release_id = self.release_info["release_id"]
        self.instance_id = uuid.uuid4().hex
        self.host_identity = socket.gethostname()
        boot = Path("/proc/sys/kernel/random/boot_id")
        self.host_boot_id = boot.read_text().strip() if boot.is_file() else None
        super().__init__(("127.0.0.1", port), Handler)

    def health(self):
        return {"schema": SCHEMA, "protocol_version": 1,
                "service": "agilex-control-platform", "release_id": self.release_id,
                "server_instance_id": self.instance_id, "readiness": "management_only",
                **self.release_info}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "AgileXFacade/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(5.0)

    def log_message(self, *_):
        pass

    def reply(self, status, payload, content_type="application/json; charset=utf-8"):
        raw = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=True, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (OSError, TimeoutError):
            pass

    def valid_host(self):
        return self.headers.get_all("Host") == [f"127.0.0.1:{self.server.server_port}"]

    def target(self):
        if not self.valid_host():
            self.reply(421, {"error_code": "HOST_NOT_ALLOWED"})
            return None
        try:
            # BaseHTTPRequestHandler normalizes a leading // before dispatch;
            # validate the original request-target so normalization cannot hide it.
            original_target = self.requestline.split()[1]
            return safe_target(original_target)
        except (ValueError, UnicodeError):
            self.reply(400, {"error_code": "UNSAFE_REQUEST_TARGET"})
            return None

    def do_GET(self):
        target = self.target()
        if target is None:
            return
        path, query = target
        if path == "/platform/health":
            return self.reply(200, self.server.health())
        if path == "/platform/connect/status":
            return self.reply(200, self.server.connector.snapshot())
        if path == "/platform/status":
            value, age, at, error = self.server.cache.read()
            state = project_status(
                value, release_id=self.server.release_id,
                server_instance_id=self.server.instance_id, observed_at=at,
                upstream_status=("PROTOCOL_ERROR" if error.protocol else "OFFLINE") if error else "ONLINE",
                age_s=age, error_code=error.code if error else None)
            state["software_release"].update(self.server.release_info)
            state["runtime_session"]["boot"].update(
                host=self.server.host_identity, host_boot_id=self.server.host_boot_id)
            return self.reply(200, state)
        if path == "/" or path.startswith("/platform-assets/"):
            relative = "index.html" if path == "/" else path[len("/platform-assets/"):]
            try:
                file = (self.server.web_root / relative).resolve(strict=True)
                if not file.is_relative_to(self.server.web_root) or not file.is_file():
                    raise ValueError()
                if file.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError()
                raw = file.read_bytes()
                mime = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
                if file.suffix == ".mjs":
                    mime = "text/javascript"
            except (OSError, ValueError):
                return self.reply(404, {"error_code": "ASSET_NOT_FOUND"})
            return self.reply(200, raw, mime)
        if path.startswith("/platform/") or path.startswith("/__client"):
            return self.reply(404, {"error_code": "ROUTE_NOT_FOUND"})
        if path in ("/workbench", "/workbench/"):
            upstream = "/"
        elif path.startswith("/workbench/"):
            upstream = self.path[len("/workbench"):]
            return self.proxy("GET", upstream)
        else:
            upstream = self.path
            return self.proxy("GET", upstream)
        return self.proxy("GET", upstream + ("?" + query if query else ""))

    def do_POST(self):
        target = self.target()
        if target is None:
            return
        path, query = target
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if self.headers.get_all("Origin") != [origin]:
            return self.reply(403, {"error_code": "ORIGIN_NOT_ALLOWED"})
        if path not in POST_ROUTES or query:
            return self.reply(404, {"error_code": "POST_ROUTE_NOT_ALLOWED"})
        tokens = self.headers.get_all("X-Tiger-Token") or []
        if len(tokens) != 1 or not tokens[0] or len(tokens[0]) > 256:
            return self.reply(403, {"error_code": "TOKEN_REQUIRED"})
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
            return self.reply(400, {"error_code": "REQUEST_LENGTH_REQUIRED"})
        try:
            size = int(lengths[0])
        except ValueError:
            return self.reply(400, {"error_code": "INVALID_REQUEST_LENGTH"})
        if not 0 < size <= MAX_REQUEST:
            return self.reply(413, {"error_code": "REQUEST_BODY_TOO_LARGE"})
        if self.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
            return self.reply(415, {"error_code": "JSON_REQUIRED"})
        try:
            raw = self.rfile.read(size)
            parsed = strict_json(raw)
            if len(raw) != size or not isinstance(parsed, dict):
                raise ValueError()
        except (ValueError, UnicodeError, OSError):
            return self.reply(400, {"error_code": "INVALID_JSON_OBJECT"})
        if path == "/platform/connect":
            result = self.server.connector.run(token=tokens[0], params=parsed)
            return self.reply(200, result)
        return self.proxy("POST", path, raw, tokens[0])

    def proxy(self, method, target, body=None, token=None):
        headers = {}
        for name in ("Range", "If-Range"):
            if self.headers.get(name):
                headers[name] = self.headers[name]
        if method == "POST":
            headers.update({"Origin": self.server.legacy.origin,
                            "Content-Type": "application/json", "X-Tiger-Token": token})
        try:
            conn, response = self.server.legacy.open(method, target, body, headers)
        except UpstreamError as exc:
            return self.reply(502, {"error_code": exc.code, "request_retried": False})
        started = False
        try:
            lengths = response.headers.get_all("Content-Length") or []
            if len(lengths) > 1:
                raise UpstreamError("UPSTREAM_PROTOCOL_ERROR", True)
            size = int(lengths[0]) if lengths else None
            if size is not None and not 0 <= size <= MAX_PROXY:
                raise UpstreamError("UPSTREAM_RESPONSE_TOO_LARGE", True)
            self.send_response(response.status)
            for name in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                if response.getheader(name):
                    self.send_header(name, response.getheader(name))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            started = True
            sent, deadline = 0, time.monotonic() + 30.0
            while True:
                if time.monotonic() > deadline:
                    raise UpstreamError("UPSTREAM_RESPONSE_DEADLINE")
                raw = response.read1(65536)
                if not raw:
                    break
                sent += len(raw)
                if sent > MAX_PROXY:
                    raise UpstreamError("UPSTREAM_RESPONSE_TOO_LARGE", True)
                self.wfile.write(raw)
        except (UpstreamError, OSError, ValueError, http.client.HTTPException) as exc:
            if not started:
                self.reply(502, {"error_code": getattr(exc, "code", "UPSTREAM_PROTOCOL_ERROR"),
                                 "request_retried": False})
            # After response headers, close the stream; never replay a request.
            self.close_connection = True
        finally:
            conn.close()

    def do_HEAD(self):
        self.reply(405, {"error_code": "METHOD_NOT_ALLOWED"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8910)
    parser.add_argument("--legacy-origin", default="http://127.0.0.1:8873")
    parser.add_argument("--web-root", type=Path, default=Path(__file__).resolve().parents[1] / "web")
    parser.add_argument("--release-id", default="development-unreleased")
    parser.add_argument("--release-manifest", type=Path)
    parser.add_argument("--expected-manifest-sha256")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    server = PlatformServer(args.port, args.legacy_origin, args.web_root, args.release_id,
                            release_manifest=args.release_manifest,
                            expected_manifest_sha256=args.expected_manifest_sha256)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
