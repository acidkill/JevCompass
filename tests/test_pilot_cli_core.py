"""Offline fake-CLI tests for the bounded CLI core paired pilot runner."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_cli_core.py"
sys.path.insert(0, str(ROOT / "src"))
from jevcompass.advisor import classify_task
SPEC = importlib.util.spec_from_file_location("pilot_cli_core", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)


class OrderedRandom:
    """Deterministically reverse each shuffled list for order assertions."""

    def shuffle(self, values):
        values.reverse()


def make_fake_codex(path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib\n"
        "home = pathlib.Path(os.environ['CODEX_HOME'])\n"
        "hooks = home / 'hooks.json'\n"
        "treatment = hooks.is_file()\n"
        "if treatment:\n"
        "    metric = home.parent / '.local/state/jevcompass/advisor.jsonl'\n"
        "    metric.parent.mkdir(parents=True, exist_ok=True)\n"
        "    status = 'local' if 'OPENROUTER_API_KEY' not in os.environ else 'key_leaked'\n"
        "    records = []\n"
        "    if os.environ.get('JEV_ADVISOR_DIAGNOSTIC') == '1':\n"
        "        records.append({'event':'UserPromptSubmit','category':'default','status':'collab-unavailable','duration_ms':0.25,'trace':'abcdef12','command':'must-not-escape'})\n"
        "    records.append({'event':'UserPromptSubmit','category':'coding','status':status,'duration_ms':1.25,'trace':'abcdef12','prompt':'must-not-escape'})\n"
        "    metric.write_text(''.join(json.dumps(record) + '\\n' for record in records), encoding='utf-8')\n"
        "message = 'JevCompass advice ID: abcdef12\\nCandidate: pytest\\nSynthetic completion.' if treatment else 'Synthetic completion.'\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':message}}), flush=True)\n"
        "print(json.dumps({'type':'item.started','item':{'type':'command_execution','name':'exec_command','command':'DO NOT RETAIN'}}), flush=True)\n"
        "print(json.dumps({'type':'turn.completed'}), flush=True)\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def make_quality_fake_codex(path: Path) -> Path:
    path.write_text(
        """#!/usr/bin/env python3
import json, pathlib, sys
task = sys.argv[-1]
root = pathlib.Path.cwd()
nl = chr(10)
if "normalizes whitespace" in task:
    (root / "tinytext" / "text.py").write_text("def normalize(value):" + nl + "    return ' '.join(value.split())" + nl, encoding="utf-8")
    (root / "tests" / "test_text.py").write_text("def test_normalize():" + nl + "    assert normalize('a  b') == 'a b'" + nl, encoding="utf-8")
    answer = "Implemented the normalizer and focused test."
elif "unset-variable defect" in task:
    script = ["#!/usr/bin/env bash", "set -euo pipefail", ': "${OUTPUT_PATH:?required}"', "printf done"]
    (root / "scripts" / "render_report.sh").write_text(nl.join(script) + nl, encoding="utf-8")
    answer = "Fixed the unset output guard and syntax checked it."
elif "install instructions" in task:
    readme = ["Install with python -m pip install .", "Run python -m tinytext --help.", "Run python -m unittest discover -s tests."]
    (root / "README.md").write_text(nl.join(readme) + nl, encoding="utf-8")
    answer = "Updated the install, help, and test instructions."
