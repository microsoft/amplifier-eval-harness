"""Render private configuration for a stock NGINX credential proxy sidecar.

Only a trusted host-side caller may supply the upstream key or handle the rendered
text: the configuration itself contains that key. Mount it read-only ONLY into an
external proxy, never into a solver container, task profile, or shared namespace.
The solver receives only the temporary access token and proxy URL. Render/write
functions do not launch anything; the host-only context manager owns one Docker
proxy's lifecycle. The caller owns credential acquisition and solver isolation.

Successful responses require a trusted upstream that does not echo credentials;
this is not universal response DLP. NGINX syntax, wire behavior (including error
header suppression), and external isolation require separate runtime verification.
Stock NGINX specially propagates an upstream 401 challenge during interception.
A non-intercepting front listener strips that header from the already-sanitized
backend reply; the credential-bearing backend listens on loopback only.
NGINX reserves 499: it is excluded from error_page and retains that status.
A literal-key body filter handles plain credential echoes, including 499; this
does not cover encoded secrets or arbitrary response headers.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ipaddress import IPv4Address
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

_SECRET = re.compile(r"[A-Za-z0-9._~+/=-]{16,4096}", re.ASCII)
_URL = re.compile(
    r"https?://(?P<host>[A-Za-z0-9.-]+)(?::(?P<port>[0-9]{1,5}))?(?P<path>/[A-Za-z0-9/_-]*)",
    re.ASCII,
)
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", re.ASCII)
NGINX_IMAGE = "nginx@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94"
# All error_page-supported codes, including nonstandard upstream statuses.
# Reserved 499 cannot be remapped; its plain-body credential guard is separate.
_ERROR_STATUSES = " ".join(str(code) for code in range(300, 600) if code != 499)
_NONCE_LABEL = "amplifier-eval-harness.proxy-nonce"


def _validate_secrets(upstream_key: str, access_token: str) -> None:
    # Errors name fields, never values; all allowed characters are literal-safe
    # within quoted NGINX strings (no quoting, escaping, or variable expansion).
    if not isinstance(upstream_key, str) or _SECRET.fullmatch(upstream_key) is None:
        raise ValueError("Invalid upstream key")
    if not isinstance(access_token, str) or _SECRET.fullmatch(access_token) is None or len(access_token) < 32:
        raise ValueError("Invalid access token")
    if upstream_key == access_token:
        raise ValueError("Upstream key and access token must differ")


def _validate_upstream(upstream_base_url: str, allow_http_loopback: bool) -> tuple[str, str, str]:
    if not isinstance(allow_http_loopback, bool):
        raise ValueError("Loopback HTTP opt-in must be boolean")
    # Validate the raw input BEFORE urllib parsing: parsers may strip whitespace,
    # and parser exception messages may include malformed input.
    if not isinstance(upstream_base_url, str):
        raise ValueError("Invalid upstream URL; only literal DNS or IPv4 API bases are supported")
    match = _URL.fullmatch(upstream_base_url)
    if match is None:
        raise ValueError("Invalid upstream URL; only literal DNS or IPv4 API bases are supported")
    port_text = match.group("port")
    port = int(port_text) if port_text is not None else None
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid upstream port")
    parsed = urlsplit(upstream_base_url)
    hostname = parsed.hostname
    assert hostname is not None
    address = None
    try:
        address = IPv4Address(hostname)
    except ValueError:
        pass
    if address is None:
        # Reject malformed numeric addresses, including ambiguous integer forms.
        if re.fullmatch(r"[0-9.]+", hostname) is not None:
            raise ValueError("Invalid upstream IPv4 address")
        if len(hostname) > 253 or any(_DNS_LABEL.fullmatch(label) is None for label in hostname.split(".")):
            raise ValueError("Invalid upstream DNS hostname")
    if parsed.scheme == "https":
        if port is not None and port != 443:
            raise ValueError("Non-default HTTPS upstream ports are unsupported")
    elif not (allow_http_loopback and address is not None and address.is_loopback):
        raise ValueError("Upstream HTTPS is required except for explicitly enabled IPv4 loopback mocks")
    if "//" in parsed.path or ".." in parsed.path:
        raise ValueError("Invalid upstream API path")
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        raise ValueError("Upstream API base must explicitly end in /v1")
    host_header = hostname if port is None else f"{hostname}:{port}"
    return f"{parsed.scheme}://{host_header}{path}", host_header, hostname


def render_nginx_config(
    upstream_base_url: str,
    upstream_key: str,
    access_token: str,
    *,
    allow_http_loopback: bool = False,
) -> str:
    """Return confidential NGINX configuration without I/O.

    Supply a full API base ending in /v1, e.g. https://api.example.test/v1 or
    https://api.example.test/prefix/v1/. Bare origins are refused, not defaulted.
    DNS and strict IPv4 literals are supported; IPv6 is deliberately unsupported.
    HTTPS uses only port 443 (implicit or explicit). HTTP and other valid ports
    are permitted only for an explicitly enabled IPv4 loopback mock.

    Keys use the single-line alphabet [A-Za-z0-9._~+/=-], length 16..4096;
    access tokens use the same alphabet, length 32..4096, and must differ from
    the key. The caller must generate a random, temporary token. Never log or
    expose the returned string, including its repr, to the solver or agent.
    """
    _validate_secrets(upstream_key, access_token)
    base, host_header, hostname = _validate_upstream(upstream_base_url, allow_http_loopback)
    locations = []
    for suffix in ("/responses", "/chat/completions"):
        endpoint = f"/v1{suffix}"
        internal_endpoint = f"/_credential_proxy{suffix}"
        # Separate admission from upstream error_page handling: otherwise local
        # return 400/401/405 responses would also be rewritten to upstream 502.
        locations.append(f"""\
        location = {endpoint} {{
            if ($request_method != POST) {{ return 405; }}
            if ($http_authorization != "Bearer {access_token}") {{ return 401; }}
            if ($args != "") {{ return 400; }}
            if ($request_uri != "{endpoint}") {{ return 404; }}
            rewrite ^ {internal_endpoint} last;
        }}

        location = {internal_endpoint} {{
            internal;
            proxy_pass "{base}{suffix}";
            proxy_set_header Host "{host_header}";
            proxy_ssl_server_name on;
            proxy_ssl_name "{hostname}";
            proxy_ssl_verify on;
            proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
            proxy_pass_request_headers off;
            proxy_set_header Authorization "Bearer {upstream_key}";
            proxy_set_header Content-Type "application/json";
            proxy_set_header Accept "application/json";
            proxy_set_header Connection "";
            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_request_buffering off;
            # NGINX reserves 499, so it cannot be an error_page target. Remove
            # the literal credential from plain response bodies at every status.
            # This is not a general encoded-data or arbitrary-header DLP filter.
            sub_filter_types *;
            sub_filter "{upstream_key}" "";
            sub_filter_once off;
            proxy_read_timeout 600s;
            proxy_send_timeout 600s;
            proxy_connect_timeout 15s;
            proxy_next_upstream off;
            proxy_ignore_headers X-Accel-Redirect X-Accel-Buffering;
            proxy_cache off;
            proxy_intercept_errors on;
            proxy_hide_header Location;
            proxy_hide_header Refresh;
            proxy_hide_header WWW-Authenticate;
            proxy_hide_header Proxy-Authenticate;
            proxy_hide_header Set-Cookie;
            proxy_redirect off;
            error_page {_ERROR_STATUSES} =502 @upstream_error;
        }}
