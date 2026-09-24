from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from jevcompass import cli, credentials


class CredentialResolutionTests(unittest.TestCase):
    def test_environment_key_takes_precedence_without_starting_worker(self):
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-secret"}), \
                mock.patch.object(credentials, "_run_worker") as run_worker:
            self.assertEqual(credentials.resolve_api_key(), "env-secret")
        run_worker.assert_not_called()

    def test_system_keyring_key_is_used_when_environment_is_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(credentials, "_run_worker", return_value={
                    "ok": True,
                    "credential": "keyring-secret",
                }) as run_worker:
            self.assertEqual(credentials.resolve_api_key(), "keyring-secret")
        run_worker.assert_called_once_with("get")

    def test_missing_or_malformed_keyring_results_fail_open_without_a_key(self):
        for result in (None, {}, {"ok": False}, {"ok": True, "credential": None}, {"ok": True, "credential": 3}):
            with self.subTest(result=result), mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(credentials, "_run_worker", return_value=result):
                self.assertEqual(credentials.resolve_api_key(), "")

    def test_lookup_worker_uses_devnull_and_short_timeout(self):
        completed = subprocess.CompletedProcess([], 0, stdout='{"ok":true,"credential":"secret"}')
        with mock.patch.object(credentials.subprocess, "run", return_value=completed) as run:
            self.assertEqual(credentials._run_worker("get"), {
                "ok": True,
                "credential": "secret",
            })
        command = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertEqual(command, [
            sys.executable, "-m", "jevcompass.credentials", "--worker", "get",
        ])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], credentials.LOOKUP_TIMEOUT_SECONDS)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("secret", " ".join(command))

    def test_worker_sends_secret_only_over_stdin_and_never_in_arguments(self):
        secret = "stdin-only-openrouter-secret"
        completed = subprocess.CompletedProcess([], 0, stdout='{"ok":true}')
        with mock.patch.object(credentials.subprocess, "run", return_value=completed) as run:
            result = credentials._run_worker("set", payload={"credential": secret}, timeout=30.0)
        self.assertEqual(result, {"ok": True})
        command = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertNotIn(secret, " ".join(command))
        self.assertEqual(json.loads(kwargs["input"]), {"credential": secret})
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)

    def test_worker_timeout_bad_json_and_nonzero_exit_are_ignored(self):
        with mock.patch.object(credentials.subprocess, "run", side_effect=subprocess.TimeoutExpired("worker", 0.35)):
            self.assertIsNone(credentials._run_worker("get"))
        for completed in (
            subprocess.CompletedProcess([], 0, stdout="not-json"),
            subprocess.CompletedProcess([], 1, stdout='{"ok":true,"credential":"secret"}'),
            subprocess.CompletedProcess([], 0, stdout="[]"),
        ):
            with self.subTest(completed=completed), mock.patch.object(credentials.subprocess, "run", return_value=completed):
                self.assertIsNone(credentials._run_worker("get"))

    def test_untrusted_or_custom_backend_modules_are_rejected(self):
        for module in (
            "keyrings.alt.file",
            "keyring.backends.fail",
            "keyring.backends.null",
            "third_party.password_store",
        ):
            backend_type = type("Backend", (), {"__module__": module})
            with self.subTest(module=module):
                with mock.patch.dict(sys.modules, {
                    "keyring": mock.Mock(get_keyring=lambda: backend_type()),
                }):
                    self.assertIsNone(credentials._trusted_backend())

    def test_known_native_backend_modules_are_accepted(self):
        for module in credentials._TRUSTED_BACKEND_MODULES:
            backend_type = type("Backend", (), {"__module__": module})
            with self.subTest(module=module):
                with mock.patch.dict(sys.modules, {
                    "keyring": mock.Mock(get_keyring=lambda: backend_type()),
                }):
                    self.assertIsInstance(credentials._trusted_backend(), backend_type)


