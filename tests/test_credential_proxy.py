"""Render-only and local file tests; NOT NGINX, network, or isolation tests.

All credentials below are fake canaries. No service, subprocess, environment
credential lookup, or container is used. Parent owns execution and wire checks.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amplifier_eval_harness.credential_proxy import (
    _ERROR_STATUSES,
    NGINX_IMAGE,
    credential_proxy,
    render_nginx_config,
    write_proxy_config,
)

_BASE = "https://api.example.test/v1"
_KEY = "FAKE_UPSTREAM_CANARY_0123456789"
_TOKEN = "FAKE_ACCESS_CANARY_0123456789_abcdefgh"


def _render(base: str = _BASE, **kwargs: object) -> str:
    return render_nginx_config(base, _KEY, _TOKEN, **kwargs)


def _location(config: str, endpoint: str) -> str:
    """Extract a rendered location, not a general NGINX parser."""
    return config.split(f"        location = {endpoint} {{\n", 1)[1].split("\n        }\n", 1)[0]


def _proxy_location(config: str, endpoint: str) -> str:
    return _location(config, "/_credential_proxy" + endpoint.removeprefix("/v1"))


class RenderOnlyCredentialProxyTests(unittest.TestCase):
    def assert_private_error(self, operation, *canaries: str) -> None:
        with self.assertRaises(ValueError) as caught:
            operation()
        for canary in (*canaries, _KEY, _TOKEN):
            self.assertNotIn(canary, str(caught.exception))
            self.assertNotIn(canary, repr(caught.exception))

    def test_render_only_fixed_hosts_paths_and_tls_verification_directives(self) -> None:
        config = _render("https://API.example.test:443/prefix/v1/")
        for endpoint, suffix in (
            ("/v1/responses", "/responses"),
            ("/v1/chat/completions", "/chat/completions"),
        ):
            block = _proxy_location(config, endpoint)
            self.assertIn("internal;", block)
            for directive in (
                f'proxy_pass "https://api.example.test:443/prefix/v1{suffix}";',
                'proxy_set_header Host "api.example.test:443";',
                "proxy_ssl_server_name on;",
                'proxy_ssl_name "api.example.test";',
                "proxy_ssl_verify on;",
                "proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;",
            ):
                self.assertIn(directive, block)
        self.assertEqual(config.count("proxy_pass "), 3)
        self.assertNotIn("proxy_ssl_verify off", config)
        self.assertNotIn("$host", config)

    def test_render_only_each_endpoint_has_return_only_admission_checks(self) -> None:
        config = _render()
        for endpoint in ("/v1/responses", "/v1/chat/completions"):
            block = _location(config, endpoint)
            self.assertIn("if ($request_method != POST) { return 405; }", block)
            self.assertIn(f'if ($http_authorization != "Bearer {_TOKEN}") {{ return 401; }}', block)
            self.assertIn('if ($args != "") { return 400; }', block)
            self.assertIn(f'if ($request_uri != "{endpoint}") {{ return 404; }}', block)
            self.assertIn(f"rewrite ^ /_credential_proxy{endpoint.removeprefix('/v1')} last;", block)
            self.assertLess(block.index("return 400;"), block.index("rewrite ^ "))
            # error_page belongs ONLY in the internal proxy location, otherwise
            # the admission failures themselves would be converted to 502.
            self.assertNotIn("error_page ", block)
            self.assertNotIn("proxy_pass ", block)
        self.assertIn("location / { return 404; }", config)
        self.assertNotIn("location ~", config)

    def test_render_only_client_headers_disabled_and_upstream_headers_literal(self) -> None:
        config = _render()
        for endpoint in ("/v1/responses", "/v1/chat/completions"):
            block = _proxy_location(config, endpoint)
            self.assertIn("proxy_pass_request_headers off;", block)
            self.assertIn(f'proxy_set_header Authorization "Bearer {_KEY}";', block)
            self.assertIn('proxy_set_header Content-Type "application/json";', block)
            self.assertIn('proxy_set_header Accept "application/json";', block)
            self.assertIn('proxy_set_header Connection "";', block)
            header_lines = [line.strip() for line in block.splitlines() if "proxy_set_header " in line]
            self.assertEqual(len(header_lines), 5)
            self.assertFalse(any("$" in line for line in header_lines))
            self.assertFalse(any(_TOKEN in line for line in header_lines))
        self.assertNotIn("$http_cookie", config)

    def test_render_only_streaming_limits_and_no_retries_or_accel_control(self) -> None:
        config = _render()
        self.assertIn("client_max_body_size 10m;", config)
        for endpoint in ("/v1/responses", "/v1/chat/completions"):
            block = _proxy_location(config, endpoint)
            for directive in (
                "proxy_http_version 1.1;",
                "proxy_buffering off;",
                "proxy_request_buffering off;",
                "proxy_read_timeout 600s;",
                "proxy_send_timeout 600s;",
                "proxy_connect_timeout 15s;",
                "proxy_next_upstream off;",
                "proxy_ignore_headers X-Accel-Redirect X-Accel-Buffering;",
                "proxy_cache off;",
            ):
                self.assertIn(directive, block)

    def test_render_only_literal_body_guard_is_present_on_both_upstream_paths(self) -> None:
        config = _render()
        for endpoint in ("/v1/responses", "/v1/chat/completions"):
            block = _proxy_location(config, endpoint)
            self.assertIn("sub_filter_types *;", block)
            self.assertIn(f'sub_filter "{_KEY}" "";', block)
            self.assertIn("sub_filter_once off;", block)

    def test_render_only_replacement_cannot_equal_an_accepted_key(self) -> None:
        key = "redacted_upstream_credential"
        config = render_nginx_config(_BASE, key, _TOKEN)
        for endpoint in ("/v1/responses", "/v1/chat/completions"):
            self.assertIn(f'sub_filter "{key}" "";', _proxy_location(config, endpoint))

    def test_render_only_common_upstream_errors_redirects_have_fixed_local_error_target(self) -> None:
        config = _render()
        for endpoint in ("/v1/responses", "/v1/chat/completions"):
            block = _proxy_location(config, endpoint)
            self.assertIn("proxy_intercept_errors on;", block)
            self.assertIn("proxy_hide_header Location;", block)
            self.assertIn("proxy_hide_header Refresh;", block)
            self.assertIn("proxy_hide_header WWW-Authenticate;", block)
            self.assertIn("proxy_hide_header Proxy-Authenticate;", block)
            self.assertIn("proxy_hide_header Set-Cookie;", block)
            self.assertIn("proxy_redirect off;", block)
            error_line = next(
                line.strip() for line in block.splitlines() if line.strip().startswith("error_page ")
            )
            self.assertTrue(error_line.endswith("=502 @upstream_error;"))
            codes = set(error_line.split()[1:-2])
            self.assertTrue({"301", "302", "303", "307", "308", "400", "401", "403", "404", "429", "500", "502", "503", "504"} <= codes)
            self.assertTrue(all(300 <= int(code) <= 599 for code in codes))
        error_block = config.split("        location @upstream_error {", 1)[1]
        self.assertIn("return 502 '{\"error\":\"upstream_request_failed\"}';", error_block)
        self.assertNotIn("proxy_pass", error_block)
        self.assertNotIn(_KEY, error_block)
        self.assertNotIn(_TOKEN, error_block)
        self.assertNotIn("add_header", error_block)

    def test_render_only_total_supported_status_set_excludes_reserved_499(self) -> None:
        expected = set(range(300, 600)) - {499}
        codes = [int(code) for code in _ERROR_STATUSES.split()]
        self.assertEqual(set(codes), expected)
        self.assertEqual(len(codes), 299)
        self.assertTrue({412, 418, 451, 599} <= set(codes))
        for endpoint in ("/v1/responses", "/v1/chat/completions"):
            block = _proxy_location(_render(), endpoint)
            error_line = next(
                line.strip() for line in block.splitlines() if line.strip().startswith("error_page ")
            )
            self.assertEqual({int(code) for code in error_line.split()[1:-2]}, expected)

    def test_render_only_private_logs_tmp_paths_and_unauthenticated_local_health(self) -> None:
        config = _render()
        for directive in (
            "daemon off;",
            "pid /tmp/nginx.pid;",
            "events {}",
            "server_tokens off;",
            "access_log off;",
            "error_log /dev/null;",
            "proxy_temp_path /tmp/proxy_temp;",
            "proxy_cache_path /tmp/proxy_cache keys_zone=unused:1m;",
            "client_body_temp_path /tmp/client_temp;",
            "fastcgi_temp_path /tmp/fastcgi_temp;",
            "uwsgi_temp_path /tmp/uwsgi_temp;",
            "scgi_temp_path /tmp/scgi_temp;",
            "listen 8080 default_server;",
        ):
            self.assertIn(directive, config)
        health = _location(config, "/healthz")
        self.assertIn("if ($request_method != GET) { return 405; }", health)
        self.assertIn('return 200 "ok";', health)
        self.assertNotIn("proxy_", health)
        self.assertNotIn("authorization", health)
        self.assertEqual(config.count("location "), 8)
        self.assertEqual(config.count("            internal;"), 2)
        self.assertNotIn("stub_status", config)
        self.assertNotIn("include ", config)

    def test_render_only_front_hop_strips_special_intercepted_auth_challenge(self) -> None:
        config = _render()
        front, backend = config.split("listen 127.0.0.1:8081;", 1)
        self.assertIn("proxy_pass http://127.0.0.1:8081;", front)
        self.assertIn("proxy_intercept_errors off;", front)
        self.assertIn("proxy_hide_header WWW-Authenticate;", front)
        self.assertNotIn(_KEY, front)
        self.assertIn(_KEY, backend)

    def test_render_only_strict_ipv4_https_and_explicit_loopback_http(self) -> None:
        self.assertIn('proxy_pass "https://192.0.2.1/v1/responses";', _render("https://192.0.2.1/v1"))
        for host in ("127.0.0.1", "127.1.2.3"):
            base = f"http://{host}:18080/v1"
            self.assert_private_error(lambda base=base: _render(base), base)
            config = _render(base, allow_http_loopback=True)
            self.assertIn(f'proxy_pass "{base}/responses";', config)
            self.assertIn(f'proxy_set_header Host "{host}:18080";', config)
        for port in (1, 65535):
            self.assertIn(f":{port}/v1/responses", _render(f"http://127.0.0.1:{port}/v1", allow_http_loopback=True))
        for base in ("http://localhost/v1", "http://192.0.2.1/v1", "http://api.example.test/v1"):
            self.assert_private_error(lambda base=base: _render(base, allow_http_loopback=True), base)

    def test_render_only_url_injection_and_unsupported_inputs_never_echo(self) -> None:
        bad_urls = (
            "https://api.example.test",
            "https://api.example.test/",
            "https://api.example.test/v2",
            "https://api.example.test/v1/responses",
            "https://user:FAKE_URL_CANARY@api.example.test/v1",
            "https://api.example.test/v1?FAKE_QUERY_CANARY=1",
            "https://api.example.test/v1?",
            "https://api.example.test/v1#FAKE_FRAGMENT_CANARY",
            "https://api.example.test/v1#",
            " https://api.example.test/v1",
            "https://api.example.test/v1\n",
            "https://api.example.\ttest/v1",
            "https://api.example.test/\x00/v1",
            "https://api.exämple.test/v1",
            "https://api.example.test/路径/v1",
            "https://$upstream/v1",
            "https://api.example.test/$secret/v1",
            "https://api.example.test/a/../v1",
            "https://api.example.test//v1",
            "https://api.example.test/v1//",
            "https://api.example.test/%76%31",
            "https://api.example.test/a;FAKE_PATH_CANARY/v1",
            'https://api.example.test/"FAKE_PATH_CANARY"/v1',
            "https://api.example.test/'FAKE_PATH_CANARY'/v1",
            "https://api.example.test/\\FAKE_PATH_CANARY/v1",
            "https://api.example.test:8443/v1",
            "https://api.example.test:0/v1",
            "https://api.example.test:65536/v1",
            "https://api.example.test:bad/v1",
            "https://api.example.test:/v1",
            "https://api.example.test:+443/v1",
            "https://api.example.test.:443/v1",
            "https://-api.example.test/v1",
            "https://api-.example.test/v1",
            "https://api..example.test/v1",
            "https://api_example.test/v1",
            "https://999.0.0.1/v1",
            "https://127.000.0.1/v1",
            "https://2130706433/v1",
            "https://[::1]/v1",
            "https://[FAKE_IPV6_CANARY]/v1",
            "ftp://api.example.test/v1",
        )
        for base in bad_urls:
            with self.subTest(base=base):
                self.assert_private_error(lambda base=base: _render(base), base)
        for base in ("http://127.0.0.1:0/v1", "http://127.0.0.1:65536/v1"):
            self.assert_private_error(lambda base=base: _render(base, allow_http_loopback=True), base)
        self.assert_private_error(lambda: _render(allow_http_loopback="yes"))

    def test_render_only_dns_label_and_total_length_validation(self) -> None:
        for host in ("a" * 64 + ".example.test", ".".join(["a" * 63] * 5)):
            base = f"https://{host}/v1"
            self.assert_private_error(lambda base=base: _render(base), base)
        self.assertIn("prefix_1/v1/responses", _render("https://api-1.example.test/prefix_1/v1"))

    def test_render_only_secret_injections_lengths_and_equality_never_echo(self) -> None:
        invalid = (
            "FAKE_INJECTION_CANARY\ninclude x;",
            "FAKE_INJECTION_CANARY\r",
            "FAKE_INJECTION_CANARY\t",
            "FAKE INJECTION CANARY",
            'FAKE_INJECTION_CANARY"',
            "FAKE_INJECTION_CANARY'",
            "FAKE_INJECTION_CANARY\\",
            "FAKE_INJECTION_CANARY$host",
            "FAKE_INJECTION_CANARY;",
            "FAKE_INJECTION_CANARY{",
            "FAKE_INJECTION_CANARY#",
            "FAKE_INJECTION_CANARYé",
            "FAKE_INJECTION_CANARY\x00",
            "A" * 4097,
            "A" * 15,
            "",
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assert_private_error(
                    lambda value=value: render_nginx_config(_BASE, value, _TOKEN), *([value] if value else [])
                )
                self.assert_private_error(
                    lambda value=value: render_nginx_config(_BASE, _KEY, value), *([value] if value else [])
                )
        self.assert_private_error(lambda: render_nginx_config(_BASE, _KEY, "A" * 31), "A" * 31)
        self.assert_private_error(lambda: render_nginx_config(_BASE, _TOKEN, _TOKEN))
        self.assert_private_error(lambda: render_nginx_config(_BASE, None, _TOKEN))
        self.assert_private_error(lambda: render_nginx_config(_BASE, _KEY, None))
        self.assert_private_error(lambda: render_nginx_config(None, _KEY, _TOKEN))

    def test_render_only_allowed_secret_alphabet_and_boundaries_are_literal(self) -> None:
        key = "A" * 16
        token = "Az09._~+/=-" * 4
        config = render_nginx_config(_BASE, key, token)
        self.assertIn(f'Authorization "Bearer {key}";', config)
        self.assertIn(f'!= "Bearer {token}"', config)
        config = render_nginx_config(_BASE, "K" * 4096, "T" * 4096)
        self.assertIn(f'Authorization "Bearer {"K" * 4096}";', config)
        self.assertIn(f'!= "Bearer {"T" * 4096}"', config)


class PrivateProxyConfigFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private = self.root / "private"
        self.private.mkdir(mode=0o700)

    def write(self, directory: Path | None = None) -> Path:
        return write_proxy_config(self.private if directory is None else directory, _BASE, _KEY, _TOKEN)

    def test_local_file_create_0600_with_full_content_and_fsync(self) -> None:
        with (
            patch("amplifier_eval_harness.credential_proxy.os.fsync", wraps=os.fsync) as fsync,
            patch("amplifier_eval_harness.credential_proxy.os.open", wraps=os.open) as opened,
        ):
            path = self.write()
        self.assertEqual(path, self.private / "nginx.conf")
        self.assertEqual(stat.S_IMODE(path.lstat().st_mode), 0o600)
        self.assertEqual(path.lstat().st_uid, os.geteuid())
        self.assertEqual(stat.S_IMODE(self.private.lstat().st_mode), 0o700)
        self.assertEqual(path.read_text(encoding="ascii"), _render())
        self.assertEqual(fsync.call_count, 2)
        self.assertEqual(opened.call_count, 2)
        directory_flags = opened.call_args_list[0].args[1]
        file_call = opened.call_args_list[1]
        self.assertTrue(directory_flags & os.O_NOFOLLOW)
        self.assertTrue(directory_flags & os.O_DIRECTORY)
        self.assertTrue(file_call.args[1] & os.O_EXCL)
        self.assertTrue(file_call.args[1] & os.O_NOFOLLOW)
        self.assertEqual(file_call.args[2], 0o600)
        self.assertIn("dir_fd", file_call.kwargs)

    def test_local_file_refuses_existing_without_reuse_or_permission_repair(self) -> None:
        path = self.private / "nginx.conf"
        path.write_text("unchanged fake file")
        path.chmod(0o644)
        with self.assertRaises(FileExistsError):
            self.write()
        self.assertEqual(path.read_text(), "unchanged fake file")
        self.assertEqual(stat.S_IMODE(path.lstat().st_mode), 0o644)

    def test_local_file_refuses_destination_symlinks_without_touching_targets(self) -> None:
        target = self.root / "outside"
        target.write_text("unchanged target")
        path = self.private / "nginx.conf"
        path.symlink_to(target)
        with self.assertRaises(FileExistsError):
            self.write()
        self.assertTrue(path.is_symlink())
        self.assertEqual(target.read_text(), "unchanged target")
        path.unlink()
        missing = self.root / "missing"
        path.symlink_to(missing)
        with self.assertRaises(FileExistsError):
            self.write()
        self.assertTrue(path.is_symlink())
        self.assertFalse(missing.exists())

    def test_local_file_refuses_symlink_parent_directory(self) -> None:
        alias = self.root / "alias"
        alias.symlink_to(self.private, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.write(alias)
        self.assertTrue(alias.is_symlink())
        self.assertFalse((self.private / "nginx.conf").exists())

    def test_local_file_refuses_nonprivate_modes_without_altering_directory(self) -> None:
        for mode in (0o755, 0o750, 0o770, 0o777, 0o500, 0o1700):
            self.private.chmod(mode)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.write()
            self.assertEqual(stat.S_IMODE(self.private.lstat().st_mode), mode)
            self.assertFalse((self.private / "nginx.conf").exists())

    def test_local_file_refuses_foreign_owner_using_synthetic_uid(self) -> None:
        other_uid = self.private.lstat().st_uid + 1
        with patch("amplifier_eval_harness.credential_proxy.os.geteuid", return_value=other_uid):
            with self.assertRaises(ValueError):
                self.write()
        self.assertFalse((self.private / "nginx.conf").exists())

    def test_local_file_refuses_missing_directory_and_nondirectory(self) -> None:
        missing = self.root / "missing"
        with self.assertRaises(OSError):
            self.write(missing)
        self.assertFalse(missing.exists())
        plain = self.root / "plain"
        plain.write_text("unchanged")
        with self.assertRaises(ValueError):
            self.write(plain)
        self.assertEqual(plain.read_text(), "unchanged")

    def test_local_file_invalid_credentials_do_not_create_destination(self) -> None:
        with self.assertRaises(ValueError):
            write_proxy_config(self.private, _BASE, _KEY + "\n", _TOKEN)
        self.assertFalse((self.private / "nginx.conf").exists())

    def test_local_file_loopback_opt_in_is_explicitly_forwarded(self) -> None:
        base = "http://127.0.0.1:18080/v1"
        path = write_proxy_config(self.private, base, _KEY, _TOKEN, allow_http_loopback=True)
        self.assertEqual(path.read_text(encoding="ascii"), _render(base, allow_http_loopback=True))

    def test_local_file_os_errors_do_not_echo_canaries_or_paths(self) -> None:
        failure = OSError(f"FAKE_IO_CANARY {_KEY} {_TOKEN}")
        with patch("amplifier_eval_harness.credential_proxy.os.write", side_effect=failure):
            with self.assertRaises(OSError) as caught:
                self.write()
        for value in ("FAKE_IO_CANARY", _KEY, _TOKEN, str(self.private)):
            self.assertNotIn(value, str(caught.exception))
            self.assertNotIn(value, repr(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(stat.S_IMODE((self.private / "nginx.conf").lstat().st_mode), 0o600)


class MockedProxyLifecycleTests(unittest.TestCase):
    """Mocked orchestration contract, not container/isolation qualification."""

    def setUp(self):
        # No real environment lookup, credentials, or readiness/network calls.
        self.environment = patch("amplifier_eval_harness.credential_proxy.os.environ", {})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.opener = patch("amplifier_eval_harness.credential_proxy.build_opener")
        self.mock_opener = self.opener.start()
        self.addCleanup(self.opener.stop)

    def test_mocked_creation_registration_cleanup_and_no_key_in_process_or_yield(self):
        identifier = "a" * 64
        actions = []

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _):
                return b"ok"

        class Opener:
            def open(self, *_args, **_kwargs):
                return Response()

        def run(argv, **kwargs):
            self.assertNotIn(_KEY, repr(argv))
            self.assertNotIn(_KEY, repr(kwargs["env"]))
            operation = argv[1]
            actions.append(operation)
            if operation == "create":
                intent = path / "proxy-intent.json"
                self.assertEqual(stat.S_IMODE(intent.lstat().st_mode), 0o600)
                self.assertEqual(intent.lstat().st_uid, os.geteuid())
                document = json.loads(intent.read_text(encoding="ascii"))
                self.assertEqual(set(document), {"name", "nonce", "image"})
                self.assertEqual(document["name"], argv[argv.index("--name") + 1])
                self.assertEqual(document["image"], NGINX_IMAGE)
                self.assertEqual(
                    argv[argv.index("--label") + 1],
                    "amplifier-eval-harness.proxy-nonce=" + document["nonce"],
                )
                for canary in (_BASE, _KEY):
                    self.assertNotIn(canary, intent.read_text(encoding="ascii"))
                self.assertIn("--cap-drop", argv)
                self.assertIn("--read-only", argv)
                self.assertFalse(any("docker.sock" in x or "incus" in x for x in argv))
                return subprocess.CompletedProcess(argv, 0, identifier.encode(), b"")
            if operation == "start":
                self.assertIn("registered-active", actions)
            if operation == "port":
                return subprocess.CompletedProcess(argv, 0, b"127.0.0.1:12345\n", b"")
            if operation == "inspect":
                return subprocess.CompletedProcess(argv, 1, b"", b"error: no such object: " + identifier.encode())
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        def record(container_id, state):
            self.assertEqual(container_id, identifier)
            actions.append("registered-" + state)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            path.chmod(0o700)
            with (
                patch("amplifier_eval_harness.credential_proxy.subprocess.run", side_effect=run),
                patch("amplifier_eval_harness.credential_proxy.build_opener", return_value=Opener()),
                patch("amplifier_eval_harness.credential_proxy.os.fsync", wraps=os.fsync) as fsync,
                patch("amplifier_eval_harness.credential_proxy.os.open", wraps=os.open) as opened,
                credential_proxy(path, _BASE, _KEY, "127.0.0.1", record) as handle,
            ):
                self.assertEqual(handle[0], "http://127.0.0.1:12345/v1")
                self.assertNotIn(_KEY, repr(handle))
                self.assertTrue((path / "nginx.conf").exists())
                self.assertNotIn(handle[1], (path / "proxy-intent.json").read_text(encoding="ascii"))
            self.assertFalse((path / "nginx.conf").exists())
            self.assertFalse((path / "proxy-intent.json").exists())
            self.assertEqual(fsync.call_count, 4)
            intent_open = next(call for call in opened.call_args_list if call.args[0] == "proxy-intent.json")
            self.assertTrue(intent_open.args[1] & os.O_EXCL)
            self.assertTrue(intent_open.args[1] & os.O_NOFOLLOW)
            self.assertEqual(intent_open.args[2], 0o600)
            self.assertIn("dir_fd", intent_open.kwargs)
            self.assertLess(actions.index("create"), actions.index("registered-active"))
            self.assertLess(actions.index("registered-active"), actions.index("start"))
            self.assertLess(actions.index("rm"), actions.index("registered-reaped"))

    def test_mocked_cleanup_failure_is_not_reported_reaped(self):
        identifier = "b" * 64
        states = []

        def run(argv, **_kwargs):
            if argv[1] == "create":
                return subprocess.CompletedProcess(argv, 0, identifier.encode(), b"")
            return subprocess.CompletedProcess(argv, 1, b"", b"unavailable")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            path.chmod(0o700)
            with patch("amplifier_eval_harness.credential_proxy.subprocess.run", side_effect=run):
                with self.assertRaisesRegex(RuntimeError, "PROXY_CLEANUP_UNVERIFIED"):
                    with credential_proxy(path, _BASE, _KEY, "127.0.0.1", lambda _, state: states.append(state)):
                        self.fail("failed start cannot yield a proxy")
            self.assertEqual(states, ["active"])
            self.assertTrue((path / "nginx.conf").exists())
            self.assertTrue((path / "proxy-intent.json").exists())

    def assert_ambiguous_create_recovered(self, create_result):
        identifier = "c" * 64
        actions = []
        document = {}

        def run(argv, **_kwargs):
            operation = argv[1]
            actions.append((operation, argv[-1]))
            if operation == "create":
                document.update(json.loads((path / "proxy-intent.json").read_text(encoding="ascii")))
                return create_result(argv)
            if operation == "inspect" and argv[-1] == document["name"]:
                self.assertEqual(argv, ["docker", "inspect", "--type", "container", document["name"]])
                result = [{
                    "Name": "/" + document["name"], "Id": identifier,
                    "Config": {"Labels": {"amplifier-eval-harness.proxy-nonce": document["nonce"]}},
                }]
                return subprocess.CompletedProcess(argv, 0, json.dumps(result).encode(), b"")
            if operation == "rm":
                self.assertEqual(argv, ["docker", "rm", "-f", identifier])
                self.assertIn(("active", identifier), actions)
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            if operation == "inspect" and argv[-1] == identifier:
                return subprocess.CompletedProcess(argv, 1, b"", f"error: no such object: {identifier}".encode())
            self.fail("unexpected Docker operation")

        def record(container_id, state):
            self.assertEqual(container_id, identifier)
            actions.append((state, container_id))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            path.chmod(0o700)
            with patch("amplifier_eval_harness.credential_proxy.subprocess.run", side_effect=run):
                with self.assertRaises((subprocess.TimeoutExpired, RuntimeError)):
                    with credential_proxy(path, _BASE, _KEY, "127.0.0.1", record):
                        self.fail("ambiguous create cannot yield")
            self.assertEqual([action for action, _ in actions], ["create", "inspect", "active", "rm", "inspect", "reaped"])
            self.assertFalse((path / "nginx.conf").exists())
            self.assertFalse((path / "proxy-intent.json").exists())
            self.mock_opener.assert_not_called()

    def test_mocked_create_timeout_recovers_owned_nonce_and_exact_id(self):
        def timeout(argv):
            raise subprocess.TimeoutExpired(argv, 25)

        self.assert_ambiguous_create_recovered(timeout)

    def test_mocked_failed_create_recovers_owned_nonce_and_exact_id(self):
        self.assert_ambiguous_create_recovered(lambda argv: subprocess.CompletedProcess(argv, 1, b"", b"failed"))

    def test_mocked_invalid_create_id_recovers_only_inspected_full_id(self):
        self.assert_ambiguous_create_recovered(lambda argv: subprocess.CompletedProcess(argv, 0, b"invalid-id", b""))

    def assert_ambiguous_create_unverified(self, lookup):
        states = []
        actions = []
        document = {}

        def run(argv, **_kwargs):
            actions.append(argv[1])
            if argv[1] == "create":
                document.update(json.loads((path / "proxy-intent.json").read_text(encoding="ascii")))
                raise subprocess.TimeoutExpired(argv, 25)
            self.assertEqual(argv, ["docker", "inspect", "--type", "container", document["name"]])
            return lookup(argv, document)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            path.chmod(0o700)
            with patch("amplifier_eval_harness.credential_proxy.subprocess.run", side_effect=run):
                with self.assertRaisesRegex(RuntimeError, "^PROXY_CLEANUP_UNVERIFIED$") as caught:
                    with credential_proxy(path, _BASE, _KEY, "127.0.0.1", lambda _, state: states.append(state)):
                        self.fail("ambiguous create cannot yield")
            self.assertTrue(caught.exception.__suppress_context__)
            self.assertEqual(actions, ["create", "inspect"])
            self.assertEqual(states, [])
            self.assertTrue((path / "nginx.conf").exists())
            self.assertEqual(json.loads((path / "proxy-intent.json").read_text(encoding="ascii")), document)
            self.assertEqual(stat.S_IMODE((path / "proxy-intent.json").lstat().st_mode), 0o600)
            self.mock_opener.assert_not_called()

    def test_mocked_ambiguous_inspect_failures_retain_config_and_intent_without_rm(self):
        def timeout(argv, _document):
            raise subprocess.TimeoutExpired(argv, 25)

        for lookup in (
            timeout,
            lambda argv, _: subprocess.CompletedProcess(argv, 1, b"", b"daemon unavailable"),
            lambda argv, _: subprocess.CompletedProcess(argv, 1, b"", b"error: no such object: different-name"),
            lambda argv, _: subprocess.CompletedProcess(argv, 0, b"not-json", b""),
            lambda argv, _: subprocess.CompletedProcess(argv, 0, b"[]", b""),
        ):
            with self.subTest(lookup=lookup):
                self.assert_ambiguous_create_unverified(lookup)

    def test_mocked_ambiguous_inspect_identity_mismatches_never_remove_other_container(self):
        for mismatch in ("name", "nonce", "missing-label", "short-id", "nonhex-id", "multiple"):
            def lookup(argv, document, mismatch=mismatch):
                container = {
                    "Name": "/" + document["name"], "Id": "d" * 64,
                    "Config": {"Labels": {"amplifier-eval-harness.proxy-nonce": document["nonce"]}},
                }
                if mismatch == "name":
                    container["Name"] += "-other"
                elif mismatch == "nonce":
                    container["Config"]["Labels"]["amplifier-eval-harness.proxy-nonce"] = "other"
                elif mismatch == "missing-label":
                    container["Config"]["Labels"] = None
                elif mismatch == "short-id":
                    container["Id"] = "d" * 12
                elif mismatch == "nonhex-id":
                    container["Id"] = "z" * 64
                objects = [container, container] if mismatch == "multiple" else [container]
                return subprocess.CompletedProcess(argv, 0, json.dumps(objects).encode(), b"")

            with self.subTest(mismatch=mismatch):
                self.assert_ambiguous_create_unverified(lookup)

    def test_mocked_timeout_then_exact_name_absence_retains_late_create_recovery(self):
        self.assert_ambiguous_create_unverified(
            lambda argv, document: subprocess.CompletedProcess(
                argv, 1, b"", f"error: no such object: {document['name']}".encode()
            )
        )

    def test_mocked_failed_create_exact_name_absence_is_still_unverified(self):
        for absence in ("Error: No such object: ", "error: no such container: ", "Error response from daemon: No such container: "):
            actions = []
            states = []

            with self.subTest(absence=absence), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                path.chmod(0o700)

                def run(argv, actions=actions, path=path, absence=absence, **_kwargs):
                    actions.append(argv[1])
                    if argv[1] == "create":
                        return subprocess.CompletedProcess(argv, 1, b"", b"failed")
                    name = json.loads((path / "proxy-intent.json").read_text(encoding="ascii"))["name"]
                    self.assertEqual(argv, ["docker", "inspect", "--type", "container", name])
                    return subprocess.CompletedProcess(argv, 1, b"", (absence + name).encode())

                with patch("amplifier_eval_harness.credential_proxy.subprocess.run", side_effect=run):
                    with self.assertRaisesRegex(RuntimeError, "^PROXY_CLEANUP_UNVERIFIED$"):
                        with credential_proxy(
                            path, _BASE, _KEY, "127.0.0.1", lambda _, state, states=states: states.append(state)
                        ):
                            self.fail("failed create cannot yield")
                self.assertEqual(actions, ["create", "inspect"])
                self.assertEqual(states, [])
                self.assertTrue((path / "nginx.conf").exists())
                self.assertTrue((path / "proxy-intent.json").exists())

    def test_mocked_existing_intent_is_not_overwritten_and_create_is_not_attempted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            path.chmod(0o700)
            intent = path / "proxy-intent.json"
            intent.write_text("existing nonsecret recovery intent")
            intent.chmod(0o600)
            with patch("amplifier_eval_harness.credential_proxy.subprocess.run") as run:
                with self.assertRaisesRegex(OSError, "^Cannot create private proxy intent$"):
                    with credential_proxy(path, _BASE, _KEY, "127.0.0.1", lambda *_: None):
                        self.fail("existing intent cannot be reused")
            run.assert_not_called()
            self.assertEqual(intent.read_text(), "existing nonsecret recovery intent")
            self.assertFalse((path / "nginx.conf").exists())

    def test_mocked_reaped_recorder_failure_cannot_skip_keyfile_unlink_or_echo_keys(self):
        identifier = "e" * 64
        actions = []
        states = []

        def run(argv, **_kwargs):
            actions.append(argv[1])
            if argv[1] == "create":
                return subprocess.CompletedProcess(argv, 0, identifier.encode(), b"")
            if argv[1] == "start":
                return subprocess.CompletedProcess(argv, 1, b"", b"failed")
            if argv[1] == "rm":
                self.assertEqual(argv, ["docker", "rm", "-f", identifier])
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            self.assertEqual(argv, ["docker", "inspect", identifier])
            return subprocess.CompletedProcess(argv, 1, b"", f"error: no such object: {identifier}".encode())

        def record(container_id, state):
            self.assertEqual(container_id, identifier)
            states.append(state)
            if state == "reaped":
                raise ValueError(f"FAKE_RECORDER_CANARY {_KEY}")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            path.chmod(0o700)
            with patch("amplifier_eval_harness.credential_proxy.subprocess.run", side_effect=run):
                with self.assertRaisesRegex(RuntimeError, "^PROXY_RESOURCE_RECORD_FAILED$") as caught:
                    with credential_proxy(path, _BASE, _KEY, "127.0.0.1", record):
                        self.fail("failed start cannot yield")
            self.assertEqual(actions, ["create", "start", "rm", "inspect"])
            self.assertEqual(states, ["active", "reaped"])
            self.assertFalse((path / "nginx.conf").exists())
            self.assertFalse((path / "proxy-intent.json").exists())
            self.assertTrue(caught.exception.__suppress_context__)
            for canary in (_KEY, "FAKE_RECORDER_CANARY"):
                self.assertNotIn(canary, str(caught.exception))
                self.assertNotIn(canary, repr(caught.exception))

    def test_local_validation_rejects_unspecified_bind_before_creating_secret_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            path.chmod(0o700)
            with self.assertRaises(ValueError):
                with credential_proxy(path, _BASE, _KEY, "0.0.0.0", lambda *_: None):
                    self.fail("unsafe binding cannot start")
            self.assertFalse((path / "nginx.conf").exists())


if __name__ == "__main__":
    unittest.main()
