from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pilot_cohort_manifest", ROOT / "scripts" / "pilot_cohort_manifest.py")
manifest_module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(manifest_module)


class CohortManifestTests(unittest.TestCase):
    def test_exact_schedule_and_seed_mapping(self):
        result = manifest_module.build_manifest(ROOT)
        feasibility = result["schedules"]["feasibility"]
        main = result["schedules"]["main"]
        self.assertEqual(len(feasibility), 6)
        self.assertEqual(len(main), 20)
        self.assertTrue(all(row["schedule_verified"] for row in feasibility + main))
        self.assertEqual(sum(row["first_arm"] == "baseline" for row in main), 10)
        self.assertEqual(sum(row["first_arm"] == "treatment" for row in main), 10)

    def test_missing_budgets_and_identity_are_not_ready(self):
        result = manifest_module.build_manifest(ROOT)
        self.assertEqual(result["status"], "not_ready")
        blockers = set(result["blockers"])
        self.assertIn("missing:per_arm_timeout_seconds", blockers)
        self.assertIn("missing:total_runtime_seconds", blockers)
        self.assertIn("missing:per_arm_token_budget", blockers)
        self.assertIn("missing:total_token_budget", blockers)
        self.assertIn("missing:observed_identity", blockers)
        self.assertIn("token_budget_monitor_not_wired_to_cohort_runners", blockers)
        self.assertFalse(result["launch_authorized"])

    def test_timeout_remote_ambiguous_stratum_unavailable(self):
        result = manifest_module.build_manifest(ROOT)
        case = result["cases"]["ambiguous_failure_triage"]
        self.assertEqual(case["status"], "unavailable")
        self.assertTrue(any("strips_key" in item for item in case["reasons"]))

    def test_local_control_is_excluded_from_remote_ambiguity(self):
        result = manifest_module.build_manifest(ROOT)
        control = result["excluded_controls"][0]
        self.assertEqual(control["status"], "unavailable")
        self.assertIn("not_genuine_remote_ambiguity", control["reason"])

    def test_tampered_manifest_artifact_fails_validation(self):
        result = manifest_module.build_manifest(ROOT)
        candidate = next(iter(result["artifacts"]))
        result["artifacts"][candidate]["sha256"] = "0" * 64
        issues = manifest_module.validate_manifest(result, ROOT)
        self.assertIn(f"artifact_changed:{candidate}", issues)

    def test_empty_inventory_and_altered_schedule_fail_validation(self):
        result = manifest_module.build_manifest(ROOT)
        result["artifacts"] = {}
        result["schedules"]["main"][0]["seed"] = -1
        issues = manifest_module.validate_manifest(result, ROOT)
        self.assertIn("artifact_inventory_mismatch", issues)
        self.assertIn("schedule_mismatch", issues)

    def test_path_traversal_artifact_is_rejected_by_fixed_inventory(self):
        result = manifest_module.build_manifest(ROOT)
        result["artifacts"]["../outside"] = {"status": "pinned", "sha256": "0" * 64}
        self.assertIn("artifact_inventory_mismatch",
                      manifest_module.validate_manifest(result, ROOT))

    def test_malformed_manifest_and_missing_protocol_fail_closed(self):
        self.assertEqual(manifest_module.validate_manifest([], ROOT),
                         ["invalid_manifest_type"])
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            self.assertIn("protocol_unavailable",
                          manifest_module.validate_manifest({}, Path(temp)))

    def test_nonpositive_or_inconsistent_caps_fail_validation(self):
        result = manifest_module.build_manifest(
            ROOT, model="gpt-6-luna", reasoning_effort="low",
            per_arm_timeout_seconds=180, total_runtime_seconds=1800,
            per_arm_token_budget=5000, total_token_budget=10000,
            execution_path="source_checkout_cli", expected_identity="codex-0.157.0",
            observed_identity="codex-0.157.0",
        )
        result["settings"]["per_arm_agent_token_budget"] = "5000"
        result["settings"]["total_runtime_seconds"] = 0
        issues = manifest_module.validate_manifest(result, ROOT)
        self.assertIn("invalid:per_arm_agent_token_budget", issues)
        self.assertIn("invalid:total_runtime_seconds", issues)

    def test_missing_artifact_is_unavailable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            fake_root = Path(temp)
            (fake_root / "BENCHMARK_PROTOCOL.md").write_text("protocol", encoding="utf-8")
            (fake_root / "scripts").mkdir()
            (fake_root / "tests").mkdir()
            result = manifest_module.build_manifest(fake_root)
            self.assertEqual(result["status"], "not_ready")
            self.assertTrue(any(item.startswith("artifact_unavailable:") for item in result["blockers"]))

    def test_pretask_source_hash_ignores_generated_bytecode(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prefix = root / "tests/fixtures/coding_test_order"
            source = prefix / "sample.py"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"reviewed source")
            cache = prefix / "pkg/__pycache__/sample.pyc"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"cache one")
            first_hash, first_files = manifest_module._fixture_tree(root, "pretask_strategy")
            cache.write_bytes(b"different generated bytecode")
            second_hash, second_files = manifest_module._fixture_tree(root, "pretask_strategy")
            self.assertEqual(first_hash, second_hash)
            self.assertEqual(first_files, ["tests/fixtures/coding_test_order/sample.py"])
            self.assertEqual(first_files, second_files)


if __name__ == "__main__":
    unittest.main()
