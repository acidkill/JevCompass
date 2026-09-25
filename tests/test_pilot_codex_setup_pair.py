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


def make_review_skills(root: Path) -> tuple[tuple[Path, str], ...]:
    sources = []
    for package, version, skill_name in runner.REVIEW_SKILL_LAYOUTS:
        source = (
            root / "plugins" / "cache" / "claude-code-workflows" / package
            / version / "skills" / skill_name
        )
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: Synthetic review candidate.\n---\n",
            encoding="utf-8",
        )
        relative = f"claude-code-workflows/{package}/{version}/skills/{skill_name}"
        sources.append((source, relative))
    return tuple(sources)


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

    def test_installed_release_setup_uses_explicit_python_and_excludes_checkout_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = make_skill(root)
            interpreter = root / "installed-python"
            interpreter.write_text("synthetic", encoding="utf-8")
            with mock.patch.object(runner, "_check_installed_version") as verify, \
                 mock.patch.object(runner.subprocess, "run", return_value=mock.Mock(returncode=0)) as launch, \
                 mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "never-forward"}):
                result = runner.run_pair(
                    mode="dry-run", model=None, cases=("C02",), auth_root=root,
                    skill_source=skill, installed_python=interpreter, rng=ReverseRandom(),
                )
            verify.assert_called_once_with(interpreter)
            self.assertEqual(result["advisor_source"], "installed-distribution")
            self.assertEqual(result["advisor_version"], "0.1.13")
            self.assertTrue(result["cases"]["C02"]["arms"]["treatment"]["hooks_configured"])
            self.assertFalse(result["cases"]["C02"]["arms"]["baseline"]["hooks_configured"])
            args, kwargs = launch.call_args
            self.assertEqual(args[0][:3], [str(interpreter), "-m", "jevcompass"])
            self.assertEqual(args[0][3], "install")
            self.assertNotIn("PYTHONPATH", kwargs["env"])
            self.assertNotIn("OPENROUTER_API_KEY", kwargs["env"])
            self.assertIn(".codex", kwargs["env"]["CODEX_HOME"])

    def test_installed_version_must_match_exact_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            interpreter = Path(temporary) / "python"
            interpreter.write_text("synthetic", encoding="utf-8")
            with mock.patch.object(runner.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="0.1.11\n")):
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    runner._check_installed_version(interpreter)
            with mock.patch.object(runner.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="0.1.13\n")) as launch:
                runner._check_installed_version(interpreter)
            args, kwargs = launch.call_args
            self.assertEqual(args[0][1], "-I")
            self.assertNotIn("PYTHONPATH", kwargs["env"])

    def test_c03_review_fixture_and_installed_profile_expose_two_skill_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock_skill = make_skill(root)
            review_skills = make_review_skills(root)
            self.assertEqual(runner._review_skill_sources(root), review_skills)
            self.assertEqual(
                __import__("jevcompass.advisor", fromlist=["classify_task"]).classify_task(
                    runner.CASE_PROMPTS["C03"]
                ),
                ("review", "python"),
            )
            delivered = runner._core.parse_event_stream(
                [
                    json.dumps({"type": "item.completed", "item": {
                        "type": "agent_message",
                        "text": "JevCompass advice ID: 12345678; code-review-excellence security-requirement-extraction",
                    }}),
                    json.dumps({"type": "item.started", "item": {
                        "type": "command_execution", "name": "exec_command", "command": "pwd",
                    }}),
                ],
                start_monotonic=0,
                known_candidate_ids=runner._core._known_catalog_ids(),
            )
            self.assertTrue(delivered["advice_id_before_first_tool"])
            self.assertEqual(
                delivered["agent_reported_candidate_ids"],
                ["code-review-excellence", "security-requirement-extraction"],
            )
            fixture = root / "fixture"
            runner._write_review_fixture(fixture)
            self.assertIn("return value - 1", (fixture / "counter.py").read_text(encoding="utf-8"))
            digest = runner._skill_digest(stock_skill)
            profiles = []
            for label in ("baseline", "treatment"):
                profile = runner._prepare_profile(
                    home=root / label, skill_source=stock_skill, skill_sha256=digest,
                    treatment=label == "treatment", auth_source=None,
                    candidate_skills=review_skills,
                )
                profiles.append(profile)
                self.assertEqual(
                    profile["candidate_skills_installed"],
                    ["code-review-excellence", "security-requirement-extraction"],
                )
                with mock.patch.dict("os.environ", {"CODEX_HOME": str(profile["codex_home"])}):
                    from jevcompass.catalog import candidates
                    available = candidates("review", "any", "python", limit=20)
                skill_ids = {item["id"] for item in available if item["kind"] == "skill"}
                self.assertTrue({"code-review-excellence", "security-requirement-extraction"} <= skill_ids)
                for _, _, skill_name in runner.REVIEW_SKILL_LAYOUTS:
                    self.assertTrue((profile["codex_home"] / "skills" / skill_name / "SKILL.md").is_file())
            self.assertEqual(
                [runner._skill_digest(p["codex_home"] / "plugins" / "cache" / "claude-code-workflows"
                                      / package / version / "skills" / skill)
                 for p in profiles for package, version, skill in runner.REVIEW_SKILL_LAYOUTS],
                [runner._skill_digest(source) for _ in profiles for source, _ in review_skills],
            )

    def test_c03_requires_explicit_key_opt_in_and_only_passes_it_to_treatment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock_skill = make_skill(root)
            review_skills = make_review_skills(root)
            with self.assertRaisesRegex(ValueError, "requires explicit"):
                runner.run_pair(
                    mode="run", model="synthetic-model", cases=("C03",), auth_root=root,
                    skill_source=stock_skill, review_skill_sources=review_skills,
                )
            with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "synthetic-secret"}):
                prepared = {
                    "auth_copied": True,
                    "candidate_skills_installed": ["code-review-excellence", "security-requirement-extraction"],
                    "isolated_python": root / "python",
                }
                with mock.patch.object(runner, "_prepare_profile", return_value=prepared), \
                     mock.patch.object(runner._core, "_collect_events", return_value=([], [], None)), \
                     mock.patch.object(runner._core, "read_safe_metrics", return_value=[]), \
                     mock.patch.object(runner.subprocess, "Popen", return_value=mock.Mock(returncode=0)) as launch:
                    arm_results = []
                    for treatment in (False, True):
                        arm_results.append(runner._run_live_arm(
                            codex="codex", model="synthetic-model", reasoning_effort="low",
                            fixture=root, home=root / str(treatment), skill_source=stock_skill,
                            skill_sha256=runner._skill_digest(stock_skill), case_id="C03", timeout=3,
                            treatment=treatment, auth_source=root / "auth.json",
                            candidate_skills=review_skills, allow_openrouter_key=True,
                        ))
                    baseline_env = launch.call_args_list[0].kwargs["env"]
                    treatment_env = launch.call_args_list[1].kwargs["env"]
            self.assertNotIn("OPENROUTER_API_KEY", baseline_env)
            self.assertEqual(treatment_env["OPENROUTER_API_KEY"], "synthetic-secret")
            self.assertEqual(baseline_env["HOME"], str(root / "False"))
            self.assertEqual(baseline_env["XDG_CACHE_HOME"], str(root / "False" / ".cache"))
            self.assertNotIn("synthetic-secret", json.dumps(arm_results))

    def test_c03_pair_isolated_copy_and_remote_opt_in_are_metadata_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock_skill = make_skill(root)
            review_skills = make_review_skills(root)
            result = runner.run_pair(
                mode="dry-run", model=None, cases=("C03",), auth_root=root,
                skill_source=stock_skill, review_skill_sources=review_skills,
                rng=ReverseRandom(),
            )
            case = result["cases"]["C03"]
            self.assertTrue(case["fixture_copies_identical"])
            self.assertTrue(case["candidate_skill_copies_identical"])
            self.assertEqual(case["arms"]["baseline"]["candidate_skills_installed"],
                             case["arms"]["treatment"]["candidate_skills_installed"])
            self.assertFalse(result["openrouter_key_forwarded"])
            self.assertFalse(case["arms"]["treatment"]["model_called"])
            self.assertIn("NO JEVCOMPASS ADVISORY", runner.CASE_PROMPTS["C03"])

    def test_c04_fixture_and_equal_key_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock_skill = make_skill(root)
            review_skills = make_review_skills(root)
            from jevcompass.advisor import classify_task
            self.assertEqual(classify_task(runner.CASE_PROMPTS["C04"]), ("review", "python"))
            result = runner.run_pair(
                mode="dry-run", model=None, cases=("C04",), auth_root=root,
                skill_source=stock_skill, review_skill_sources=review_skills,
                rng=ReverseRandom(),
            )
            case = result["cases"]["C04"]
            self.assertTrue(case["fixture_copies_identical"])
            self.assertTrue(case["candidate_skill_copies_identical"])
            self.assertEqual(case["arms"]["baseline"]["candidate_skills_installed"],
                             case["arms"]["treatment"]["candidate_skills_installed"])
            self.assertFalse(result["core_20_denominator_included"])
            self.assertIn("RETRY_CONTRACT.md", runner.CASE_PROMPTS["C04"])
            self.assertTrue((runner.RETRY_FIXTURE / "tests" / "test_retry.py").is_file())

            with mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "synthetic-secret"}):
                prepared = {
                    "auth_copied": True,
                    "candidate_skills_installed": ["code-review-excellence", "security-requirement-extraction"],
                    "isolated_python": root / "python",
                }
                with mock.patch.object(runner, "_prepare_profile", return_value=prepared), \
                     mock.patch.object(runner._core, "_collect_events", return_value=([], [], None)), \
                     mock.patch.object(runner._core, "read_safe_metrics", return_value=[]), \
                     mock.patch.object(runner.subprocess, "Popen", return_value=mock.Mock(returncode=0)) as launch:
                    for treatment in (False, True):
                        runner._run_live_arm(
                            codex="codex", model="synthetic-model", reasoning_effort="low",
                            fixture=root, home=root / str(treatment), skill_source=stock_skill,
                            skill_sha256=runner._skill_digest(stock_skill), case_id="C04", timeout=3,
                            treatment=treatment, auth_source=root / "auth.json",
                            candidate_skills=review_skills, allow_openrouter_key=True,
                        )
                    environments = [call.kwargs["env"] for call in launch.call_args_list]
            self.assertEqual([env["OPENROUTER_API_KEY"] for env in environments],
                             ["synthetic-secret", "synthetic-secret"])

    def test_c04_action_telemetry_counts_only_successful_fixture_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixture"
            home = root / "home"
            events = []
            for command, exit_code in [
                (f"cat {fixture / 'retry.py'}", 0),
                (f"sed -n '1,30p' {fixture / 'RETRY_CONTRACT.md'}", 0),
                (f"nl -ba {fixture / 'tests/test_retry.py'}", 0),
                (f"cat {fixture / 'retry.py'} | wc -l", 0),
                (f"cat {fixture / 'retry.py'}", 1),
            ]:
                events.append(json.dumps({"type": "item.completed", "item": {
                    "type": "command_execution", "command": command, "exit_code": exit_code,
                }}))
            telemetry = runner._c03_action_telemetry(
                events, event_times=[4.1, 4.2, 4.3, 4.4, 4.5],
                start_monotonic=4.0, fixture=fixture, home=home, case_id="C04",
            )
            self.assertEqual(telemetry["review_target_reads"], [
                {"id": "retry.py", "elapsed_ms": 100.0},
                {"id": "RETRY_CONTRACT.md", "elapsed_ms": 200.0},
                {"id": "tests/test_retry.py", "elapsed_ms": 300.0},
            ])
            self.assertNotIn(str(root), json.dumps(telemetry))
            self.assertNotIn("cat ", json.dumps(telemetry))
            self.assertEqual(telemetry["unmatched_command_forms"], [{"form": "compound", "count": 1}])

    def test_c04_exact_fixture_cd_then_read_is_counted_but_other_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixture"
            commands = [
                f"bash -lc 'cd {fixture} && sed -n 1,20p retry.py'",
                f"bash -lc 'cd {root / 'other'} && cat retry.py'",
            ]
            events = [json.dumps({"type": "item.completed", "item": {
                "type": "command_execution", "command": command, "exit_code": 0,
            }}) for command in commands]
            telemetry = runner._c03_action_telemetry(
                events, event_times=[2.1, 2.2], start_monotonic=2.0,
                fixture=fixture, home=root / "home", case_id="C04",
            )
            self.assertEqual(telemetry["review_target_reads"],
                             [{"id": "retry.py", "elapsed_ms": 100.0}])
            self.assertEqual(telemetry["unmatched_command_forms"],
                             [{"form": "compound", "count": 1}])

    def test_c04_unmatched_command_diagnostic_never_emits_command_or_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixture"
            events = [json.dumps({"type": "item.completed", "item": {
                "type": "command_execution", "command": command, "exit_code": exit_code,
            }}) for command, exit_code in [
                (f"rg -n 'except' {fixture / 'retry.py'}", 0),
                (f"bash -lc 'git status && sed -n 1,20p retry.py'", 0),
                (f"python3 -c 'print(1)'", 0),
                (f"rg -n 'secret' {fixture / 'retry.py'}", 1),
            ]]
            telemetry = runner._c03_action_telemetry(
                events, event_times=[2.1, 2.2, 2.3, 2.4],
                start_monotonic=2.0, fixture=fixture, home=root / "home", case_id="C04",
            )
            self.assertEqual(telemetry["unmatched_command_forms"], [
                {"form": "search", "count": 1},
                {"form": "python", "count": 1},
                {"form": "compound", "count": 1},
            ])
            serialized = json.dumps(telemetry)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("except", serialized)
            self.assertNotIn("secret", serialized)
            self.assertNotIn("rg -n", serialized)

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

    def test_c03_action_telemetry_counts_only_completed_successful_exact_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixture"
            home = root / "home"
            counter = fixture / "counter.py"
            code_review = home / ".codex" / "skills" / "code-review-excellence" / "SKILL.md"
            security = (
                home / ".codex" / "plugins" / "cache" / "claude-code-workflows"
                / "security-scanning" / "1.3.2" / "skills"
                / "security-requirement-extraction" / "SKILL.md"
            )

            def completed(command, exit_code=0):
                return json.dumps({
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": command,
                        "exit_code": exit_code,
                    },
                })

            lines = [
                json.dumps({
                    "type": "item.started",
                    "item": {"type": "command_execution", "command": f"cat {counter}"},
                }),
                completed(f"cat {counter}"),
                completed(f"sed -n '1,20p' {code_review}"),
                completed(f"head -n 10 {security}"),
                completed(f"cat {counter}", exit_code=1),
                completed(f"ls -la {fixture}"),
                completed(f"rg -n 'counter.py' {fixture}"),
                completed(f"echo 'read {code_review}'"),
                completed(f"cat {counter} | wc -l"),
                completed(f"cat --help {counter}"),
                completed(f"head --help {counter}"),
                completed(f"sed -i 's/return/replace/' {counter}"),
            ]
            telemetry = runner._c03_action_telemetry(
                lines,
                event_times=[100.01, 100.125, 100.25, 100.5, 100.6, 100.7, 100.8, 100.9, 101.0],
                start_monotonic=100.0,
                fixture=fixture,
                home=home,
            )

            self.assertEqual(telemetry, {
                "counter_read_ms": 125.0,
                "candidate_skill_reads": [
                    {"id": "code-review-excellence", "elapsed_ms": 250.0},
                    {"id": "security-requirement-extraction", "elapsed_ms": 500.0},
                ],
            })
            serialized = json.dumps(telemetry)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn("counter.py", serialized)
            self.assertNotIn("SKILL.md", serialized)
            self.assertNotIn("cat", serialized)

    def test_c03_action_telemetry_ignores_mentions_and_nonmatching_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixture"
            home = root / "home"
            counter = fixture / "counter.py"
            skill = home / ".codex" / "skills" / "code-review-excellence" / "SKILL.md"
            lines = [
                json.dumps({
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": f"Read {counter} and {skill}"},
                }),
                json.dumps({
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": f"cat {fixture / 'different.py'}",
                        "exit_code": 0,
                    },
                }),
                json.dumps({
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": f"cat {counter}",
                        "exit_code": 1,
                    },
                }),
            ]
            telemetry = runner._c03_action_telemetry(
                lines, event_times=[4.1, 4.2, 4.3], start_monotonic=4.0,
                fixture=fixture, home=home,
            )
            self.assertEqual(telemetry, {
                "counter_read_ms": None,
                "candidate_skill_reads": [],
            })

    def test_c03_action_telemetry_unwraps_one_limited_shell_layer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixture"
            home = root / "home"
            counter = fixture / "counter.py"
            skill = home / ".codex" / "skills" / "code-review-excellence" / "SKILL.md"

            def completed(command):
                return json.dumps({
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": command,
                        "exit_code": 0,
                    },
                })

            wrapped_counter = f"/usr/bin/bash -lc \"sed -n '1,240p' {counter}\""
            wrapped_skill = f"/bin/sh -lc \"cat {skill}\""
            telemetry = runner._c03_action_telemetry(
                [completed(wrapped_counter), completed(wrapped_skill)],
                event_times=[12.125, 12.25],
                start_monotonic=12.0,
                fixture=fixture,
                home=home,
            )
            self.assertEqual(telemetry, {
                "counter_read_ms": 125.0,
                "candidate_skill_reads": [
                    {"id": "code-review-excellence", "elapsed_ms": 250.0},
                ],
            })

            rejected_commands = [
                f"/usr/bin/bash -lc \"cat {counter} && echo done\"",
                f"/usr/bin/bash -lc \"bash -lc 'cat {counter}'\"",
                f"/usr/bin/bash -lc \"python -c 'open(\\\"{counter}\\\").read()'\"",
                f"/usr/bin/bash -lc \"cat {counter}\" extra",
            ]
            rejected = runner._c03_action_telemetry(
                [completed(command) for command in rejected_commands],
                event_times=[12.3, 12.4, 12.5, 12.6],
                start_monotonic=12.0,
                fixture=fixture,
                home=home,
            )
            self.assertEqual(rejected, {
                "counter_read_ms": None,
                "candidate_skill_reads": [],
            })

    def test_c03_action_telemetry_supports_nl_and_two_read_conjunction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixture"
            home = root / "home"
            counter = fixture / "counter.py"

            def completed(command):
                return json.dumps({
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": command,
                        "exit_code": 0,
                    },
                })

            baseline = (
                f"/usr/bin/bash -lc \"sed -n '1,240p' counter.py "
                f"&& nl -ba counter.py\""
            )
            listing = (
                "/usr/bin/bash -lc \"rg --files -g 'counter.py' "
                "-g 'AGENTS.md' -g 'SKILL.md'\""
            )
            treatment = "/usr/bin/bash -lc 'nl -ba counter.py'"
            telemetry = runner._c03_action_telemetry(
                [completed(baseline), completed(listing), completed(treatment)],
                event_times=[20.125, 20.2, 20.3],
                start_monotonic=20.0,
                fixture=fixture,
                home=home,
            )
            self.assertEqual(telemetry, {
                "counter_read_ms": 125.0,
                "candidate_skill_reads": [],
            })

            direct_nl = runner._c03_action_telemetry(
                [completed(f"/usr/bin/nl -ba {counter}")],
                event_times=[20.25],
                start_monotonic=20.0,
                fixture=fixture,
                home=home,
            )
            self.assertEqual(direct_nl["counter_read_ms"], 250.0)

            rejected = runner._c03_action_telemetry(
                [
                    completed(
                        f"/usr/bin/bash -lc \"rg --files -g 'counter.py' && "
                        "nl -ba counter.py\""
                    ),
                    completed(
                        f"/usr/bin/bash -lc \"sed -n '1,240p' counter.py && "
                        "echo done\""
                    ),
                    completed(
                        f"/usr/bin/bash -lc \"cat counter.py && nl -ba counter.py "
                        "&& cat counter.py\""
                    ),
                ],
                event_times=[20.4, 20.5, 20.6],
                start_monotonic=20.0,
                fixture=fixture,
                home=home,
            )
            self.assertEqual(rejected, {
                "counter_read_ms": None,
                "candidate_skill_reads": [],
            })

    def test_timeout_is_bounded(self):
        with self.assertRaises(ValueError):
            runner.run_pair(mode="mock", model=None, timeout=runner.MAX_TIMEOUT + 1)


if __name__ == "__main__":
    unittest.main()
