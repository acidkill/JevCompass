from __future__ import annotations

import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]

import pilot_contract_triage_pair as runner
import pilot_cli_core as core


class PrivateEventRetentionTests(unittest.TestCase):
    def _fixture(self, root: Path) -> Path:
        fixture = root / "fixture"
        fixture.mkdir()
        return fixture

    def _run(self, root: Path, *, source: str, retain: bool, timeout: int = 5,
             parser_error: bool = False):
        fixture = self._fixture(root)
        output = root / "private-output"
        output.mkdir(mode=0o700)
        measurement = output / "agent-measurement.json"
        with (
            mock.patch.object(
                runner.common, "_cli_command",
                return_value=[sys.executable, "-c", source],
            ),
            mock.patch.object(runner, "_event_receipts",
                              side_effect=RuntimeError("parser failed")
                              if parser_error else lambda *a, **k: ({"parsed": True}, "answer")),
        ):
            return runner._run_arm(
                codex="fake", model="test-model", reasoning_effort="low",
                prompt="synthetic", fixture=fixture, home=root / "home",
                timeout=timeout, treatment=False, measurement_path=measurement,
                retain_private_events=retain,
            ), measurement, output

    def test_success_archives_private_jsonl_and_receipt_exposes_metadata_only(self):
        raw_line = json.dumps({"event": "private-sentinel", "secret": "do-not-copy-to-receipt"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = f"print({raw_line!r}, flush=True)"
            (result, answer), measurement, output = self._run(
                root, source=source, retain=True,
            )
            archive = output / "agent-measurement-events.jsonl"
            self.assertEqual(answer, "answer")
            self.assertTrue(archive.is_file())
            self.assertEqual(archive.read_text(encoding="utf-8"), raw_line + "\n")
            self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
            self.assertTrue(result["private_event_archive_captured"])
            self.assertEqual(result["private_event_archive_bytes"], archive.stat().st_size)
            self.assertNotIn("private-sentinel", json.dumps(result))
            self.assertNotIn("private-sentinel", measurement.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(measurement.read_text())["status"], "completed")

    def test_parser_error_keeps_archive_and_redacted_measurement(self):
        raw_line = json.dumps({"event": "parser-failure-private"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (result, answer), measurement, output = self._run(
                root, source=f"print({raw_line!r}, flush=True)",
                retain=True, parser_error=True,
            )
            archive = output / "agent-measurement-events.jsonl"
            self.assertEqual(result["failure"], "event_parser_error")
            self.assertIsNone(answer)
            self.assertEqual(archive.read_text(encoding="utf-8"), raw_line + "\n")
            self.assertTrue(result["private_event_archive_captured"])
            self.assertNotIn("parser-failure-private", json.dumps(result))
            self.assertTrue(measurement.is_file())

    def test_timeout_keeps_events_collected_before_timeout(self):
        raw_line = json.dumps({"event": "timeout-private"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = f"import time; print({raw_line!r}, flush=True); time.sleep(3)"
            (result, answer), measurement, output = self._run(
                root, source=source, retain=True, timeout=1,
            )
            archive = output / "agent-measurement-events.jsonl"
            self.assertEqual(result["failure"], "timeout")
            self.assertIsNone(answer)
            self.assertEqual(archive.read_text(encoding="utf-8"), raw_line + "\n")
            self.assertTrue(result["private_event_archive_captured"])
            self.assertTrue(measurement.is_file())

    def test_over_limit_archive_is_not_created(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "oversized.jsonl"
            result = runner._write_private_event_archive(
                ["x" * (core.MAX_EVENT_BYTES + 1)], path,
            )
            self.assertEqual(result, {
                "private_event_archive_captured": False,
                "private_event_archive_bytes": None,
            })
            self.assertFalse(path.exists())

    def test_existing_or_symlink_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "events.jsonl"
            target.write_text("original\n", encoding="utf-8")
            result = runner._write_private_event_archive(["new"], target)
            self.assertFalse(result["private_event_archive_captured"])
            self.assertEqual(target.read_text(encoding="utf-8"), "original\n")

            outside = root / "outside.txt"
            outside.write_text("untouched\n", encoding="utf-8")
            link = root / "linked-events.jsonl"
            link.symlink_to(outside)
            result = runner._write_private_event_archive(["new"], link)
            self.assertFalse(result["private_event_archive_captured"])
            self.assertEqual(outside.read_text(encoding="utf-8"), "untouched\n")

    def test_default_path_does_not_create_archive_or_add_metadata(self):
        raw_line = json.dumps({"event": "default-private"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (result, answer), _measurement, output = self._run(
                root, source=f"print({raw_line!r}, flush=True)", retain=False,
            )
            self.assertEqual(answer, "answer")
            self.assertFalse((output / "agent-measurement-events.jsonl").exists())
            self.assertNotIn("private_event_archive_captured", result)
            self.assertNotIn("private_event_archive_bytes", result)

    def test_cli_exposes_explicit_archive_optin(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            with self.assertRaises(SystemExit) as exit_info:
                runner.main(["--help"])
        self.assertEqual(exit_info.exception.code, 0)
        self.assertIn("--retain-private-events", stdout.getvalue())

    def test_pair_explicit_optin_is_forwarded_to_both_arms(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            seen = []

            def strict_arm(*, retain_private_events, **kwargs):
                seen.append((kwargs["treatment"], retain_private_events))
                return runner._empty_arm("synthetic_offline"), None

            with (
                mock.patch.object(runner, "_verify_codex_version", return_value=True),
                mock.patch.object(core, "_copy_auth", return_value=True),
                mock.patch.object(runner, "_run_arm", side_effect=strict_arm),
            ):
                runner.run_pair(
                    codex="fake", model="test-model", reasoning_effort="low",
                    timeout=5, seed=1, output_dir=root / "pair",
                    fixture_source=runner.FIXTURE,
                    retain_private_events=True,
                )
            self.assertEqual(sorted(seen), [(False, True), (True, True)])


if __name__ == "__main__":
    unittest.main()
