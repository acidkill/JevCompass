from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from jevcompass._typed_decision_cache import (
    MAX_ENTRIES, TTL_SECONDS, TypedDecisionCache,
)
from jevcompass.decisions import DecisionsClient


class TypedDecisionCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache_dir = Path(self.temp.name) / "cache"
        self.env = patch.dict(os.environ, {
            "JEVCOMPASS_TYPED_DECISION_CACHE": "1",
            "JEVCOMPASS_TYPED_CACHE_DIR": str(self.cache_dir),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def make_cache(self, *, scope="test_order", model="m1", policy="p1",
                   threshold=0.70, request=None):
        return TypedDecisionCache(
            scope=scope, model=model, policy_version=policy,
            confidence_threshold=threshold,
            request=request or {"state": {"surface": "python", "signals": ["api"]},
                                "questions": {"first": {"criteria": {"t1": "unit"}}}},
        )

    def test_opt_in_and_safe_atomic_file_permissions_and_private_values_absent(self):
        request = {"state": {"surface": "python", "candidate": "PRIVATE_CALLER_ID",
                              "command": "/private/work/pytest --password=secret"},
                   "questions": {"q": {"instructions": "fixed typed question"}}}
        cache = self.make_cache(request=request)
        allowed = {"t1"}
        cache.put(("t1",), .91, eligible_tokens=allowed)
        self.assertEqual(cache.get(allowed).tokens, ("t1",))
        self.assertEqual(stat.S_IMODE(self.cache_dir.stat().st_mode), 0o700)
        record = next(self.cache_dir.glob("*.json"))
        self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
        disk = record.name + record.read_text()
        for secret in ("PRIVATE_CALLER_ID", "/private/work", "pytest", "password", "secret", "instructions"):
            self.assertNotIn(secret, disk)
        with patch.dict(os.environ, {"JEVCOMPASS_TYPED_DECISION_CACHE": "0"}):
            disabled = self.make_cache(request={"state": {"surface": "python"}})
            self.assertIsNone(disabled.get(allowed))

    def test_reuses_record_in_a_new_process(self):
        request = {"state": {"surface": "python", "signals": ["api"]},
                   "questions": {"first": {"criteria": {"t1": "unit"}}}}
        self.make_cache(request=request).put(("t1",), .91, eligible_tokens={"t1"})
        code = (
            "from jevcompass._typed_decision_cache import TypedDecisionCache; "
            f"c=TypedDecisionCache(scope='test_order',model='m1',policy_version='p1',"
            f"confidence_threshold=.70,request={request!r}); "
            "r=c.get({'t1'}); assert r and r.tokens==('t1',) and r.confidence==.91; print('hit')"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        completed = subprocess.run([sys.executable, "-c", code], env=env, text=True,
                                   capture_output=True, check=True, timeout=10)
        self.assertEqual(completed.stdout.strip(), "hit")

    def test_model_policy_threshold_scope_and_request_invalidate(self):
        request = {"state": {"surface": "python"}}
        base = self.make_cache(request=request)
        base.put(("t1",), .90, eligible_tokens={"t1"})
        variants = (
            self.make_cache(model="m2", request=request),
            self.make_cache(policy="p2", request=request),
            self.make_cache(threshold=.71, request=request),
            self.make_cache(scope="triage", request=request),
            self.make_cache(request={"state": {"surface": "api"}}),
            self.make_cache(request={"state": {"surface": "python", "signals": ["dep", "api"]},
                                             "questions": {"first": {"criteria": {"t1": "unit"}}}}),
            self.make_cache(request={"state": {"surface": "python", "signals": ["api"]},
                                             "questions": {"first": {"criteria": {"t2": "integration", "t1": "unit"}}}}),
        )
        for variant in variants:
            with self.subTest(key=variant._key):
                self.assertIsNone(variant.get({"t1"}))

    def test_invalid_low_confidence_expired_and_corrupt_records_miss(self):
        cache = self.make_cache()
        cache.put(("t1",), .69, eligible_tokens={"t1"})
        self.assertEqual(list(self.cache_dir.glob("*.json")), [])
        cache.put(("t1",), .90, eligible_tokens={"t1"})
        path, _directory = cache._record_path()
        data = json.loads(path.read_text())
        import time
        data["created"] = time.time() - TTL_SECONDS - 1
        path.write_text(json.dumps(data))
        self.assertIsNone(cache.get({"t1"}))
        path.write_text("not json")
        self.assertIsNone(cache.get({"t1"}))
        cache.put(("private-id",), .90, eligible_tokens={"private-id"})
        self.assertEqual(list(self.cache_dir.glob("*.json")), [path])

    def test_hit_revalidates_allowlist_and_confidence_and_prunes_entry_count(self):
        cache = self.make_cache()
        cache.put(("t1",), .90, eligible_tokens={"t1", "t2"})
        self.assertIsNone(cache.get({"t2"}))
        path, _directory = cache._record_path()
        data = json.loads(path.read_text())
        data["confidence"] = .60
        path.write_text(json.dumps(data))
        self.assertIsNone(cache.get({"t1", "t2"}))
        for index in range(MAX_ENTRIES + 2):
            other = self.make_cache(request={"state": {"case": index}})
            other.put(("t1",), .90, eligible_tokens={"t1"})
        self.assertLessEqual(len(list(self.cache_dir.glob("*.json"))), MAX_ENTRIES)
        unrelated = self.cache_dir / "unrelated.json"
        unrelated.write_text("leave me")
        unrelated_temp = self.cache_dir / ".tmp-user-sentinel"
        unrelated_temp.write_text("also leave me")
        other = self.make_cache(request={"state": {"case": "final"}})
        other.put(("t1",), .90, eligible_tokens={"t1"})
        self.assertEqual(unrelated.read_text(), "leave me")
        self.assertEqual(unrelated_temp.read_text(), "also leave me")

    def test_cache_io_errors_fail_open(self):
        with patch.dict(os.environ, {"JEVCOMPASS_TYPED_CACHE_DIR": str(self.cache_dir / "file") }):
            self.cache_dir.mkdir(mode=0o700)
            (self.cache_dir / "file").write_text("occupied")
            cache = self.make_cache()
            self.assertIsNone(cache.get({"t1"}))
            cache.put(("t1",), .90, eligible_tokens={"t1"})


class TypedCallerCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {
            "JEVCOMPASS_TYPED_DECISION_CACHE": "1",
            "JEVCOMPASS_TYPED_CACHE_DIR": str(Path(self.temp.name) / "cache"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.calls = []
        self.response_confidence = .93
        self.transport_error = False

    def factory(self, **kwargs):
        def transport(_url, body, _key, _timeout):
            self.calls.append(body)
            if self.transport_error:
                raise OSError("offline-test")
            request = json.loads(body)
            answers = {}
            for key, question in request["questions"].items():
                selected = next(iter(question["criteria"]))
                answers[key] = {"type": "choice", "choice": selected,
                                "confidence": self.response_confidence}
            return json.dumps({"answers": answers,
                               "usage": {"input_tokens": 7, "output_tokens": 2}}).encode()
        options = dict(kwargs)
        options.pop("timeout", None)
        return DecisionsClient(api_key="test-api-key", model="local-fixture-model",
                               transport=transport, **options)

    def test_strategy_cache_hit_is_explicit_and_has_no_replayed_usage(self):
        from jevcompass import strategy
        with patch.object(strategy, "DecisionsClient", side_effect=self.factory):
            first = strategy.choose_strategies(
                strategy.TaskKind.CODING,
                {strategy.TaskSignal.DEPENDENCY_CHANGE, strategy.TaskSignal.BEHAVIOR_CHANGE},
            )
            second = strategy.choose_strategies(
                strategy.TaskKind.CODING,
                {strategy.TaskSignal.DEPENDENCY_CHANGE, strategy.TaskSignal.BEHAVIOR_CHANGE},
            )
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertIsNotNone(first.usage)
        self.assertIsNone(second.usage)

    def test_test_order_cache_hit_preserves_local_command_and_usage_is_none(self):
        from jevcompass import test_order
        candidates = (
            test_order.TestCandidate(test_order.TestKind.UNIT, "/private/pytest --token=SECRET",
                                     "PRIVATE-A", .50, test_order.Coverage.DIRECT,
                                     test_order.RuntimeBucket.SLOW),
            test_order.TestCandidate(test_order.TestKind.INTEGRATION, "/private/integration SECRET",
                                     "PRIVATE-B", .50, test_order.Coverage.INDIRECT,
                                     test_order.RuntimeBucket.FAST),
        )
        with patch.object(test_order, "DecisionsClient", side_effect=self.factory):
            first = test_order.rank_tests(test_order.ChangedSurface.API, candidates, ())
            second = test_order.rank_tests(test_order.ChangedSurface.API, candidates, ())
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(second.ordered_candidates[0].command, "/private/pytest --token=SECRET")
        self.assertIsNotNone(first.usage)
        self.assertIsNone(second.usage)
        disk = "".join(path.name + path.read_text() for path in
                        (Path(self.temp.name) / "cache").glob("*.json"))
        self.assertNotIn("PRIVATE-A", disk)
        self.assertNotIn("pytest", disk)
        self.assertNotIn("SECRET", disk)
        self.assertNotIn("test-api-key", disk)

    def test_triage_cache_hit_keeps_validated_hypothesis_and_has_no_usage(self):
        from jevcompass import triage
        kinds = (triage.FailureKind.IMPORT,)
        hypotheses = (triage.HypothesisId.IMPORT_MODULE_MISSING,
                      triage.HypothesisId.IMPORT_PATH_CHANGED)
        with patch.object(triage, "DecisionsClient", side_effect=self.factory):
            first = triage.triage_failure(kinds, hypotheses, 1)
            second = triage.triage_failure(kinds, hypotheses, 1)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(first.steps, second.steps)
        self.assertIsNotNone(first.decision_usage)
        self.assertIsNone(second.decision_usage)


    def test_low_confidence_and_transport_errors_are_never_cached(self):
        from jevcompass import triage
        kinds = (triage.FailureKind.IMPORT,)
        hypotheses = (triage.HypothesisId.IMPORT_MODULE_MISSING,
                      triage.HypothesisId.IMPORT_PATH_CHANGED)
        self.response_confidence = .60
        with patch.object(triage, "DecisionsClient", side_effect=self.factory):
            low_a = triage.triage_failure(kinds, hypotheses, 1)
            low_b = triage.triage_failure(kinds, hypotheses, 1)
        self.assertFalse(low_a.cache_hit)
        self.assertFalse(low_b.cache_hit)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(list((Path(self.temp.name) / "cache").glob("*.json")), [])

        self.response_confidence = .93
        self.transport_error = True
        with patch.object(triage, "DecisionsClient", side_effect=self.factory):
            error_a = triage.triage_failure(kinds, hypotheses, 2)
            error_b = triage.triage_failure(kinds, hypotheses, 2)
        self.assertFalse(error_a.cache_hit)
        self.assertFalse(error_b.cache_hit)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(list((Path(self.temp.name) / "cache").glob("*.json")), [])

    def test_explicit_injected_client_bypasses_disk_cache(self):
        from jevcompass import test_order
        candidate_pair = (
            test_order.TestCandidate(test_order.TestKind.UNIT, "unit", None, .5,
                                     test_order.Coverage.DIRECT, test_order.RuntimeBucket.SLOW),
            test_order.TestCandidate(test_order.TestKind.INTEGRATION, "integration", None, .5,
                                     test_order.Coverage.INDIRECT, test_order.RuntimeBucket.FAST),
        )
        client = self.factory()
        first = test_order.rank_tests(test_order.ChangedSurface.API, candidate_pair, (), client=client)
        second = test_order.rank_tests(test_order.ChangedSurface.API, candidate_pair, (), client=client)
        self.assertFalse(first.cache_hit)
        self.assertFalse(second.cache_hit)
        self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()
