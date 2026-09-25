"""Offline and metadata-safety tests for the supplemental C01/R10 CLI runner."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_codex_setup_pair.py"
sys.path.insert(0, str(ROOT / "src"))
SPEC = importlib.util.spec_from_file_location("pilot_codex_setup_pair", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)


class ReverseRandom:
    def shuffle(self, values):
        values.reverse()


def make_skill(root: Path) -> Path:
    skill = root / "openai-docs"
    skill.mkdir()
    (skill / "SKILL.md").write_text("Synthetic stock-skill stand-in for offline tests.\n", encoding="utf-8")
    (skill / "guide.md").write_text("No network access is used by this test.\n", encoding="utf-8")
    return skill


class CodexSetupPairTests(unittest.TestCase):
    def test_mock_pair_is_supplemental_randomized_and_metadata_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = make_skill(root)
            with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "do-not-forward"}):
                result = runner.run_pair(
                    mode="mock", model=None, timeout=3, rng=ReverseRandom(),
                    auth_root=root, skill_source=skill,
                )
                isolated = runner._core._isolated_environment(
                    home=root / "profile", isolated_python=root / "profile" / "python",
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["case_order"], ["R10", "C01"])
            self.assertFalse(result["core_20_denominator_included"])
            self.assertFalse(result["openrouter_key_forwarded"])
            self.assertNotIn("OPENROUTER_API_KEY", isolated)
            self.assertTrue(result["cases"]["C01"]["fixture_copies_identical"])
            self.assertTrue(result["cases"]["C01"]["skill_copies_identical"])
            self.assertEqual(
                result["cases"]["C01"]["arm_order"], ["treatment", "baseline"],
            )
            self.assertTrue(result["cases"]["C01"]["arms"]["treatment"]["advice_id_before_first_tool"])
            self.assertFalse(result["cases"]["C01"]["arms"]["baseline"]["advice_id_before_first_tool"])
            self.assertTrue(result["cases"]["R10"]["routine_negative_control"])
            self.assertTrue(result["cases"]["R10"]["arms"]["treatment"]["routine_no_advice"])
            self.assertFalse(result["cases"]["R10"]["arms"]["treatment"]["advice_id_before_first_tool"])
            rendered = json.dumps(result)
            for private_text in (
                runner.CASE_PROMPTS["C01"], runner.CASE_PROMPTS["R10"],
                "Synthetic stock-skill stand-in", "do-not-forward",
            ):
                self.assertNotIn(private_text, rendered)

    def test_dry_run_installs_same_skill_and_hooks_only_for_treatment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = make_skill(root)
            source_auth = root / "source" / "auth.json"
            source_auth.parent.mkdir()
            secret_payload = '{"access_token":"synthetic-test-secret"}'
            source_auth.write_text(secret_payload, encoding="utf-8")
            digest = runner._skill_digest(skill)
            baseline_home = root / "baseline"
            treatment_home = root / "treatment"
            baseline = runner._prepare_profile(
                home=baseline_home, skill_source=skill, skill_sha256=digest,
                treatment=False, auth_source=source_auth,
            )
            treatment = runner._prepare_profile(
                home=treatment_home, skill_source=skill, skill_sha256=digest,
                treatment=True, auth_source=source_auth,
            )
            self.assertTrue(baseline["auth_copied"])
            self.assertTrue(treatment["auth_copied"])
            for label, profile in (("baseline", baseline), ("treatment", treatment)):
                self.assertEqual(
                    stat.S_IMODE((profile["codex_home"] / "auth.json").stat().st_mode),
                    0o600,
                )
                installed = profile["codex_home"] / "skills" / "openai-docs"
                self.assertEqual(runner._skill_digest(installed), digest)
            self.assertFalse((baseline["codex_home"] / "hooks.json").exists())
            self.assertTrue((treatment["codex_home"] / "hooks.json").is_file())
            config = json.loads(
                (treatment["codex_home"] / "hooks.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(config["hooks"]), {"UserPromptSubmit", "SubagentStart"})
            self.assertEqual(
                hashlib.sha256((baseline["codex_home"] / "auth.json").read_bytes()).digest(),
                hashlib.sha256((treatment["codex_home"] / "auth.json").read_bytes()).digest(),
            )

    def test_dry_run_does_not_require_auth_or_launch_codex(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = make_skill(root)
            with mock.patch.object(runner, "_source_auth_root", return_value=root), \
                 mock.patch.object(runner, "_source_skill_root", return_value=skill):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    status = runner.main(["--dry-run"])
            self.assertEqual(status, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["mode"], "dry-run")
            self.assertFalse(result["cases"]["C01"]["arms"]["baseline"]["model_called"])
            self.assertTrue(result["cases"]["C01"]["arms"]["treatment"]["hooks_configured"])
            self.assertTrue(result["cases"]["R10"]["routine_negative_control"])

    def test_c02_preflight_is_same_prompt_in_both_arms_and_not_core_case(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = make_skill(root)
            result = runner.run_pair(
                mode="mock", model=None, cases=("C02",), auth_root=root,
                skill_source=skill, rng=ReverseRandom(),
            )
            self.assertEqual(result["case_order"], ["C02"])
            self.assertFalse(result["core_20_denominator_included"])
            self.assertTrue(result["cases"]["C02"]["arms"]["treatment"]["advice_id_before_first_tool"])
            self.assertFalse(result["cases"]["C02"]["arms"]["baseline"]["advice_id_before_first_tool"])
            self.assertIn("NO JEVCOMPASS ADVISORY", runner.CASE_PROMPTS["C02"])
            self.assertNotIn("NO JEVCOMPASS ADVISORY", runner.CASE_PROMPTS["C01"])
            command = runner._command(codex="codex", model="model", reasoning_effort="low",
                                      prompt=runner.CASE_PROMPTS["C02"])
            self.assertEqual(command[-1], runner.CASE_PROMPTS["C02"])

    def test_stock_skill_source_uses_system_skill_when_user_skill_is_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            system_skill = root / "skills" / ".system" / "openai-docs"
            system_skill.mkdir(parents=True)
            (system_skill / "SKILL.md").write_text("stock", encoding="utf-8")
            self.assertEqual(runner._source_skill_root(root), system_skill)
            user_skill = root / "skills" / "openai-docs"
            user_skill.mkdir()
            self.assertEqual(runner._source_skill_root(root), user_skill)

    def test_missing_stock_skill_fails_with_fixed_redacted_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "private-machine-path"
            with self.assertRaisesRegex(FileNotFoundError, "stock openai-docs skill is unavailable"):
                runner.run_pair(
                    mode="dry-run", model=None, auth_root=root, skill_source=missing,
                )

    def test_live_pair_without_auth_fails_before_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = make_skill(root)
            with self.assertRaisesRegex(RuntimeError, "Codex auth.json is unavailable"):
                runner.run_pair(
                    mode="run", model="test-model", auth_root=root,
                    skill_source=skill, codex="/unused",
                )

    def test_blind_answers_use_private_opaque_files_and_separate_mapping(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "jevcompass-blind-pair"
            count = runner._write_blind_answers(directory, [
                {"case": "C01", "arm": "baseline", "answer": "Fixture citation: docs/verification.md"},
                {"case": "C01", "arm": "treatment", "answer": "Fixture citation: docs/hook-requirements.md"},
            ])
            self.assertEqual(count, (2, 0))
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            mapping = json.loads((directory / "mapping.json").read_text(encoding="utf-8"))
            self.assertEqual({item["arm"] for item in mapping}, {"baseline", "treatment"})
            self.assertEqual(len(list(directory.glob("*.txt"))), 2)
            for entry in mapping:
                self.assertRegex(entry["token"], r"^[a-f0-9]{24}$")
                answer = directory / f'{entry["token"]}.txt'
                self.assertEqual(stat.S_IMODE(answer.stat().st_mode), 0o600)
                self.assertNotIn(entry["arm"], answer.name)
            self.assertEqual(stat.S_IMODE((directory / "mapping.json").stat().st_mode), 0o600)
            rejected = runner._write_blind_answers(root / "jevcompass-other", [
                {"case": "C01", "arm": "baseline", "answer": "api_key=synthetic-secret"},
            ])
            self.assertEqual(rejected, (0, 1))
            self.assertFalse(list((root / "jevcompass-other").glob("*.txt")))
            sanitized = runner._write_blind_answers(root / "jevcompass-sanitized", [
                {"case": "C01", "arm": "treatment", "answer": "JevCompass advice ID: abcdef12\nSee /tmp/jevcompass-codex-setup-abc/fixture/docs/verification.md", "fixture_root": "/tmp/jevcompass-codex-setup-abc/fixture"},
            ])
            self.assertEqual(sanitized, (1, 0))
            artifact = next((root / "jevcompass-sanitized").glob("*.txt")).read_text(encoding="utf-8")
            self.assertIn("<fixture>/docs/verification.md", artifact)
            self.assertNotIn("abcdef12", artifact)
            self.assertNotIn("/tmp/", artifact)

    def test_codex_setup_metric_category_survives_safe_filter(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "advisor.jsonl"
            path.write_text(json.dumps({
                "event": "UserPromptSubmit", "category": "codex-setup", "status": "local",
                "duration_ms": 9.96, "trace": "be80b0ba",
            }) + "\n", encoding="utf-8")
            metrics = runner._core.read_safe_metrics(path)
            self.assertEqual(metrics[0]["category"], "codex-setup")
            self.assertEqual(metrics[0]["trace"], "be80b0ba")

    def test_final_answer_ignores_partial_and_tool_events(self):
        lines = [
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "preflight"}}),
            json.dumps({"type": "item.completed", "item": {"type": "command_execution", "text": "private"}}),
            json.dumps({"type": "item.started", "item": {"type": "agent_message", "text": "draft"}}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "final"}}),
        ]
        self.assertEqual(runner._final_answer(lines), "final")

    def test_timeout_keeps_only_parsed_partial_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = make_skill(root)
            auth = root / "auth.json"
            auth.write_text('{"access_token":"synthetic-test-secret"}', encoding="utf-8")
            fixture = root / "fixture"
            fixture.mkdir()
            event = json.dumps({"type": "item.completed", "item": {
                "type": "agent_message", "text": "JevCompass advice ID: abcdef12 openai-docs",
            }})
            fake_process = mock.Mock(returncode=-9)
            with mock.patch.object(runner.subprocess, "Popen", return_value=fake_process), \
                 mock.patch.object(runner._core, "_collect_events", return_value=([event], [10.0], "timeout")), \
                 mock.patch.object(runner.time, "monotonic", return_value=9.0), \
                 mock.patch.object(runner._core, "read_safe_metrics", return_value=[]):
                result = runner._run_live_arm(
                    codex="/unused", model="synthetic", reasoning_effort="low",
                    fixture=fixture, home=root / "profile", skill_source=skill,
                    skill_sha256=runner._skill_digest(skill), case_id="C01",
                    timeout=3, treatment=True, auth_source=auth,
                )
            self.assertEqual(result["failure"], "timeout")
            self.assertEqual(result["event_count"], 1)
            self.assertTrue(result["partial_event_stream"])
            self.assertTrue(result["advice_id_before_first_tool"])
            self.assertNotIn("synthetic-test-secret", json.dumps(result))

    def test_timeout_is_bounded(self):
        with self.assertRaises(ValueError):
            runner.run_pair(mode="mock", model=None, timeout=runner.MAX_TIMEOUT + 1)


if __name__ == "__main__":
    unittest.main()
