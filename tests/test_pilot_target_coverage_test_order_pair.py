"""Offline tests for the target-coverage cross-layer pair adapter."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pilot_cross_layer_test_order_pair as cross_layer  # noqa: E402
import pilot_target_coverage_test_order_pair as runner  # noqa: E402


def _arm(first_candidate: str, *, treatment: bool = False,
         validation: bool = True) -> dict:
    result = {
        "cli_status": "completed",
        "required_suite_exit": 0 if validation else None,
        "required_suite_invocation_observed": validation,
        "focused_test_exits": [{"candidate_id": first_candidate, "exit_code": 0}],
        "independent_final_validation_status": "passed" if validation else "failed",
        "changed_source_observed": True,
        "immutable_files_preserved": True,
        "first_focused_candidate": first_candidate,
    }
    if treatment:
        result.update({
            "post_change_rank_phase_status": "verified",
            "choice_follow_status": "followed",
        })
    return result


class TargetCoverageRunnerTests(unittest.TestCase):
    def test_enrichment_is_grounded_and_leaves_frozen_fixture_unchanged(self):
        original = (runner.FIXTURE / "test-options.json").read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            enriched = runner._prepare_enriched_fixture(Path(temporary) / "staged")
            options = json.loads((enriched / "test-options.json").read_text())
            self.assertEqual(
                options["signals"],
                ["boundary_mapping_changed", "internal_logic_changed",
                 "public_contract_changed"],
            )
            by_id = {item["id"]: item for item in options["candidates"]}
            self.assertEqual(by_id["unit"]["coverage_targets"], ["internal_logic_changed"])
            self.assertEqual(by_id["integration"]["coverage_targets"], [
                "boundary_mapping_changed", "internal_logic_changed",
                "public_contract_changed",
            ])
            self.assertEqual(options["required"], [
                {"id": "full", "command": runner.cross_layer.REQUIRED_COMMAND},
            ])
            self.assertNotEqual((enriched / "test-options.json").read_bytes(), original)
        self.assertEqual((runner.FIXTURE / "test-options.json").read_bytes(), original)

    def test_modified_reviewed_evidence_is_rejected_before_arm_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            changed_source = root / "source"
            shutil.copytree(runner.FIXTURE, changed_source)
            test = changed_source / "tests" / "test_integration_checkout.py"
            test.write_text(test.read_text().replace(
                '"service_fee_cents": 25', '"service_fee_cents": 24',
            ))
            with self.assertRaisesRegex(ValueError, "reviewed target evidence"):
                runner._prepare_enriched_fixture(root / "staged", source=changed_source)
            self.assertFalse((root / "staged").exists())

    def test_pair_reuses_public_runner_with_equal_metadata_and_unbiased_baseline(self):
        observed = {}

        def fake_pair(**kwargs):
            source = kwargs["fixture_source"]
            observed.update(kwargs)
            copy_a, copy_b = source.parent / "copy-a", source.parent / "copy-b"
            digest_a = cross_layer.engine._copy_identical_fixture(source, copy_a)
            digest_b = cross_layer.engine._copy_identical_fixture(source, copy_b)
            self.assertEqual(digest_a, digest_b)
            options_a = json.loads((copy_a / "test-options.json").read_text())
            options_b = json.loads((copy_b / "test-options.json").read_text())
            self.assertEqual(options_a, options_b)
            observed["options"] = options_a
            return {"status": "failed", "arms": {}}

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "receipt-dir"
            with mock.patch.object(runner.cross_layer, "run_pair", side_effect=fake_pair):
                result = runner.run_pair(
                    codex="/not-run", model="gpt-6-luna", reasoning_effort="low",
                    timeout=10, seed=11, output_dir=output,
                )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(observed["baseline_first_candidate_policy"], "either")
        self.assertEqual(observed["baseline_prompt"], runner.BASELINE_PROMPT)
        self.assertEqual(observed["treatment_prompt_suffix"], runner.TARGET_RANKING)
        self.assertIn("own judgment", runner.BASELINE_PROMPT)
        self.assertNotIn("faster-unit-first", runner.BASELINE_PROMPT)
        self.assertNotIn("rank --input", runner.BASELINE_PROMPT)
        self.assertIn(runner.cross_layer.RANK_COMMAND, runner.TARGET_RANKING)
        self.assertIn("coverage_targets", json.dumps(observed["options"]))

    def _run_cross_layer_gate(self, *, policy="unit", baseline_first="unit",
                              validation=True):
        output = Path(self._temp.name) / f"output-{policy}-{baseline_first}-{validation}"
        output.mkdir()
        mapping = {"arm-a": "baseline", "arm-b": "treatment"}
        (output / "arm-map.json").write_text(json.dumps(mapping))
        (output / "receipt.json").write_text("{}")
        arms = {
            "arm-a": _arm(baseline_first, validation=validation),
            "arm-b": _arm("integration", treatment=True, validation=validation),
        }

        def fake_pair(**kwargs):
            self.assertEqual(kwargs["baseline_prompt"], runner.BASELINE_PROMPT)
            self.assertEqual(kwargs["treatment_prompt_suffix"], runner.TARGET_RANKING)
            return {"status": "completed", "arms": arms}

        with mock.patch.object(cross_layer, "_original_pair", side_effect=fake_pair):
            return cross_layer.run_pair(
                output_dir=output,
                fixture_source=runner.FIXTURE,
                baseline_prompt=runner.BASELINE_PROMPT,
                treatment_prompt_suffix=runner.TARGET_RANKING,
                baseline_first_candidate_policy=policy,
            ), output

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._temp.cleanup()

    def test_legacy_default_still_requires_unit_first(self):
        result, output = self._run_cross_layer_gate(baseline_first="unit")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["baseline_first_candidate_policy"], "unit")
        self.assertEqual(result["quality_gate_failures"], [])

        integration_first, _ = self._run_cross_layer_gate(baseline_first="integration")
        self.assertEqual(integration_first["status"], "failed")
        self.assertIn("baseline_unit_first_policy_not_observed",
                      integration_first["quality_gate_failures"])

    def test_either_policy_accepts_both_candidates_but_keeps_validation_gates(self):
        for candidate in ("unit", "integration"):
            with self.subTest(candidate=candidate):
                result, _ = self._run_cross_layer_gate(
                    policy="either", baseline_first=candidate,
                )
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["baseline_first_candidate_policy"], "either")
                self.assertEqual(result["quality_gate_failures"], [])

        invalid, output = self._run_cross_layer_gate(
            policy="either", baseline_first="integration", validation=False,
        )
        self.assertEqual(invalid["status"], "failed")
        self.assertIn("arm-a_required_suite_unverified",
                      invalid["quality_gate_failures"])
        self.assertIn("arm-a_independent_final_validation_failed",
                      invalid["quality_gate_failures"])

        receipt_text = (output / "receipt.json").read_text()
        for private_value in (
            "coverage_targets", "checkout/service.py", "test-options.json",
            runner.BASELINE_PROMPT, runner.TARGET_RANKING,
        ):
            self.assertNotIn(private_value, receipt_text)

    def test_baseline_policy_rejects_unbounded_or_unknown_values(self):
        for policy in (["unit", "integration"], "provider-choice"):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as temporary:
                with self.assertRaisesRegex(ValueError, "invalid baseline"):
                    cross_layer.run_pair(
                        output_dir=Path(temporary),
                        baseline_first_candidate_policy=policy,
                    )


if __name__ == "__main__":
    unittest.main()
