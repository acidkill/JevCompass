#!/usr/bin/env python3
"""Run a frozen, bounded contract suite against a synthetic ledger adapter.

The fixture is trusted synthetic Python, not a security sandbox. Its stdout and
stderr are discarded; only fixed aggregate counts cross a dedicated pipe.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 2.0
MAX_TIMEOUT_SECONDS = 15.0
MAX_RESULT_BYTES = 1024
EXPECTED_TEST_COUNT = 10

_CHILD_SUITE = r'''
import json
import os
import sys
import unittest
from collections.abc import Mapping
from inspect import Parameter, signature

result_fd = int(sys.argv[1])
fixture_root = sys.argv[2]
sys.path.insert(0, fixture_root)


def report(tests_run, failures):
    payload = json.dumps(
        {"tests_run": tests_run, "failures": failures},
        separators=(",", ":"),
    ).encode("ascii")
    os.write(result_fd, payload)


try:
    from ledger_adapter import load_document
except BaseException:
    report(0, 1)
    raise SystemExit(1)


class Entry:
    def __init__(self, payload=None, payload_error=None):
        self.value = payload
        self.payload_error = payload_error
        self.exit_count = 0

    @property
    def payload(self):
        if self.payload_error is not None:
            raise self.payload_error
        return self.value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exit_count += 1
        return False


class Client:
    def __init__(self, payload=None, open_error=None, payload_error=None,
                 mutate_cache=False):
        self.entry = Entry(payload, payload_error)
        self.open_error = open_error
        self.mutate_cache = mutate_cache
        self.calls = []

    def open_document(self, *, document_key, allow_stale, cache_hint):
        self.calls.append((document_key, allow_stale, cache_hint))
        if self.open_error is not None:
            raise self.open_error
        if self.mutate_cache and cache_hint is not None:
            cache_hint["dependency_write"] = "isolated"
        return self.entry


class ContractTests(unittest.TestCase):
    def test_public_signature_preserves_legacy_positional_shape(self):
        parameters = list(signature(load_document).parameters.values())
        self.assertEqual(
            [parameter.name for parameter in parameters],
            ["client", "document_key", "stale_ok", "cache"],
        )
        self.assertTrue(all(
            parameter.kind == Parameter.POSITIONAL_OR_KEYWORD
            for parameter in parameters
        ))
        self.assertEqual(parameters[2].default, False)
        self.assertIsNone(parameters[3].default)
        client = Client({"v": 1})
        self.assertEqual(load_document(client, "d-1"), {"v": 1})
        self.assertEqual(client.calls[0][:2], ("d-1", False))

    def test_dependency_v2_mapping_uses_keywords_and_values(self):
        cache = {"region": "west"}
        client = Client({"v": 2})
        self.assertEqual(
            load_document(client, document_key="d-2", stale_ok=True, cache=cache),
            {"v": 2},
        )
        key, allow_stale, cache_hint = client.calls[0]
        self.assertEqual((key, allow_stale), ("d-2", True))
        self.assertEqual(cache_hint, cache)
        self.assertIsNot(cache_hint, cache)

    def test_dependency_mutation_cannot_change_caller_cache(self):
        cache = {"limit": 3}
        client = Client({"ok": True}, mutate_cache=True)
        load_document(client, "d-3", cache=cache)
        self.assertEqual(cache, {"limit": 3})
        self.assertEqual(client.calls[0][2], {"limit": 3, "dependency_write": "isolated"})

    def test_mapping_payload_is_returned_as_an_independent_shallow_dict(self):
        nested = {"labels": ["kept"]}
        payload = {"nested": nested, "enabled": True}
        client = Client(payload)
        result = load_document(client, "d-4")
        self.assertIs(type(result), dict)
        self.assertEqual(result, payload)
        self.assertIsNot(result, payload)
        self.assertIs(result["nested"], nested)

    def test_none_and_empty_values_are_distinct_valid_boundaries(self):
        empty_client = Client({})
        empty_cache = {}
        self.assertEqual(load_document(empty_client, "", cache=empty_cache), {})
        self.assertEqual(empty_client.calls[0][0], "")
        self.assertEqual(empty_client.calls[0][2], {})
        self.assertIsNot(empty_client.calls[0][2], empty_cache)
        none_client = Client(None)
        self.assertIsNone(load_document(none_client, "d-5", cache=None))
        self.assertIsNone(none_client.calls[0][2])

    def test_dependency_exception_instance_propagates_unchanged(self):
        failure = LookupError("sentinel")
        client = Client(open_error=failure)
        with self.assertRaises(LookupError) as caught:
            load_document(client, "d-6")
        self.assertIs(caught.exception, failure)

    def test_payload_exception_propagates_and_context_exits(self):
        failure = RuntimeError("payload-sentinel")
        client = Client(payload_error=failure)
        with self.assertRaises(RuntimeError) as caught:
            load_document(client, "d-7")
        self.assertIs(caught.exception, failure)
        self.assertEqual(client.entry.exit_count, 1)

    def test_entry_is_closed_exactly_once_on_success(self):
        client = Client({"closed": True})
        self.assertEqual(load_document(client, "d-8"), {"closed": True})
        self.assertEqual(client.entry.exit_count, 1)

    def test_non_mapping_payload_raises_type_error_and_still_closes(self):
        client = Client(["not", "a", "mapping"])
        with self.assertRaises(TypeError):
            load_document(client, "d-9")
        self.assertEqual(client.entry.exit_count, 1)

    def test_caller_cache_and_dependency_payload_are_not_mutated(self):
        cache = {"mode": "readonly"}
        payload = {"value": 9}
        client = Client(payload)
        result = load_document(client, "d-10", cache=cache)
        self.assertEqual(cache, {"mode": "readonly"})
        self.assertEqual(payload, {"value": 9})
        result["value"] = 10
        self.assertEqual(payload, {"value": 9})


suite = unittest.defaultTestLoader.loadTestsFromTestCase(ContractTests)
result = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0).run(suite)
report(result.testsRun, len(result.failures) + len(result.errors))
raise SystemExit(0 if result.wasSuccessful() else 1)
'''


def _safe_result(status: str, passed: int, failed: int) -> dict[str, object]:
    return {
        "status": status,
        "passed": passed,
        "failed": failed,
        "exit_code": 0 if status == "passed" else 1,
    }


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_child_result(payload: bytes) -> tuple[int, int] | None:
    if not payload or len(payload) > MAX_RESULT_BYTES:
        return None
    try:
        value = json.loads(
            payload.decode("ascii"), object_pairs_hook=_reject_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"tests_run", "failures"}
        or type(value["tests_run"]) is not int
        or type(value["failures"]) is not int
        or value["tests_run"] < 0
        or value["failures"] < 0
        or value["tests_run"] != EXPECTED_TEST_COUNT
        or value["failures"] > value["tests_run"]
    ):
        return None
    return value["tests_run"], value["failures"]


def _run_contract(fixture_dir: Path, timeout: float) -> dict[str, object]:
    adapter = fixture_dir / "ledger_adapter.py"
    if (
        not fixture_dir.is_dir()
        or not adapter.is_file()
        or adapter.is_symlink()
        or not (fixture_dir / "ledger_archive.py").is_file()
    ):
        return _safe_result("failed", 0, EXPECTED_TEST_COUNT)

    read_fd, write_fd = os.pipe()
    child_result: tuple[int, int] | None = None
    exit_code = 1
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                _CHILD_SUITE,
                str(write_fd),
                str(fixture_dir.resolve()),
            ],
            cwd=fixture_dir,
            env={"PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(write_fd,),
            timeout=timeout,
            check=False,
        )
        exit_code = completed.returncode
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    finally:
        os.close(write_fd)
    try:
        payload = os.read(read_fd, MAX_RESULT_BYTES + 1)
        child_result = _parse_child_result(payload)
    except OSError:
        child_result = None
    finally:
        os.close(read_fd)

    if child_result is None:
        return _safe_result("failed", 0, EXPECTED_TEST_COUNT)
    tests_run, failures = child_result
    if exit_code == 0 and failures == 0:
        return _safe_result("passed", tests_run, 0)
    passed = max(0, tests_run - failures)
    # A successful-looking payload cannot override a failed child exit status.
    if exit_code != 0 and failures == 0:
        failures = 1
        passed = max(0, tests_run - 1)
    return _safe_result("failed", passed, failures)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= MAX_TIMEOUT_SECONDS:
        parser.error("timeout must be positive and at most 15 seconds")
    result = _run_contract(args.fixture_dir, args.timeout)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