class CredentialStoreOperationTests(unittest.TestCase):
    def test_worker_uses_fixed_entry_and_status_never_returns_the_key(self):
        secret = "worker-only-test-secret"
        values = {}

        class FakeBackend:
            __module__ = "keyring.backends.SecretService"

            def get_password(self, service, username):
                self.assert_entry(service, username)
                return values.get((service, username))

            def set_password(self, service, username, credential):
                self.assert_entry(service, username)
                values[(service, username)] = credential

            def delete_password(self, service, username):
                self.assert_entry(service, username)
                del values[(service, username)]

            @staticmethod
            def assert_entry(service, username):
                if (service, username) != (credentials.SERVICE_NAME, credentials.ACCOUNT_NAME):
                    raise AssertionError("unexpected keyring entry")

        backend = FakeBackend()
        fake_module = mock.Mock(get_keyring=lambda: backend)
        with mock.patch.dict(sys.modules, {"keyring": fake_module}):
            self.assertTrue(credentials._operate("set", {"credential": secret})["ok"])
            status = credentials._operate("status", {})
            self.assertEqual(status, {
                "ok": True,
                "secure_store_available": True,
                "configured": True,
            })
            self.assertNotIn(secret, json.dumps(status))
            self.assertEqual(credentials._operate("get", {})["credential"], secret)
            self.assertTrue(credentials._operate("delete", {})["ok"])
            self.assertNotIn((credentials.SERVICE_NAME, credentials.ACCOUNT_NAME), values)


class CredentialStatusTests(unittest.TestCase):
    def test_status_reports_environment_without_inspecting_or_emitting_secret(self):
        secret = "private-openrouter-token"
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": secret}), \
                mock.patch.object(credentials, "_run_worker") as run_worker:
            result = credentials.credential_status()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = credentials.auth_main("status")
        self.assertEqual(result["source"], "environment")
        self.assertTrue(result["configured"])
        self.assertEqual(exit_code, 0)
        self.assertNotIn(secret, json.dumps(result) + output.getvalue())
        run_worker.assert_not_called()

    def test_status_reports_system_store_without_returning_secret(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(credentials, "_run_worker", return_value={
                    "ok": True,
                    "secure_store_available": True,
                    "configured": True,
                }):
            result = credentials.credential_status()
        self.assertEqual(result, {
            "configured": True,
            "source": "system-keyring",
            "secure_store_available": True,
        })


class CredentialAuthTests(unittest.TestCase):
    class TTY(io.StringIO):
        def isatty(self):
            return True

    def test_set_and_delete_refuse_noninteractive_stdin(self):
        output = io.StringIO()
        error = io.StringIO()
        with mock.patch.object(credentials.sys, "stdin", io.StringIO("")), \
                mock.patch.object(credentials.sys, "stderr", error), \
                contextlib.redirect_stdout(output):
            self.assertEqual(credentials.auth_main("set"), 2)
            self.assertEqual(credentials.auth_main("delete"), 2)
        self.assertIn("interactive terminal", error.getvalue())

    def test_set_reads_hidden_confirmation_and_never_prints_secret(self):
        secret = "never-print-this-secret"
        output = io.StringIO()
        error = io.StringIO()
        with mock.patch.object(credentials.sys, "stdin", self.TTY()), \
                mock.patch.object(credentials.sys, "stderr", self.TTY()), \
                mock.patch.object(credentials.getpass, "getpass", side_effect=[secret, secret]), \
                mock.patch.object(credentials, "_run_worker", return_value={"ok": True}) as run_worker, \
                contextlib.redirect_stdout(output):
            exit_code = credentials.auth_main("set")
        self.assertEqual(exit_code, 0)
        self.assertIn("stored in the system keyring", output.getvalue())
        self.assertNotIn(secret, output.getvalue() + error.getvalue())
        args, kwargs = run_worker.call_args
        self.assertEqual(args, ("set",))
        self.assertEqual(kwargs["payload"], {"credential": secret})
        self.assertNotIn(secret, repr(args))
        self.assertNotIn(secret, repr(kwargs.get("timeout")))

    def test_cli_auth_status_is_a_supported_command(self):
        with mock.patch.object(cli, "auth_main", return_value=0) as auth:
            self.assertEqual(cli.main(["auth", "status"]), 0)
        auth.assert_called_once_with("status")

    def test_delete_requires_explicit_confirmation(self):
        output = io.StringIO()
        with mock.patch.object(credentials.sys, "stdin", self.TTY()), \
                mock.patch.object(credentials.sys, "stderr", self.TTY()), \
                mock.patch.object(credentials.getpass, "getpass", return_value="DELETE"), \
                mock.patch.object(credentials, "_run_worker", return_value={"ok": True}) as run_worker, \
                contextlib.redirect_stdout(output):
            exit_code = credentials.auth_main("delete")
        self.assertEqual(exit_code, 0)
        self.assertIn("environment variable", output.getvalue())
        run_worker.assert_called_once_with("delete", timeout=credentials.AUTH_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main()
