"""Offline tests for the frozen dependency adapter quality gate."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "dependency_contract_review"
VERIFIER_PATH = ROOT / "scripts" / "verify_dependency_contract.py"

_CORRECT_ADAPTER = '''"""Stable app adapter for dependency v2."""
from collections.abc import Mapping


def load_document(client, document_key, stale_ok=False, cache=None):
    if cache is not None and not isinstance(cache, Mapping):
        raise TypeError("cache-must-be-a-mapping")
    cache_hint = None if cache is None else dict(cache)
    with client.open_document(
        document_key=document_key,
        allow_stale=stale_ok,
        cache_hint=cache_hint,
    ) as entry:
        payload = entry.payload
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise TypeError("payload-must-be-a-mapping-or-none")
    return dict(payload)
'''

_MUTANT_ADAPTERS = {
    "old_positional_dependency_call": '''def load_document(client, document_key, stale_ok=False, cache=None):
    return client.open_document(document_key, stale_ok, cache)
''',
    "caller_cache_forwarded_directly": '''def load_document(client, document_key, stale_ok=False, cache=None):
    with client.open_document(
        document_key=document_key, allow_stale=stale_ok, cache_hint=cache
    ) as entry:
        payload = entry.payload
    return None if payload is None else dict(payload)
''',
    "empty_payload_collapsed_to_none": '''from collections.abc import Mapping


def load_document(client, document_key, stale_ok=False, cache=None):
    hint = None if cache is None else dict(cache)
    with client.open_document(
        document_key=document_key, allow_stale=stale_ok, cache_hint=hint
    ) as entry:
        payload = entry.payload
    if not payload:
        return None
    if not isinstance(payload, Mapping):
        raise TypeError("payload-must-be-a-mapping-or-none")
    return dict(payload)
''',
    "dependency_exception_suppressed": '''from collections.abc import Mapping


def load_document(client, document_key, stale_ok=False, cache=None):
    hint = None if cache is None else dict(cache)
    try:
        with client.open_document(
            document_key=document_key, allow_stale=stale_ok, cache_hint=hint
        ) as entry:
            payload = entry.payload
    except Exception:
        return None
    return None if payload is None else dict(payload)
''',
    "entry_not_context_managed": '''from collections.abc import Mapping


def load_document(client, document_key, stale_ok=False, cache=None):
    hint = None if cache is None else dict(cache)
    entry = client.open_document(
        document_key=document_key, allow_stale=stale_ok, cache_hint=hint
    )
    payload = entry.payload
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise TypeError("payload-must-be-a-mapping-or-none")
    return dict(payload)
''',
}

_SPEC = importlib.util.spec_from_file_location("verify_dependency_contract", VERIFIER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
verifier = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verifier)


class DependencyContractVerifierTests(unittest.TestCase):
    def copy_fixture(self, parent: Path) -> Path:
        destination = parent / "candidate"
        shutil.copytree(FIXTURE, destination)
        return destination

    def invoke(self, fixture_dir: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
        completed = subprocess.run(
            [
                sys.executable,
                str(VERIFIER_PATH),
                "--fixture-dir",
                str(fixture_dir),
                "--timeout",
                "2",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.stderr, "")
        payload = json.loads(completed.stdout)
        self.assertEqual(
            set(payload), {"status", "passed", "failed", "exit_code"}
        )
        return completed, payload

    def test_seeded_incompatible_adapter_fails_independent_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self.copy_fixture(Path(temporary))
            completed, payload = self.invoke(candidate)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(payload["status"], "failed")
        self.assertGreater(payload["failed"], 0)
        self.assertEqual(payload["passed"] + payload["failed"], 10)

    def test_correct_adapter_passes_all_frozen_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self.copy_fixture(Path(temporary))
            (candidate / "ledger_adapter.py").write_text(
                _CORRECT_ADAPTER, encoding="utf-8"
            )
            completed, payload = self.invoke(candidate)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            payload,
            {"status": "passed", "passed": 10, "failed": 0, "exit_code": 0},
        )

    def test_plausible_contract_mutants_are_rejected(self):
        for mutant_name, source in _MUTANT_ADAPTERS.items():
            with self.subTest(mutant=mutant_name), tempfile.TemporaryDirectory() as temporary:
                candidate = self.copy_fixture(Path(temporary))
                (candidate / "ledger_adapter.py").write_text(source, encoding="utf-8")
                completed, payload = self.invoke(candidate)
                self.assertEqual(completed.returncode, 1)
                self.assertEqual(payload["status"], "failed")
                self.assertGreater(payload["failed"], 0)
                self.assertEqual(payload["passed"] + payload["failed"], 10)

    def test_adapter_stdout_is_not_reflected_in_gate_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self.copy_fixture(Path(temporary))
            (candidate / "ledger_adapter.py").write_text(
                "print('PRIVATE_FIXTURE_SENTINEL')\n" + _CORRECT_ADAPTER,
                encoding="utf-8",
            )
            completed, payload = self.invoke(candidate)
        self.assertEqual(completed.returncode, 0)
        self.assertNotIn("PRIVATE_FIXTURE_SENTINEL", completed.stdout)
        self.assertEqual(payload["passed"], 10)

    def test_timeout_is_bounded_and_returns_safe_failure(self):
        with mock.patch.object(
            verifier.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["python"], 0.01),
        ):
            result = verifier._run_contract(FIXTURE, timeout=0.01)
        self.assertEqual(
            result,
            {"status": "failed", "passed": 0, "failed": 10, "exit_code": 1},
        )

    def test_child_result_parser_rejects_malformed_or_wrong_count(self):
        self.assertIsNone(verifier._parse_child_result(b"not-json"))
        self.assertIsNone(
            verifier._parse_child_result(
                b'{"tests_run":10,"tests_run":10,"failures":0}'
            )
        )
        self.assertIsNone(
            verifier._parse_child_result(b'{"tests_run":9,"failures":0}')
        )
        self.assertIsNone(
            verifier._parse_child_result(
                b'{"tests_run":10,"failures":-1}'
            )
        )


if __name__ == "__main__":
    unittest.main()