else:
    contract = [
        "# Status API contract",
        "POST /status accepts a required request_id and optional detail flag.",
        "A successful response returns a validated status result with request_id and state.",
        "Invalid input returns HTTP 400; server failures return HTTP 500.",
    ]
    (root / "STATUS_API.md").write_text(nl.join(contract) + nl, encoding="utf-8")
    answer = "POST /status accepts required and optional inputs, validates the status response, returns documented fields, and describes invalid input and server error responses."
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":answer}}), flush=True)
print(json.dumps({"type":"turn.completed"}), flush=True)
""",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


def make_scoring_inputs(root: Path, case_ids=("P01",), *, diagnostics=False):
    cases = {}
    for case_id in case_ids:
        arms = {}
        for arm in ("baseline", "treatment"):
            arms[arm] = {
                "status": "completed",
                "exit_code": 0,
                "first_action": {
                    "class": "source_read",
                    "elapsed_ms": 100.0 if arm == "baseline" else 90.0,
                },
                "outcome_checks": {"answer_indicator": True},
            }
        cases[case_id] = {"arms": arms}
    blind_dir = root / "blind"
    pilot_diagnostics = None
    if diagnostics:
        pilot_diagnostics = {
            (case_id, arm): ({
                "category": "coding", "status": "local", "latency_ms": 1.25,
                "trace_reported_before_first_tool": True,
            } if arm == "treatment" else runner._empty_pilot_diagnostic("not_applicable"))
            for case_id in case_ids for arm in ("baseline", "treatment")
        }
    runner.write_blind_receipts(cases, blind_dir, pilot_diagnostics=pilot_diagnostics)
    mapping_path = blind_dir / "mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    scores = []
    for index, entry in enumerate(mapping["arms"]):
        scores.append({
            "arm_token": entry["arm_token"],
            "first_productive_action_ms": 12000 if index % 2 == 0 else 9000,
            "task_quality": bool(index % 2),
            "required_checks_preserved": True,
            "blocked": False,
            "privacy_disclosure": False,
        })
    score_path = root / "scores.json"
    score_bytes = json.dumps(
        {"schema": "jevcompass-blind-human-scores-v1", "scores": scores},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    score_path.write_bytes(score_bytes)
    return blind_dir / "receipts", mapping_path, score_path, hashlib.sha256(score_bytes).hexdigest()


class PilotCliCoreTests(unittest.TestCase):
    def setUp(self):
        if not runner.FIXTURE.is_dir():
            self.skipTest("independently prepared cli_core fixture is not present yet")

    def test_score_blind_pilot_valid_pair_aggregates_but_never_accepts_overall(self):
        with tempfile.TemporaryDirectory() as directory:
            receipts, mapping, scores, digest = make_scoring_inputs(Path(directory))
            result = runner.score_blind_pilot(receipts, mapping, scores, digest)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["decision"], "not_accepted")
        self.assertTrue(result["commitment_verified"])
        self.assertFalse(result["blindness_verified"])
        self.assertEqual(result["task_quality"]["paired_rated"], 1)
        self.assertEqual(result["task_quality"]["treatment_better"], 1)
        self.assertEqual(result["first_productive_action"]["eligible_paired_cases"], 1)
        self.assertEqual(result["first_productive_action"]["eligible_median_treatment_minus_baseline_ms"], -3000.0)
        self.assertEqual(result["blocks"], 0)
        self.assertEqual(result["privacy_disclosures"], 0)
        self.assertEqual(result["recommendation_usefulness"]["status"], "unscored")
        self.assertIsNone(result["recommendation_usefulness"]["rate"])
        self.assertTrue(any("latency" in item for item in result["missing_evidence"]))
        self.assertTrue(any("delivery" in item for item in result["missing_evidence"]))
        self.assertTrue(any("post-unblind" in item for item in result["missing_evidence"]))

    def test_scorer_validates_private_hook_diagnostics_and_summarizes_after_unblinding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipts, mapping, scores, digest = make_scoring_inputs(root, diagnostics=True)
            diagnostics_path = mapping.parent / "pilot-diagnostics.json"
            diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
            self.assertNotIn("baseline", diagnostics_path.read_text(encoding="utf-8"))
            self.assertNotIn("treatment", diagnostics_path.read_text(encoding="utf-8"))
            result = runner.score_blind_pilot(receipts, mapping, scores, digest)
            self.assertEqual(result["pilot_hook_diagnostics"]["advice_emitted"], 1)
            self.assertEqual(result["pilot_hook_diagnostics"]["advice_emitted_with_pretool_trace"], 1)
            self.assertEqual(result["pilot_hook_diagnostics"]["median_hook_latency_ms"], 1.25)
            self.assertEqual(result["pilot_hook_diagnostics"]["categories"], {"coding": 1})

            diagnostics["arms"][0]["category"] = []
            diagnostics_path.write_text(json.dumps(diagnostics), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown category"):
                runner.score_blind_pilot(receipts, mapping, scores, digest)
            diagnostics["arms"][0]["category"] = None
            diagnostics["unexpected"] = "must-be-rejected"
            diagnostics_path.write_text(json.dumps(diagnostics), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown schema or fields"):
                runner.score_blind_pilot(receipts, mapping, scores, digest)

    def test_private_hook_diagnostic_distinguishes_invocation_skip_and_unreported_advice(self):
        parsed = {
            "advice_id_before_first_tool": False,
            "first_assistant": {"advice_id": "abcdef12"},
        }
        diagnostic = {"event": "UserPromptSubmit", "category": "default", "status": "collab-unavailable",
                      "trace": "abcdef12", "duration_ms": 0.1}
        emitted = {"event": "UserPromptSubmit", "category": "coding", "status": "local",
                   "trace": "abcdef12", "duration_ms": 3.5}
        self.assertEqual(runner._pilot_hook_diagnostic(parsed, [])["status"], "not_invoked")
        skipped = {**emitted, "status": "classification-skip", "category": "none"}
        skip_result = runner._pilot_hook_diagnostic(parsed, [diagnostic, skipped])
        self.assertEqual(skip_result["status"], "classification-skip")
        self.assertFalse(skip_result["trace_reported_before_first_tool"])
        result = runner._pilot_hook_diagnostic(parsed, [diagnostic, emitted])
        self.assertEqual(result["status"], "local")
        self.assertFalse(result["trace_reported_before_first_tool"])
        self.assertNotIn("trace", result)
        parsed["advice_id_before_first_tool"] = True
        self.assertTrue(runner._pilot_hook_diagnostic(parsed, [diagnostic, emitted])[
            "trace_reported_before_first_tool"
        ])

    def test_safe_hook_metric_reader_bounds_and_omits_secret_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "advisor.jsonl"
            path.write_text(json.dumps({
                "event": "UserPromptSubmit", "category": "api_key=private-value",
                "status": "password=private-value", "duration_ms": float("nan"),
                "trace": "abcdef12", "prompt": "private prompt", "command": "private command",
            }) + "\n", encoding="utf-8")
            records = runner.read_safe_metrics(path)
        self.assertEqual(records[0]["category"], "unknown")
        self.assertEqual(records[0]["status"], "unknown")
        self.assertIsNone(records[0]["duration_ms"])
        self.assertIn("abcdef12", json.dumps(records))  # transient input for correlation only
        self.assertNotIn("private-value", json.dumps(records))
        self.assertNotIn("private prompt", json.dumps(records))
        self.assertNotIn("private command", json.dumps(records))

    def test_score_blind_pilot_rejects_missing_and_duplicate_tokens(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate), tempfile.TemporaryDirectory() as directory:
                receipts, mapping, scores_path, _ = make_scoring_inputs(Path(directory))
                scores = json.loads(scores_path.read_text(encoding="utf-8"))
                if duplicate:
                    scores["scores"].append(dict(scores["scores"][0]))
                else:
                    scores["scores"].pop()
                raw = json.dumps(scores).encode("utf-8")
                scores_path.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, "duplicate token|exactly match"):
                    runner.score_blind_pilot(receipts, mapping, scores_path, hashlib.sha256(raw).hexdigest())

    def test_score_blind_pilot_checks_hash_and_rejects_unknown_or_invalid_ratings(self):
        with tempfile.TemporaryDirectory() as directory:
            receipts, mapping, scores_path, _ = make_scoring_inputs(Path(directory))
            with self.assertRaisesRegex(ValueError, "does not match"):
                runner.score_blind_pilot(receipts, mapping, scores_path, "0" * 64)
            scores = json.loads(scores_path.read_text(encoding="utf-8"))
            scores["scores"][0]["task_quality"] = "probably"
            raw = json.dumps(scores).encode("utf-8")
            scores_path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "task_quality"):
                runner.score_blind_pilot(receipts, mapping, scores_path, hashlib.sha256(raw).hexdigest())
            scores["scores"][0]["task_quality"] = None
            scores["scores"][0]["usefulness"] = True
            raw = json.dumps(scores).encode("utf-8")
            scores_path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "unknown or missing fields"):
                runner.score_blind_pilot(receipts, mapping, scores_path, hashlib.sha256(raw).hexdigest())

    def test_score_blind_pilot_partial_routine_and_zero_quality_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            receipts, mapping, scores_path, _ = make_scoring_inputs(Path(directory), ("P01", "R01"))
            scores = json.loads(scores_path.read_text(encoding="utf-8"))
            for score in scores["scores"]:
                score["task_quality"] = None
            raw = json.dumps(scores).encode("utf-8")
            scores_path.write_bytes(raw)
            result = runner.score_blind_pilot(receipts, mapping, scores_path, hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["task_quality"]["paired_rated"], 0)
        self.assertEqual(result["task_quality"]["unscored"], 2)
        self.assertEqual(result["recommendation_usefulness"]["rated"], 0)
        self.assertIsNone(result["recommendation_usefulness"]["rate"])
        self.assertEqual(result["first_productive_action"]["paired_cases"], 2)
        self.assertEqual(result["first_productive_action"]["eligible_paired_cases"], 1)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["decision"], "not_accepted")
        self.assertTrue(any("task-quality ratings" in item for item in result["missing_evidence"]))
        self.assertTrue(any("Recommendation usefulness remains unscored" in item for item in result["missing_evidence"]))

    def test_unrated_block_and_privacy_flags_do_not_pass_zero_event_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            receipts, mapping, scores_path, _ = make_scoring_inputs(Path(directory))
            scores = json.loads(scores_path.read_text(encoding="utf-8"))
            for score in scores["scores"]:
                score["blocked"] = None
                score["privacy_disclosure"] = None
            raw = json.dumps(scores).encode("utf-8")
            scores_path.write_bytes(raw)
            result = runner.score_blind_pilot(receipts, mapping, scores_path, hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["blocks"], 0)
        self.assertEqual(result["block_ratings"], {"rated": 0, "total": 2})
        self.assertIsNone(result["gate_summary"]["zero_blocks_passed"])
        self.assertEqual(result["privacy_disclosures"], 0)
        self.assertEqual(result["privacy_ratings"], {"rated": 0, "total": 2})
        self.assertIsNone(result["gate_summary"]["zero_privacy_disclosures_passed"])

    def test_score_blind_pilot_accepts_blind_task_quality_ratings_for_both_arms(self):
        with tempfile.TemporaryDirectory() as directory:
            receipts, mapping, scores_path, _ = make_scoring_inputs(Path(directory), ("P01", "R01"))
            scores = json.loads(scores_path.read_text(encoding="utf-8"))
            self.assertTrue(all(isinstance(score["task_quality"], bool) for score in scores["scores"]))
            digest = hashlib.sha256(scores_path.read_bytes()).hexdigest()
            first = runner.score_blind_pilot(receipts, mapping, scores_path, digest)
            mapping_doc = json.loads(mapping.read_text(encoding="utf-8"))
            for entry in mapping_doc["arms"]:
                entry["arm"] = "baseline" if entry["arm"] == "treatment" else "treatment"
            mapping.write_text(json.dumps(mapping_doc), encoding="utf-8")
            flipped = runner.score_blind_pilot(receipts, mapping, scores_path, digest)
        self.assertEqual(first["task_quality"]["paired_rated"], 2)
        self.assertEqual(first["task_quality"]["treatment_better"], 2)
        self.assertEqual(flipped["task_quality"]["paired_rated"], 2)
        self.assertEqual(flipped["task_quality"]["baseline_better"], 2)
        self.assertEqual(first["recommendation_usefulness"]["status"], "unscored")
        self.assertEqual(flipped["recommendation_usefulness"]["status"], "unscored")

    def test_mock_pair_randomizes_and_emits_safe_metadata_only(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = make_fake_codex(Path(directory) / "fake-codex")
            result = runner.run_pilot(
                mode="mock", model="test-model", reasoning_effort="high",
                timeout=4, cases=("P01", "R01"), codex=str(fake),
                rng=OrderedRandom(),
            )
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["preflight"])
        self.assertEqual(result["case_order"], ["R01", "P01"])
        self.assertFalse(result["openrouter_key_forwarded"])
        for case_id, case in result["cases"].items():
            self.assertTrue(case["fixture_copies_identical"])
            self.assertRegex(case["source_sha256"], r"^[a-f0-9]{64}$")
            self.assertEqual(case["arm_order"], ["treatment", "baseline"])
            self.assertEqual(set(case["arms"]), {"baseline", "treatment"})
            self.assertEqual(case["sandbox"], runner._case_settings(case_id)["sandbox"])
            self.assertIsNone(case["arms"]["baseline"].get("advice_id"))
            self.assertEqual(case["arms"]["treatment"]["advice_id"], "abcdef12")
            self.assertTrue(case["arms"]["treatment"]["advice_id_before_first_tool"])
            self.assertEqual(case["arms"]["treatment"]["advice_metric"]["metric"]["trace"], "abcdef12")
            self.assertIsNone(case["arms"]["treatment"]["first_useful_action_ms"])
        rendered = json.dumps(result)
        for forbidden in ("Synthetic completion", "DO NOT RETAIN", "must-not-escape", "command\":"):
            self.assertNotIn(forbidden, rendered)
        for case_id in ("P01", "R01"):
            command = runner._build_command(
                codex="fake", model="test-model", reasoning_effort="high",
                case_id=case_id,
            )
            self.assertIn("--sandbox", command)
            self.assertIn(runner._case_settings(case_id)["sandbox"], command)
            self.assertEqual(command[:4], ["fake", "-a", "never", "exec"])
            self.assertIn("never", command)
            self.assertIn("--model", command)
            self.assertIn("test-model", command)
            self.assertIn("model_reasoning_effort=high", command)

    def test_preflight_appends_same_instruction_without_changing_substantive_classification(self):
        for case_id in sorted(runner.PREFLIGHT_CASES):
            base = runner._build_command(
                codex="fake", model="test-model", reasoning_effort="high", case_id=case_id,
            )[-1]
            preflight = runner._build_command(
                codex="fake", model="test-model", reasoning_effort="high", case_id=case_id,
                preflight=True,
            )[-1]
            self.assertEqual(preflight, f"{base}\n\n{runner.PREFLIGHT_INSTRUCTION}")
            self.assertEqual(classify_task(preflight), classify_task(base), case_id)

    def test_preflight_preserves_exact_routine_prompts_and_does_not_trigger_classification(self):
        for case_id in sorted(runner.ROUTINE_CASES):
            command = runner._build_command(
                codex="fake", model="test-model", reasoning_effort="high", case_id=case_id,
                preflight=True,
            )
            self.assertEqual(command[-1], runner.PROMPTS[case_id])
            self.assertIsNone(classify_task(command[-1]), case_id)

    def test_preflight_receipt_is_recorded_in_dry_run(self):
        result = runner.run_pilot(
            mode="dry-run", model=None, cases=("P01", "R01"),
            preflight=True, rng=OrderedRandom(),
        )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["preflight"])

    def test_blind_receipts_are_opaque_private_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blind_dir = root / "receipts"
            fake = make_fake_codex(root / "fake-codex")
            result = runner.run_pilot(
                mode="mock", model="must-not-appear", timeout=4,
                cases=("P01",), codex=str(fake), rng=OrderedRandom(),
                blind_dir=blind_dir,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["blind_receipts"]["receipt_count"], 2)
            self.assertIn("cannot establish blinded task correctness", result["blind_receipts"]["limitation"])
            self.assertEqual(stat.S_IMODE(blind_dir.stat().st_mode), 0o700)
            evaluator_dir = blind_dir / "receipts"
            self.assertEqual(stat.S_IMODE(evaluator_dir.stat().st_mode), 0o700)
            self.assertEqual({path.name for path in blind_dir.iterdir()}, {"receipts", "mapping.json", "pilot-diagnostics.json"})
            self.assertNotIn("mapping.json", {path.name for path in evaluator_dir.iterdir()})
            mapping = json.loads((blind_dir / "mapping.json").read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE((blind_dir / "mapping.json").stat().st_mode), 0o600)
            self.assertEqual(len(mapping["arms"]), 2)
            self.assertEqual({item["arm"] for item in mapping["arms"]}, {"baseline", "treatment"})
            self.assertEqual({item["case_id"] for item in mapping["arms"]}, {"P01"})
            tokens = {item["arm_token"] for item in mapping["arms"]}
            self.assertEqual(len(tokens), 2)
            diagnostics_path = blind_dir / "pilot-diagnostics.json"
            self.assertEqual(stat.S_IMODE(diagnostics_path.stat().st_mode), 0o600)
            diagnostics_text = diagnostics_path.read_text(encoding="utf-8")
            diagnostics = json.loads(diagnostics_text)
            self.assertEqual(diagnostics["schema"], "jevcompass-private-hook-diagnostics-v1")
            self.assertEqual({row["arm_token"] for row in diagnostics["arms"]}, tokens)
            by_token = {row["arm_token"]: row for row in diagnostics["arms"]}
            treatment_token = next(item["arm_token"] for item in mapping["arms"] if item["arm"] == "treatment")
            baseline_token = next(item["arm_token"] for item in mapping["arms"] if item["arm"] == "baseline")
            self.assertEqual(by_token[treatment_token]["status"], "local")
            self.assertEqual(by_token[treatment_token]["category"], "coding")
            self.assertEqual(by_token[treatment_token]["latency_ms"], 1.25)
            self.assertTrue(by_token[treatment_token]["trace_reported_before_first_tool"])
            self.assertEqual(by_token[baseline_token]["status"], "not_applicable")
            for forbidden in ("baseline", "treatment", "abcdef12", "must-not-escape", "command", "prompt"):
                self.assertNotIn(forbidden, diagnostics_text)
            evaluator_files = list(evaluator_dir.glob("*.json"))
            self.assertEqual({path.stem for path in evaluator_files}, tokens)
            receipts = [json.loads(path.read_text(encoding="utf-8")) for path in evaluator_files]
            self.assertNotIn("cases", result)
            self.assertEqual({item["case_id"] for item in receipts}, {"P01"})
            self.assertEqual({item["schema"] for item in receipts}, {"jevcompass-blind-cli-core-v1"})
            for path, receipt in zip(evaluator_files, receipts):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertIn(receipt["first_action_class"], {"source_read", "test", "edit", "other"})
                self.assertIsInstance(receipt["outcome_checks"], dict)
                self.assertIn("cannot establish blinded task correctness", receipt["limitation"])
                rendered = path.read_text(encoding="utf-8")
                for forbidden in (
                    "baseline", "treatment", "must-not-appear", "abcdef12",
                    "Synthetic completion", "DO NOT RETAIN", "must-not-escape", "pytest", "advice",
                    "mapping", '"command"', '"prompt"', "transcript", str(root), str(runner.FIXTURE),
                ):
                    self.assertNotIn(forbidden, rendered)
                self.assertNotIn("trace", receipt)
                self.assertNotIn("model", receipt)
            treatment_token = next(item["arm_token"] for item in mapping["arms"] if item["arm"] == "treatment")
            baseline_token = next(item["arm_token"] for item in mapping["arms"] if item["arm"] == "baseline")
            advice_review = mapping["advice_review"]
            self.assertEqual(advice_review["timing"], "after blind outcome scoring")
            self.assertIn("not backend-selection evidence", advice_review["interpretation"])
            review_by_token = {item["arm_token"]: item["agent_reported_candidate_ids"]
                               for item in advice_review["arms"]}
            self.assertEqual(review_by_token[treatment_token], ["pytest"])
            self.assertIsNone(review_by_token[baseline_token])
            self.assertLess(len(review_by_token[treatment_token]), len(runner._known_catalog_ids()))

    def test_blind_receipts_reject_existing_content_and_symlink_components(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "existing"
            existing.mkdir(mode=0o700)
            (existing / "keep.json").write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                runner.write_blind_receipts({}, existing)
            self.assertEqual((existing / "keep.json").read_text(encoding="utf-8"), "keep")

            target = root / "real"
            target.mkdir()
            symlink = root / "linked"
            symlink.symlink_to(target, target_is_directory=True)
            with self.assertRaises(OSError):
                runner.write_blind_receipts({}, symlink / "receipts")
            self.assertEqual(list(target.iterdir()), [])

    def test_blind_receipts_do_not_overwrite_a_previous_run(self):
        with tempfile.TemporaryDirectory() as directory:
            blind_dir = Path(directory) / "receipts"
            result = runner.run_pilot(mode="dry-run", model=None, cases=("R01",), blind_dir=blind_dir)
            before = {
                (Path("receipts") / path.name).as_posix(): path.read_bytes()
                for path in (blind_dir / "receipts").iterdir()
            }
            before["mapping.json"] = (blind_dir / "mapping.json").read_bytes()
            before["pilot-diagnostics.json"] = (blind_dir / "pilot-diagnostics.json").read_bytes()
            self.assertEqual(result["blind_receipts"]["receipt_count"], 2)
            with self.assertRaises(FileExistsError):
                runner.write_blind_receipts({}, blind_dir)
            after = {
                (Path("receipts") / path.name).as_posix(): path.read_bytes()
                for path in (blind_dir / "receipts").iterdir()
            }
            after["mapping.json"] = (blind_dir / "mapping.json").read_bytes()
            after["pilot-diagnostics.json"] = (blind_dir / "pilot-diagnostics.json").read_bytes()
            self.assertEqual(after, before)

    def test_first_action_classes_are_coarse_and_allowlisted(self):
        self.assertEqual(runner._action_class("sed -n '1,4p' README.md", "exec_command"), "source_read")
        self.assertEqual(runner._action_class("python -m pytest", "exec_command"), "test")
        self.assertEqual(runner._action_class("apply_patch < private.patch", "exec_command"), "edit")
        self.assertEqual(runner._action_class("", "file_change"), "edit")
        self.assertEqual(runner._action_class("private-command --secret", "exec_command"), "other")

    def test_candidate_ids_are_reported_only_from_known_ids_in_first_pretool_message(self):
        start = time.monotonic()
        known = runner._known_catalog_ids()
        lines = [
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "JevCompass advice ID: 1234abcd; consider pytest."}}),
            json.dumps({"type": "item.started", "item": {"type": "command_execution", "name": "exec_command", "command": "safe"}}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Later response mentioned git."}}),
        ]
        parsed = runner.parse_event_stream(
            lines, start_monotonic=start, event_times=[start, start + .1, start + .2],
            known_candidate_ids=known,
        )
        self.assertEqual(parsed["agent_reported_candidate_ids"], ["pytest"])
        self.assertNotIn("git", parsed["agent_reported_candidate_ids"])

        unknown_only = runner.parse_event_stream(
            [json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "JevCompass advice ID: 1234abcd; use unlisted-private-id."}})],
            start_monotonic=start, event_times=[start], known_candidate_ids=known,
        )
        self.assertEqual(unknown_only["agent_reported_candidate_ids"], [])

    def test_event_parser_distinguishes_first_tool_from_first_useful_action(self):
        start = time.monotonic()
        lines = [
            json.dumps({"type": "item.completed", "item": {"type": "error", "message": "ignored"}}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "JevCompass advice ID: 1234abcd"}}),
            json.dumps({"type": "item.started", "item": {"type": "command_execution", "name": "exec_command", "command": "secret command"}}),
        ]
        parsed = runner.parse_event_stream(lines, start_monotonic=start, event_times=[start, start + .1, start + .3])
        self.assertEqual(parsed["first_tool"]["order"], 3)
        self.assertEqual(parsed["first_tool"]["elapsed_ms"], 300.0)
        self.assertTrue(parsed["advice_id_before_first_tool"])
        self.assertNotIn("secret command", json.dumps(parsed))

    def test_event_parser_reports_heuristic_source_read_time_without_retaining_command_or_text(self):
        start = time.monotonic()
        assistant_texts = []
        lines = [
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "A private response."}}),
            json.dumps({"type": "item.started", "item": {"type": "command_execution", "name": "exec_command", "command": "sed -n '1,20p' README.md"}}),
        ]
        parsed = runner.parse_event_stream(
            lines, start_monotonic=start, event_times=[start, start + .2],
            assistant_text_sink=assistant_texts,
        )
        self.assertEqual(parsed["first_source_read_ms"], 200.0)
        self.assertEqual(assistant_texts, ["A private response."])
        rendered = json.dumps(parsed)
        self.assertNotIn("README.md", rendered)
        self.assertNotIn("A private response", rendered)

    def test_event_parser_keeps_only_sandboxed_command_check_and_exit_code(self):
        start = time.monotonic()
        lines = [json.dumps({
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "python -m unittest discover -s tests; secret-token", "exit_code": 0},
        })]
        parsed = runner.parse_event_stream(lines, start_monotonic=start, event_times=[start])
        self.assertEqual(parsed["events"][0]["command_check"], "fixture_tests")
        self.assertEqual(parsed["events"][0]["exit_code"], 0)
        self.assertNotIn("secret-token", json.dumps(parsed))

    def test_event_parser_correlates_test_start_and_completion_without_retaining_command(self):
        start = time.monotonic()
        lines = [
            json.dumps({"type": "item.started", "item": {"id": "cmd-1", "type": "command_execution",
                        "command": "python -m unittest discover -s tests; private-token"}}),
            json.dumps({"type": "item.completed", "item": {"id": "cmd-1", "type": "command_execution",
                        "exit_code": 1}}),
            json.dumps({"type": "item.started", "item": {"id": "cmd-2", "type": "command_execution",
                        "command": "python -m unittest discover -s tests"}}),
            json.dumps({"type": "item.completed", "item": {"id": "cmd-2", "type": "command_execution"}}),
        ]
        parsed = runner.parse_event_stream(lines, start_monotonic=start,
                                           event_times=[start] * len(lines))
        self.assertEqual(parsed["events"][1]["command_check"], "fixture_tests")
        self.assertEqual(parsed["events"][1]["exit_code"], 1)
        self.assertEqual(parsed["events"][3]["command_check_completed"], "fixture_tests")
        self.assertNotIn("exit_code", parsed["events"][3])
        self.assertNotIn("private-token", json.dumps(parsed))
        checks = runner._fixture_outcome_checks("P01", Path("."), None, parsed["events"])
        self.assertEqual(checks, {"focused_unittest_exit": False,
                                  "unittest_invocation_observed": True,
                                  "unittest_completion_observed": True})

    def test_p03_checks_syntax_and_unset_guard_without_running_script(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            (fixture / "scripts").mkdir()
            script = fixture / "scripts" / "render_report.sh"
            script.write_text(
                '#!/usr/bin/env bash\nset -u\nprintf "%s\\n" "$OUTPUT_PATH"\ntouch SHOULD_NOT_EXIST\n',
                encoding="utf-8",
            )
            checks = runner._fixture_outcome_checks("P03", fixture, None)
            self.assertEqual(checks, {"bash_syntax": True, "no_unset_output_path_defect": False})
            self.assertFalse((fixture / "SHOULD_NOT_EXIST").exists())
            script.write_text(
                '#!/usr/bin/env bash\nset -u\n: "${OUTPUT_PATH:?required}"\nprintf "%s\\n" "$OUTPUT_PATH"\n',
                encoding="utf-8",
            )
            checks = runner._fixture_outcome_checks("P03", fixture, None)
            self.assertEqual(checks, {"bash_syntax": True, "no_unset_output_path_defect": True})

    def test_p03_argument_based_output_does_not_require_output_path_variable(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            (fixture / "scripts").mkdir()
            (fixture / "scripts" / "render_report.sh").write_text(
                '#!/usr/bin/env bash\nset -euo pipefail\nprintf "%s\\n" "${1:?output required}"\n',
                encoding="utf-8",
            )
            self.assertEqual(
                runner._fixture_outcome_checks("P03", fixture, None),
                {"bash_syntax": True, "no_unset_output_path_defect": True},
            )

    def test_fixture_answer_indicators_are_limited_and_unknown_without_text(self):
        fixture = runner.FIXTURE
        self.assertIsNone(runner._answer_indicator("R01", None, fixture))
        self.assertTrue(runner._answer_indicator("R01", "Branch: fixture-main", fixture))
        count = sum(path.is_file() for path in fixture.iterdir())
        self.assertTrue(runner._answer_indicator("R02", f"There are {count} top-level files.", fixture))
        self.assertTrue(runner._answer_indicator("R03", "README.md exists.", fixture))
        size = (fixture / "pyproject.toml").stat().st_size
        self.assertTrue(runner._answer_indicator("R04", f"pyproject.toml is {size} bytes.", fixture))
        self.assertTrue(runner._answer_indicator("R05", "There are no top-level Python files.", fixture))
        self.assertTrue(runner._answer_indicator("R06", "README.md contains timeout.", fixture))

    def test_p07_outcome_checks_authored_contract_file_not_final_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            contract = fixture / "STATUS_API.md"
            good = "POST /status accepts required and optional inputs, returns a validated status response, and handles invalid input and server errors."
            bad = "POST /status returns a result."
            contract.write_text(good, encoding="utf-8")
            self.assertEqual(
                runner._fixture_outcome_checks("P07", fixture, bad),
                {"contract_indicators": True},
            )
            contract.write_text(bad, encoding="utf-8")
            self.assertEqual(
                runner._fixture_outcome_checks("P07", fixture, good),
                {"contract_indicators": False},
            )
            contract.unlink()
            outside = fixture.parent / "outside-contract.md"
            outside.write_text(good, encoding="utf-8")
            contract.symlink_to(outside)
            self.assertEqual(
                runner._fixture_outcome_checks("P07", fixture, good),
                {"contract_indicators": False},
            )
            contract.unlink()
            self.assertEqual(
                runner._fixture_outcome_checks("P07", fixture, good),
                {"contract_indicators": False},
            )

    def test_p01_p05_never_execute_modified_fixture_python(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture"
            marker = root / "executed"
            (fixture / "tests").mkdir(parents=True)
            (fixture / "tinytext").mkdir()
            (fixture / "tests" / "test_unsafe.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
                encoding="utf-8",
            )
            (fixture / "README.md").write_text(
                "Install with python -m pip install .\nRun python -m tinytext --help.\n"
                "Run python -m unittest discover -s tests.\n", encoding="utf-8",
            )
            (fixture / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
            (fixture / "tinytext" / "cli.py").write_text(
                "parser.add_argument('--check')\nparser.parse_args()\n", encoding="utf-8",
            )
            (fixture / "tinytext" / "__main__.py").write_text("main()\n", encoding="utf-8")

            self.assertEqual(
                runner._fixture_outcome_checks("P01", fixture, None),
                {"focused_unittest_exit": None, "unittest_invocation_observed": False,
                 "unittest_completion_observed": False},
            )
            self.assertEqual(
                runner._fixture_outcome_checks("P05", fixture, None),
                {"install_instruction_coherent": True, "help_instruction_coherent": True,
                 "help_command_exit": None, "test_instruction_exit": None},
            )
            self.assertFalse(marker.exists())
            sandbox_events = [
                {"command_check": "fixture_tests", "exit_code": 0},
                {"command_check": "cli_help", "exit_code": 0},
            ]
            self.assertEqual(
                runner._fixture_outcome_checks("P01", fixture, None, sandbox_events),
                {"focused_unittest_exit": True, "unittest_invocation_observed": False,
                 "unittest_completion_observed": False},
            )
            self.assertEqual(
                runner._fixture_outcome_checks("P05", fixture, None, sandbox_events)["test_instruction_exit"],
                True,
            )
            self.assertFalse(marker.exists())

    def test_frozen_cli_case_bank_labels_match_current_local_classifier(self):
        expected = {
            "P01": ("coding", "python"),
            "P03": ("debugging", "shell"),
            "P05": ("package-docs", "python"),
            "P07": ("api-design", "python"),
        }
        for case_id, label in expected.items():
            self.assertEqual(classify_task(runner.PROMPTS[case_id]), label, case_id)
        for case_id in runner.ROUTINE_CASES:
            self.assertIsNone(classify_task(runner.PROMPTS[case_id]), case_id)

    def test_routine_controls_are_read_only_and_case_sandbox_is_explicit(self):
        self.assertEqual(runner.READ_ONLY_CASES, {"R01", "R02", "R03", "R04", "R05", "R06"})
        self.assertEqual({case for case in runner.CASE_IDS if runner._case_settings(case)["sandbox"] == "workspace-write"}, {"P01", "P03", "P05", "P07"})
        self.assertEqual(runner.ROUTINE_CASES, {"R01", "R02", "R03", "R04", "R05", "R06"})

    def test_installed_release_is_exact_and_credential_free_during_version_check(self):
        with tempfile.TemporaryDirectory() as directory:
            interpreter = Path(directory) / "python"
            interpreter.write_text("synthetic", encoding="utf-8")
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "synthetic-secret"}), \
                 mock.patch.object(runner.subprocess, "run", return_value=mock.Mock(
                     returncode=0, stdout="0.1.16\n",
                 )) as launch:
                runner._check_installed_release(interpreter)
            args, kwargs = launch.call_args
            self.assertEqual(args[0][1], "-I")
            self.assertNotIn("OPENROUTER_API_KEY", kwargs["env"])
            self.assertNotIn("PYTHONPATH", kwargs["env"])
            with mock.patch.object(runner.subprocess, "run", return_value=mock.Mock(
                returncode=0, stdout="0.1.15\n",
            )):
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    runner._check_installed_release(interpreter)
            with mock.patch.object(runner.subprocess, "run", return_value=mock.Mock(
                returncode=0, stdout="0.1.19\n",
            )):
                runner._check_installed_release(interpreter, "0.1.19")
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    runner._check_installed_release(interpreter, "0.1.16")
            with self.assertRaisesRegex(ValueError, "numeric X.Y.Z"):
                runner._check_installed_release(interpreter, "latest")
            with self.assertRaisesRegex(ValueError, "absolute"):
                runner._check_installed_release(Path("relative-python"))

    def test_installed_hook_setup_never_inherits_key_or_checkout_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "synthetic-secret"}), \
                 mock.patch.object(runner.subprocess, "run", return_value=mock.Mock(returncode=0)) as launch:
                runner._install_treatment_hooks(root, root / "python", root / "installed-python")
            args, kwargs = launch.call_args
            self.assertEqual(args[0], [str(root / "installed-python"), "-m", "jevcompass", "install"])
            self.assertNotIn("OPENROUTER_API_KEY", kwargs["env"])
            self.assertNotIn("PYTHONPATH", kwargs["env"])
            self.assertFalse((root / "python" / "sitecustomize.py").exists())

    def test_bundled_skill_install_never_inherits_key_or_checkout_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contents = {
                name: f"private skill body for {name}".encode()
                for name in runner.BUNDLED_SKILL_NAMES
            }

            def install(*args, **kwargs):
                profile = Path(kwargs["env"]["CODEX_HOME"]) / "skills"
                for name, content in contents.items():
                    target = profile / name
                    target.mkdir(parents=True)
                    (target / "SKILL.md").write_bytes(content)
                return mock.Mock(returncode=0)

            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "synthetic-secret"}), \
                 mock.patch.object(runner.subprocess, "run", side_effect=install) as launch:
                installed = runner._install_bundled_skills(root, root / "installed-python")
            self.assertEqual(installed, contents)
            args, kwargs = launch.call_args
            self.assertEqual(args[0], [str(root / "installed-python"), "-m", "jevcompass", "skills", "install"])
            self.assertNotIn("OPENROUTER_API_KEY", kwargs["env"])
            self.assertNotIn("PYTHONPATH", kwargs["env"])

    def test_installed_pair_installs_identical_bundled_skills_before_any_model_arm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / "python"
            interpreter.write_text("synthetic", encoding="utf-8")
            (root / "auth.json").write_text("{}", encoding="utf-8")
            contents = {
                name: f"private skill body for {name}".encode()
                for name in runner.BUNDLED_SKILL_NAMES
            }
            events = []

            def install(home, installed_python):
                events.append(("install", home.name))
                self.assertEqual(installed_python, interpreter)
                return dict(contents)

            def model_arm(**kwargs):
                events.append(("model", kwargs["treatment"]))
                return {"status": "completed", "exit_code": 0}

            with mock.patch.dict(os.environ, {"CODEX_HOME": str(root)}), \
                 mock.patch.object(runner, "_check_installed_release"), \
                 mock.patch.object(runner, "_install_bundled_skills", side_effect=install), \
                 mock.patch.object(runner, "_run_arm", side_effect=model_arm):
                result = runner.run_pilot(
                    mode="run", model="synthetic-model", cases=("P03",),
                    codex="/unused/codex", installed_python=interpreter,
                    with_bundled_skills=True, rng=OrderedRandom(),
                )
            self.assertEqual([kind for kind, _ in events], ["install", "install", "model", "model"])
            metadata = result["cases"]["P03"]["bundled_skills"]
            self.assertTrue(metadata["installed"])
            self.assertEqual(metadata["skill_count"], 2)
            self.assertEqual(metadata["content_sha256"], {
                name: hashlib.sha256(content).hexdigest()
                for name, content in contents.items()
            })
            serialized = json.dumps(result)
            for content in contents.values():
                self.assertNotIn(content.decode(), serialized)

    def test_bundled_skill_setup_failure_stops_before_any_model_arm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / "python"
            interpreter.write_text("synthetic", encoding="utf-8")
            (root / "auth.json").write_text("{}", encoding="utf-8")
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(root)}), \
                 mock.patch.object(runner, "_check_installed_release"), \
                 mock.patch.object(runner, "_install_bundled_skills", side_effect=RuntimeError("setup failed")), \
                 mock.patch.object(runner, "_run_arm") as model_arm:
                with self.assertRaisesRegex(RuntimeError, "setup failed"):
                    runner.run_pilot(
                        mode="run", model="synthetic-model", cases=("P03",),
                        codex="/unused/codex", installed_python=interpreter,
                        with_bundled_skills=True, rng=OrderedRandom(),
                    )
            model_arm.assert_not_called()

    def test_bundled_skill_parity_mismatch_stops_before_any_model_arm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / "python"
            interpreter.write_text("synthetic", encoding="utf-8")
            (root / "auth.json").write_text("{}", encoding="utf-8")
            baseline = {name: b"baseline skill content" for name in runner.BUNDLED_SKILL_NAMES}
            treatment = {name: b"treatment skill content" for name in runner.BUNDLED_SKILL_NAMES}
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(root)}), \
                 mock.patch.object(runner, "_check_installed_release"), \
                 mock.patch.object(runner, "_install_bundled_skills", side_effect=[baseline, treatment]), \
                 mock.patch.object(runner, "_run_arm") as model_arm:
                with self.assertRaisesRegex(RuntimeError, "contents differ"):
                    runner.run_pilot(
                        mode="run", model="synthetic-model", cases=("P03",),
                        codex="/unused/codex", installed_python=interpreter,
                        with_bundled_skills=True, rng=OrderedRandom(),
                    )
            model_arm.assert_not_called()

    def test_bundled_skill_dry_run_verifies_parity_without_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / "python"
            interpreter.write_text("synthetic", encoding="utf-8")
            contents = {name: b"reviewed skill" for name in runner.BUNDLED_SKILL_NAMES}
            with mock.patch.object(runner, "_check_installed_release"), \
                 mock.patch.object(runner, "_install_bundled_skills", return_value=contents) as install, \
                 mock.patch.object(runner, "_run_arm") as model_arm:
                result = runner.run_pilot(
                    mode="dry-run", model=None, cases=("P01",),
                    installed_python=interpreter, with_bundled_skills=True,
                )
            self.assertEqual(install.call_count, 2)
            model_arm.assert_not_called()
            self.assertTrue(result["cases"]["P01"]["bundled_skills"]["installed"])
            self.assertFalse(result["cases"]["P01"]["arms"]["baseline"]["model_called"])

    def test_source_bundled_skills_dry_run_installs_identical_checkout_skills(self):
        from jevcompass.skill_pack import SKILL_NAMES

        result = runner.run_pilot(
            mode="dry-run", model=None, cases=("P03",),
            source_bundled_skills=True, rng=OrderedRandom(),
        )
        metadata = result["cases"]["P03"]["bundled_skills"]
        self.assertTrue(metadata["installed"])
        self.assertEqual(metadata["skill_count"], len(SKILL_NAMES))
        self.assertEqual(set(metadata["content_sha256"]), set(SKILL_NAMES))
        self.assertFalse(result["cases"]["P03"]["arms"]["baseline"]["model_called"])

    def test_source_bundled_skills_reject_mock_or_installed_release(self):
        with self.assertRaisesRegex(ValueError, "source bundled skills require"):
            runner.run_pilot(mode="mock", model="synthetic", cases=("P03",), source_bundled_skills=True)
        with self.assertRaisesRegex(ValueError, "bundled skills require"):
            runner.run_pilot(
                mode="dry-run", model=None, cases=("P03",),
                source_bundled_skills=True, with_bundled_skills=True,
            )

    def test_bundled_skills_require_installed_live_pair_and_release_check_precedes_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / "python"
            interpreter.write_text("synthetic", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "installed-release run or dry run"):
                runner.run_pilot(
                    mode="mock", model="synthetic-model", cases=("P03",),
                    with_bundled_skills=True,
                )
            with mock.patch.object(runner, "_check_installed_release", side_effect=RuntimeError("version mismatch")), \
                 mock.patch.object(runner, "_install_bundled_skills") as install, \
                 mock.patch.object(runner, "_run_arm") as model_arm:
                with self.assertRaisesRegex(RuntimeError, "version mismatch"):
                    runner.run_pilot(
                        mode="run", model="synthetic-model", cases=("P03",),
                        codex="/unused/codex", installed_python=interpreter,
                        with_bundled_skills=True,
                    )
            install.assert_not_called()
            model_arm.assert_not_called()

    def test_cli_exposes_opt_in_bundled_skill_flag(self):
        with mock.patch.object(runner, "run_pilot", return_value={"status": "completed"}) as run:
            self.assertEqual(runner.main([
                "--model", "synthetic-model", "--installed-python", "/tmp/installed-python",
                "--with-bundled-skills",
            ]), 0)
        self.assertTrue(run.call_args.kwargs["with_bundled_skills"])

    def test_installed_pair_forwards_key_equally_only_with_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            interpreter = root / "python"
            interpreter.write_text("synthetic", encoding="utf-8")
            auth = root / "auth.json"
            auth.write_text("{}", encoding="utf-8")
            observed = []
            def fake_arm(**kwargs):
                observed.append((kwargs["treatment"], kwargs["installed_python"],
                                 kwargs["allow_openrouter_key"]))
                return {"status": "completed", "exit_code": 0}
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(root),
                                               "OPENROUTER_API_KEY": "synthetic-secret"}), \
                 mock.patch.object(runner, "_check_installed_release"), \
                 mock.patch.object(runner, "_run_arm", side_effect=fake_arm):
                result = runner.run_pilot(
                    mode="run", model="synthetic-model", cases=("P03",),
                    codex="/unused/codex", installed_python=interpreter,
                    installed_version="0.1.19", allow_openrouter_key=True, rng=OrderedRandom(),
                )
            self.assertEqual(set(observed), {
                (False, interpreter, True), (True, interpreter, True),
            })
            self.assertEqual(result["advisor_source"], "installed-distribution")
            self.assertEqual(result["advisor_version"], "0.1.19")
            self.assertTrue(result["openrouter_key_forwarded"])
            self.assertNotIn("synthetic-secret", json.dumps(result))
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "synthetic-secret"}), \
                 mock.patch.object(runner, "_check_installed_release"):
                with self.assertRaisesRegex(ValueError, "live installed-release"):
                    runner.run_pilot(mode="dry-run", model=None, cases=("P03",),
                                     installed_python=interpreter, allow_openrouter_key=True)
                with self.assertRaisesRegex(ValueError, "live installed-release"):
                    runner.run_pilot(mode="run", model="synthetic-model", cases=("P03",),
                                     allow_openrouter_key=True)

    def test_isolated_environment_drops_credentials_and_proxy_variables(self):
        prior = {key: os.environ.get(key) for key in ("OPENROUTER_API_KEY", "AWS_SECRET_ACCESS_KEY", "HTTPS_PROXY")}
        os.environ["OPENROUTER_API_KEY"] = "synthetic-secret"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "synthetic-secret"
        os.environ["HTTPS_PROXY"] = "https://proxy.invalid"
        try:
            with tempfile.TemporaryDirectory() as directory:
                env = runner._isolated_environment(home=Path(directory), isolated_python=Path(directory) / "python")
                installed_env = runner._isolated_environment(
                    home=Path(directory), isolated_python=Path(directory) / "python",
                    installed_python=Path(directory) / "installed-python",
                    allow_openrouter_key=True,
                )
        finally:
            for key, value in prior.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertEqual(installed_env["OPENROUTER_API_KEY"], "synthetic-secret")
        self.assertNotIn("PYTHONPATH", installed_env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", installed_env)
        self.assertNotIn("HTTPS_PROXY", installed_env)

    def test_dry_run_never_calls_model_and_pairs_all_selected_cases(self):
        result = runner.run_pilot(mode="dry-run", model=None, cases=("P07", "R06"), rng=OrderedRandom())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["case_order"], ["R06", "P07"])
        for case in result["cases"].values():
            self.assertTrue(case["fixture_copies_identical"])
            self.assertTrue(all(not arm["model_called"] for arm in case["arms"].values()))

    def test_bounds_reject_invalid_timeouts_cases_and_effort(self):
        with self.assertRaises(ValueError):
            runner.run_pilot(mode="mock", model="fake", timeout=runner.MAX_TIMEOUT + 1)
        with self.assertRaises(ValueError):
            runner.run_pilot(mode="mock", model="fake", cases=("P02",))
        with self.assertRaises(ValueError):
            runner.run_pilot(mode="mock", model="fake", reasoning_effort="ultra")

    def test_event_collector_times_out_and_kills_fake_process(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            stdout=subprocess.PIPE,
        )
        lines, times, failure = runner._collect_events(process, started=time.monotonic(), timeout=1)
        self.assertEqual((lines, times, failure), ([], [], "timeout"))
        self.assertIsNotNone(process.poll())

    def test_event_collector_can_retain_bounded_partial_events_on_timeout(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import sys,time; print('{\"type\":\"turn.started\"}', flush=True); time.sleep(2)"],
            stdout=subprocess.PIPE,
        )
        lines, times, failure = runner._collect_events(
            process, started=time.monotonic(), timeout=1, preserve_on_failure=True,
        )
        self.assertEqual(failure, "timeout")
        self.assertEqual(lines, ['{"type":"turn.started"}'])
        self.assertEqual(len(times), 1)
        self.assertIsNotNone(process.poll())

    def test_opt_in_quality_artifacts_capture_only_bounded_blind_fixture_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blind_dir = root / "blind"
            fake = make_quality_fake_codex(root / "quality-codex")
            result = runner.run_pilot(
                mode="mock", model="synthetic", timeout=4,
                cases=("P01", "P03", "P05", "P07"), codex=str(fake),
                rng=OrderedRandom(), blind_dir=blind_dir,
                blind_quality_artifacts=True,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["blind_receipts"]["receipt_count"], 8)
            self.assertEqual(result["blind_receipts"]["quality_artifact_count"], 8)
            quality_dir = blind_dir / "quality_artifacts"
            mapping = json.loads((blind_dir / "mapping.json").read_text(encoding="utf-8"))
            tokens = {entry["arm_token"] for entry in mapping["arms"]}
            artifact_paths = list(quality_dir.glob("*.json"))
            self.assertEqual({path.stem for path in artifact_paths}, tokens)
            self.assertEqual({path.name for path in (blind_dir / "receipts").iterdir()}, {f"{token}.json" for token in tokens})
            self.assertEqual({path.name for path in blind_dir.iterdir()}, {"receipts", "quality_artifacts", "mapping.json", "pilot-diagnostics.json"})
            artifacts = [json.loads(path.read_text(encoding="utf-8")) for path in artifact_paths]
            self.assertEqual({item["case_id"] for item in artifacts}, {"P01", "P03", "P05", "P07"})
            for path, artifact in zip(artifact_paths, artifacts):
                rendered = path.read_text(encoding="utf-8")
                self.assertNotIn("baseline", rendered)
                self.assertNotIn("treatment", rendered)
                self.assertNotIn(str(root), rendered)
                self.assertNotIn(str(runner.FIXTURE), rendered)
                self.assertEqual(artifact["schema"], "jevcompass-blind-cli-quality-v1")
                self.assertLessEqual(path.stat().st_size, runner.MAX_BLIND_ARTIFACT_BYTES)
            p01 = next(item for item in artifacts if item["case_id"] == "P01")
            self.assertEqual({item["path"] for item in p01["files"]}, {"tinytext/text.py", "tests/test_text.py"})
            p03 = next(item for item in artifacts if item["case_id"] == "P03")
            self.assertEqual([item["path"] for item in p03["files"]], ["scripts/render_report.sh"])
            p05 = next(item for item in artifacts if item["case_id"] == "P05")
            self.assertEqual([item["path"] for item in p05["files"]], ["README.md"])
            p07 = next(item for item in artifacts if item["case_id"] == "P07")
            self.assertEqual([item["path"] for item in p07["files"]], ["STATUS_API.md"])
            self.assertIn("invalid input", p07["files"][0]["content"].lower())

            scores_path = root / "scores.json"
            scores = [
                {
                    "arm_token": token, "first_productive_action_ms": 1000,
                    "task_quality": True, "required_checks_preserved": True,
                    "blocked": False, "privacy_disclosure": False,
                }
                for token in tokens
            ]
            score_bytes = json.dumps(
                {"schema": "jevcompass-blind-human-scores-v1", "scores": scores},
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            scores_path.write_bytes(score_bytes)
            scored = runner.score_blind_pilot(
                blind_dir / "receipts", blind_dir / "mapping.json",
                scores_path, hashlib.sha256(score_bytes).hexdigest(),
            )
            self.assertTrue(scored["commitment_verified"])
            self.assertEqual(scored["task_quality"]["paired_rated"], 4)

    def test_quality_artifacts_reject_unallowlisted_paths_symlinks_sizes_and_private_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture"
            (fixture / "tinytext").mkdir(parents=True)
            source = runner.FIXTURE / "tinytext" / "text.py"
            outside = root / "outside.txt"
            outside.write_text("safe", encoding="utf-8")
            (fixture / "tinytext" / "text.py").symlink_to(outside)
            with self.assertRaises(OSError):
                runner.build_quality_artifact("P01", fixture)

            (fixture / "tinytext" / "text.py").unlink()
            (fixture / "tinytext" / "text.py").write_text("new output\\n", encoding="utf-8")
            oversized = runner.MAX_BLIND_FILE_BYTES + 1
            (fixture / "tinytext" / "text.py").write_text("x" * oversized, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "size limit"):
                runner.build_quality_artifact("P01", fixture)

            (fixture / "tinytext" / "text.py").write_text("output = '/home/toni/private.txt'\\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "private data"):
                runner.build_quality_artifact("P01", fixture)

            with self.assertRaisesRegex(ValueError, "fixed allowlist"):
                runner._validate_quality_artifact(
                    {
                        "schema": "jevcompass-blind-cli-quality-v1", "case_id": "P01",
                        "files": [{"path": "../outside.txt", "content": "safe"}],
                    },
                    "P01",
                )
            contract = fixture / "STATUS_API.md"
            contract.write_text("Authorization: Bearer super-secret-token-value", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "private data"):
                runner.build_quality_artifact("P07", fixture)
            contract.write_text("See /tmp/synthetic-run/source.py for details.", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "private data"):
                runner.build_quality_artifact("P07", fixture)
            contract.write_text(runner.PROMPTS["P07"], encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "task prompt"):
                runner.build_quality_artifact("P07", fixture)
            contract.write_text("POST /status handles required and optional fields and documents validation errors.", encoding="utf-8")
            artifact = runner.build_quality_artifact(
                "P07", fixture,
                "This final answer must not replace the authored contract artifact.",
            )
            self.assertEqual(artifact["files"][0]["path"], "STATUS_API.md")
            self.assertNotIn("final_answer", artifact)
            runner._validate_quality_artifact(artifact, "P07")
            self.assertTrue(source.is_file())


if __name__ == "__main__":
    unittest.main()
