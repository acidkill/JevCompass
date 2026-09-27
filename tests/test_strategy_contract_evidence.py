"""Offline contract evidence handling for reviewed strategy choices."""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from jevcompass.cli import main
from jevcompass.strategy import ContractEvidence, StrategyId, choose_strategies


CODING_PAIR = ["existing_symbol", "behavior_change"]


class ContractEvidenceTests(unittest.TestCase):
    def local_choice(self, evidence: str, resolved: str | None = None):
        kwargs = {"contract_evidence": evidence}
        if resolved is not None:
            kwargs["resolved_strategy"] = resolved
        with patch("jevcompass.strategy.DecisionsClient", side_effect=AssertionError("client constructed")) as factory:
            result = choose_strategies("coding", CODING_PAIR, **kwargs)
        factory.assert_not_called()
        self.assertEqual(result.status, "no-remote-choice")
        self.assertIsNone(result.usage)
        return result

    def test_consistent_locally_resolves_to_inspect(self):
        result = self.local_choice(ContractEvidence.CONSISTENT.value)
        self.assertEqual([item.id for item in result.recommendations],
                         [StrategyId.INSPECT_DEPENDENCY_OR_SYMBOL_USE])

    def test_conflicting_and_absent_locally_resolve_contract_first(self):
        for evidence in (ContractEvidence.CONFLICTING.value, ContractEvidence.ABSENT.value):
            with self.subTest(evidence=evidence):
                result = self.local_choice(evidence)
                self.assertEqual([item.id for item in result.recommendations],
                                 [StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT])

    def test_conflicting_evidence_advice_supports_plan_resolution(self):
        for evidence in (ContractEvidence.CONFLICTING.value, ContractEvidence.ABSENT.value):
            with self.subTest(evidence=evidence):
                result = self.local_choice(evidence)
                rationale = result.recommendations[0].rationale.lower()
                self.assertIn("establish the required behavior and precedence", rationale)
                self.assertIn("resolving any conflict explicitly", rationale)
                self.assertIn("if a failure is provided", rationale)
                self.assertIn("preserve its original test and symptom", rationale)
                self.assertIn("regression that distinguishes", rationale)
                self.assertIn("mandatory validation commands", rationale)
                self.assertNotIn("the failing test", rationale)

    def test_contract_resolution_guidance_stays_out_of_scope(self):
        consistent = self.local_choice(ContractEvidence.CONSISTENT.value)
        self.assertNotIn(
            "establish the required behavior",
            consistent.recommendations[0].rationale.lower(),
        )

        with patch("jevcompass.strategy.DecisionsClient", side_effect=AssertionError("client constructed")) as factory:
            other_kind = choose_strategies(
                "testing", CODING_PAIR, contract_evidence=ContractEvidence.CONFLICTING,
            )
        factory.assert_not_called()
        self.assertEqual(other_kind.status, "no-remote-choice")
        self.assertEqual(len(other_kind.recommendations), 1)
        self.assertNotIn(
            "establish the required behavior",
            other_kind.recommendations[0].rationale.lower(),
        )

        client = Mock()
        client.decide.return_value = {
            "strategy": {
                "type": "choice",
                "choice": StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT.value,
                "confidence": 0.9,
            }
        }
        remote = choose_strategies(
            "coding", CODING_PAIR, client=client,
            contract_evidence=ContractEvidence.UNKNOWN,
        )
        self.assertEqual(remote.status, "remote-choice")
        self.assertNotIn(
            "establish the required behavior",
            remote.recommendations[0].rationale.lower(),
        )

    def test_explicit_resolution_must_agree_with_contract_evidence(self):
        agreed = self.local_choice(
            ContractEvidence.CONSISTENT.value,
            StrategyId.INSPECT_DEPENDENCY_OR_SYMBOL_USE.value,
        )
        self.assertEqual(agreed.recommendations[0].id, StrategyId.INSPECT_DEPENDENCY_OR_SYMBOL_USE)

        disagreed = self.local_choice(
            ContractEvidence.CONSISTENT.value,
            StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT.value,
        )
        self.assertEqual(disagreed.recommendations, ())

    def test_partial_with_explicit_local_resolution_stays_local(self):
        result = self.local_choice(
            ContractEvidence.PARTIAL.value,
            StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT.value,
        )
        self.assertEqual(result.recommendations[0].id, StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT)

    def test_invalid_and_private_values_abstain_without_client(self):
        for value in ("private prompt: customer data", "CLIENT_SECRET", "consistent or conflicting"):
            with self.subTest(value=value):
                result = self.local_choice(value)
                self.assertEqual(result.recommendations, ())

    def test_partial_is_only_enum_evidence_added_to_existing_remote_state(self):
        client = Mock()
        client.decide.return_value = {
            "strategy": {
                "type": "choice",
                "choice": StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT.value,
                "confidence": 0.9,
            }
        }
        result = choose_strategies(
            "coding", CODING_PAIR, client=client,
            contract_evidence=ContractEvidence.PARTIAL,
        )

        self.assertEqual(result.status, "remote-choice")
        state = client.decide.call_args.args[0]
        self.assertEqual(state, {
            "task_kind": "coding",
            "signals": ["behavior_change", "existing_symbol"],
            "contract_evidence": "partial",
        })
        self.assertEqual(client.decide.call_args.args[1]["strategy"]["type"], "choice")

    def test_unknown_and_none_keep_legacy_state_and_remote_behavior(self):
        for evidence in (None, ContractEvidence.UNKNOWN.value):
            with self.subTest(evidence=evidence):
                client = Mock()
                client.decide.return_value = {
                    "strategy": {
                        "type": "choice",
                        "choice": StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT.value,
                        "confidence": 0.9,
                    }
                }
                result = choose_strategies("coding", CODING_PAIR, client=client,
                                           contract_evidence=evidence)
                self.assertEqual(result.status, "remote-choice")
                self.assertEqual(client.decide.call_args.args[0], {
                    "task_kind": "coding",
                    "signals": ["behavior_change", "existing_symbol"],
                })

    def test_partial_does_not_create_client_when_only_one_candidate_exists(self):
        with patch("jevcompass.strategy.DecisionsClient", side_effect=AssertionError("client constructed")) as factory:
            result = choose_strategies(
                "coding", ["existing_symbol"], contract_evidence="partial",
            )
        factory.assert_not_called()
        self.assertEqual(result.status, "no-remote-choice")
        self.assertEqual([item.id for item in result.recommendations],
                         [StrategyId.INSPECT_DEPENDENCY_OR_SYMBOL_USE])

    def test_out_of_scope_ambiguous_task_omits_contract_metadata(self):
        for evidence in ("consistent", "partial"):
            with self.subTest(evidence=evidence):
                client = Mock()
                client.decide.return_value = {
                    "strategy": {
                        "type": "choice",
                        "choice": StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT.value,
                        "confidence": 0.9,
                    }
                }
                result = choose_strategies(
                    "coding", CODING_PAIR + ["regression_risk"],
                    client=client, contract_evidence=evidence,
                )
                self.assertEqual(result.status, "remote-choice")
                self.assertEqual(client.decide.call_args.args[0], {
                    "task_kind": "coding",
                    "signals": ["behavior_change", "existing_symbol", "regression_risk"],
                })

    def test_contract_rule_does_not_apply_to_other_kinds_or_signal_sets(self):
        for kind, signals, expected in (
            ("testing", CODING_PAIR, StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT),
            ("coding", CODING_PAIR + ["unclear_contract"], StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT),
        ):
            with self.subTest(kind=kind, signals=signals):
                with patch("jevcompass.strategy.DecisionsClient", side_effect=AssertionError("client constructed")) as factory:
                    result = choose_strategies(kind, signals, contract_evidence="consistent")
                factory.assert_not_called()
                self.assertEqual([item.id for item in result.recommendations], [expected])

    def test_cli_accepts_enum_and_rejects_arbitrary_contract_text(self):
        output = io.StringIO()
        with patch("jevcompass.strategy.DecisionsClient", side_effect=AssertionError("client constructed")) as factory:
            with contextlib.redirect_stdout(output):
                code = main([
                    "strategy", "choose", "--kind", "coding",
                    "--signal", "existing_symbol", "--signal", "behavior_change",
                    "--contract-evidence", "absent", "--json",
                ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["strategies"][0]["id"],
                         StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT.value)
        factory.assert_not_called()

        with patch("jevcompass.strategy.DecisionsClient", side_effect=AssertionError("client constructed")) as factory:
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main([
                        "strategy", "choose", "--kind", "coding",
                        "--signal", "existing_symbol", "--signal", "behavior_change",
                        "--contract-evidence", "raw prompt text",
                    ])
        self.assertEqual(raised.exception.code, 2)
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