""")
    return """\
daemon off;
pid /tmp/nginx.pid;
error_log /dev/null;
events {}
http {
    server_tokens off;
    access_log off;
    default_type application/json;
    proxy_temp_path /tmp/proxy_temp;
    proxy_cache_path /tmp/proxy_cache keys_zone=unused:1m;
    client_body_temp_path /tmp/client_temp;
    fastcgi_temp_path /tmp/fastcgi_temp;
    uwsgi_temp_path /tmp/uwsgi_temp;
    scgi_temp_path /tmp/scgi_temp;

    # This front hop removes NGINX's special intercepted-401 challenge header.
    # It sees only the temporary solver token, never an upstream auth header.
    server {
        listen 8080 default_server;
        server_name _;
        client_max_body_size 10m;
        location / {
            proxy_pass http://127.0.0.1:8081;
            proxy_http_version 1.1;
            proxy_pass_request_headers off;
            proxy_set_header Authorization $http_authorization;
            proxy_set_header Content-Type application/json;
            proxy_set_header Accept application/json;
            proxy_set_header Connection "";
            proxy_hide_header WWW-Authenticate;
            proxy_hide_header Proxy-Authenticate;
            proxy_hide_header Location;
            proxy_hide_header Refresh;
            proxy_hide_header Set-Cookie;
            proxy_intercept_errors off;
            proxy_buffering off;
            proxy_request_buffering off;
            proxy_read_timeout 600s;
            proxy_send_timeout 600s;
            proxy_connect_timeout 15s;
            proxy_next_upstream off;
        }
    }

    server {
        listen 127.0.0.1:8081;
        server_name _;
        client_max_body_size 10m;
        location / { return 404; }
        location = /healthz {
            if ($request_method != GET) { return 405; }
            if ($request_uri != "/healthz") { return 404; }
            default_type text/plain;
            return 200 "ok";
        }
""" + "".join(locations) + """\
        location @upstream_error {
            default_type application/json;
            return 502 '{"error":"upstream_request_failed"}';
        }
    }
}
"""


def _check_private_directory(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise ValueError("Private directory must be an existing execution-UID-owned non-symlink directory with mode 0700")


def write_proxy_config(
    private_dir: Path,
    upstream_base_url: str,
    upstream_key: str,
    access_token: str,
    *,
    allow_http_loopback: bool = False,
) -> Path:
    """Exclusively create private_dir/nginx.conf (0600), flush and fsync it.

    The host directory must already exist, be non-symlink, have mode exactly
    0700, and be owned by the effective execution UID. Ancestor directories must
    be trusted by the caller. No directories are created and no permissions
    are repaired. Existing destinations (including symlinks) are never reused.
    A failed write can leave an exclusively created private partial file; the
    host operator owns cleanup. Mount the completed file read-only ONLY into
    the external proxy, never into the task DTU/profile.
    """
    config = render_nginx_config(
        upstream_base_url, upstream_key, access_token, allow_http_loopback=allow_http_loopback
    ).encode("ascii")
    directory_fd = None
    file_fd = None
    failure = None
    try:
        metadata = os.lstat(private_dir)
        _check_private_directory(metadata)
        directory_fd = os.open(private_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        opened = os.fstat(directory_fd)
        _check_private_directory(opened)
        if (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("Private directory changed during opening")
        file_fd = os.open(
            "nginx.conf",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        created = os.fstat(file_fd)
        if (
            not stat.S_ISREG(created.st_mode)
            or stat.S_IMODE(created.st_mode) != 0o600
            or created.st_uid != os.geteuid()
        ):
            raise ValueError("New proxy configuration must be an execution-UID-owned regular file with mode 0600")
        remaining = memoryview(config)
        while remaining:
            written = os.write(file_fd, remaining)
            if written <= 0:
                raise OSError("Proxy configuration write made no progress")
            remaining = remaining[written:]
        os.fsync(file_fd)
        os.fsync(directory_fd)
    except FileExistsError:
        failure = FileExistsError("Proxy configuration destination already exists")
    except OSError:
        failure = OSError("Cannot create private proxy configuration")
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)
    # Raise outside the handler so even __context__ cannot retain the original
    # filesystem exception's potentially sensitive path/message.
    if failure is not None:
        raise failure from None
    return private_dir / "nginx.conf"


def _write_proxy_intent(private_dir: Path, name: str, nonce: str) -> Path:
    """Persist a private, exclusive, nonsecret recovery identity before create."""
    content = json.dumps({"name": name, "nonce": nonce, "image": NGINX_IMAGE}).encode("ascii")
    directory_fd = None
    file_fd = None
    failure = None
    try:
        metadata = private_dir.lstat()
        _check_private_directory(metadata)
        directory_fd = os.open(private_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        opened = os.fstat(directory_fd)
        _check_private_directory(opened)
        if (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("Private directory changed during opening")
        file_fd = os.open(
            "proxy-intent.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600, dir_fd=directory_fd,
        )
        created = os.fstat(file_fd)
        if (
            not stat.S_ISREG(created.st_mode)
            or stat.S_IMODE(created.st_mode) != 0o600
            or created.st_uid != os.geteuid()
        ):
            raise ValueError("Proxy intent must be an execution-UID-owned regular file with mode 0600")
        remaining = memoryview(content)
        while remaining:
            written = os.write(file_fd, remaining)
            if written <= 0:
                raise OSError("Proxy intent write made no progress")
            remaining = remaining[written:]
        os.fsync(file_fd)
        os.fsync(directory_fd)
    except OSError:
        failure = OSError("Cannot create private proxy intent")
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)
    if failure is not None:
        raise failure from None
    return private_dir / "proxy-intent.json"


def _container_absent(inspected: subprocess.CompletedProcess, identity: str) -> bool:
    # Bind known Docker case variants to only the exact requested name or ID.
    absence = inspected.stderr.decode("utf-8", errors="replace").strip().lower()
    return inspected.returncode != 0 and absence in {
        f"error: no such object: {identity}",
        f"error: no such container: {identity}",
        f"error response from daemon: no such container: {identity}",
    }


def _owned_proxy_identifier(inspected: subprocess.CompletedProcess, name: str, nonce: str) -> str:
    """Accept only a singleton container with this exact name, nonce and full ID."""
    if inspected.returncode == 0:
        try:
            objects = json.loads(inspected.stdout)
            if isinstance(objects, list) and len(objects) == 1 and isinstance(objects[0], dict):
                container = objects[0]
                config = container.get("Config")
                labels = config.get("Labels") if isinstance(config, dict) else None
                identifier = container.get("Id")
                if (
                    container.get("Name") == "/" + name
                    and isinstance(labels, dict) and labels.get(_NONCE_LABEL) == nonce
                    and isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{64}", identifier)
                ):
                    return identifier
        except (ValueError, UnicodeError):
            pass
    raise RuntimeError("PROXY_CLEANUP_UNVERIFIED")


@contextmanager
def credential_proxy(
    private_dir: Path,
    upstream_base_url: str,
    upstream_key: str,
    bind_ipv4: str,
    record_resource: Callable[[str, str], None],
) -> Iterator[tuple[str, str]]:
    """Yield (solver API base, temporary token) for a separately mounted proxy.

    The caller owns the DTU and must not give it host/Docker management access.
    record_resource(id, "active") runs immediately after create, BEFORE start;
    "reaped" is recorded only after exact-container removal and absence checks.
    This context manager is host-only; never execute it in the solver. Neither
    subprocess arguments/environment nor yielded values contain the upstream key.
    It removes its private credential file after confirmed container removal.
    A private nonsecret intent precedes create; an ambiguous result is recovered
    only by exact name/nonce/full-ID inspection. Unknown cleanup retains both
    files and fails. Recorder errors are advisory to cleanup but still reported,
    with callback exception text suppressed because it may contain credentials.
    """
    address = IPv4Address(bind_ipv4)
    if address.is_unspecified or not (address.is_private or address.is_loopback):
        raise ValueError("Proxy must bind an explicit private or loopback IPv4 address")
    if not callable(record_resource):
        raise ValueError("Resource recorder is required")
    token = secrets.token_urlsafe(32)
    config = write_proxy_config(private_dir, upstream_base_url, upstream_key, token)
    config_identity = config.stat()
    identifier = None
    name = "eval-api-" + secrets.token_hex(6)
    nonce = secrets.token_hex(16)
    intent = None
    create_attempted = False
    # The Docker client runs on the host. Do not forward model credentials to it.
    environment = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL")}

    def docker(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", *args], capture_output=True, timeout=25, env=environment, check=False
        )

    def record(state: str) -> None:
        try:
            record_resource(identifier, state)
        except Exception:
            raise RuntimeError("PROXY_RESOURCE_RECORD_FAILED") from None

    try:
        intent_path = _write_proxy_intent(private_dir, name, nonce)
        intent_identity = intent_path.lstat()
        intent = intent_path
        create_attempted = True
        created = docker(
            "create", "--name", name, "--label", f"{_NONCE_LABEL}={nonce}",
            "--user", f"{os.geteuid()}:{os.getegid()}",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "32", "--memory", "128m",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=32m",
            "--publish", f"{address}::8080",
            "--mount", f"type=bind,src={config.absolute()},dst=/etc/nginx/nginx.conf,readonly",
            "--entrypoint", "nginx", NGINX_IMAGE, "-c", "/etc/nginx/nginx.conf",
        )
        if created.returncode != 0:
            raise RuntimeError("PROXY_CREATE_FAILED")
        candidate = created.stdout.decode("ascii").strip()
        if re.fullmatch(r"[a-f0-9]{64}", candidate) is None:
            raise RuntimeError("PROXY_CREATE_ID_INVALID")
        identifier = candidate
        record("active")
        if docker("start", identifier).returncode != 0:
            raise RuntimeError("PROXY_START_FAILED")
        ports = docker("port", identifier, "8080/tcp")
        mapping = ports.stdout.decode("ascii").strip()
        match = re.fullmatch(re.escape(str(address)) + r":([0-9]{1,5})", mapping)
        if ports.returncode != 0 or match is None:
            raise RuntimeError("PROXY_PORT_INVALID")
        port = int(match.group(1))
        if not 1 <= port <= 65535:
            raise RuntimeError("PROXY_PORT_INVALID")
        origin = f"http://{address}:{port}"
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + 10
        while True:
            try:
                with opener.open(origin + "/healthz", timeout=1) as response:
                    if response.status == 200 and response.read(8) == b"ok":
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("PROXY_HEALTH_TIMEOUT")
            time.sleep(0.1)
        yield origin + "/v1", token
    finally:
        removed = not create_attempted
        recorder_failed = False
        try:
            if identifier is None and create_attempted:
                inspected = docker("inspect", "--type", "container", name)
                # A client timeout/nonzero return cannot prove daemon-side
                # creation has finished. Even exact-name absence is ambiguous:
                # retain recovery files rather than deleting a late mount's key.
                identifier = _owned_proxy_identifier(inspected, name, nonce)
                try:
                    record("active")
                except RuntimeError:
                    recorder_failed = True
            if identifier is not None:
                removal = docker("rm", "-f", identifier)
                inspected = docker("inspect", identifier)
                removed = removal.returncode == 0 and _container_absent(inspected, identifier)
                if removed:
                    try:
                        record("reaped")
                    except RuntimeError:
                        recorder_failed = True
        except Exception:
            # Unknown lookup/removal outcomes never authorize file deletion.
            removed = False
        if removed:
            current = config.lstat()
            if (
                not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != (config_identity.st_dev, config_identity.st_ino)
            ):
                raise RuntimeError("PROXY_CONFIG_IDENTITY_CHANGED")
            config.unlink()
            if intent is not None:
                current = intent.lstat()
                if (
                    not stat.S_ISREG(current.st_mode)
                    or (current.st_dev, current.st_ino) != (intent_identity.st_dev, intent_identity.st_ino)
                ):
                    raise RuntimeError("PROXY_INTENT_IDENTITY_CHANGED")
                intent.unlink()
            if recorder_failed:
                raise RuntimeError("PROXY_RESOURCE_RECORD_FAILED") from None
        else:
            raise RuntimeError("PROXY_CLEANUP_UNVERIFIED") from None
