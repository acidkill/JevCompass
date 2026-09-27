"""End-to-end opt-in hook strategy path with a fake Decisions transport."""

import json
import os
import unittest
from unittest import mock

from jevcompass import advisor, strategy
from jevcompass.decisions import DecisionsClient


AMBIGUOUS_PROMPT = (
    "Implement a Python change to the behavior of the existing function normalize: "
    "preserve output ordering. private-hook-strategy-sentinel"
)
LOCAL_PROMPT = "Implement the existing function normalize."


class HookStrategyRemotePathTests(unittest.TestCase):
    def _evaluate(self, prompt, transport):
        constructed = []

        def client_factory(**kwargs):
            client = DecisionsClient(
                api_key="synthetic-test-key",
                model="fixture-model",
                transport=transport,
                **kwargs,
            )
            constructed.append(client)
            return client

        with mock.patch.dict(os.environ, {
            "JEVCOMPASS_TYPED_DECISION_CACHE": "0",
        }, clear=False), \
                mock.patch.object(strategy, "DecisionsClient", side_effect=client_factory) as factory, \
                mock.patch.object(advisor, "select_advice", return_value=None) as select_advice, \
                mock.patch.object(advisor, "_judge") as generic_judge:
            output = advisor.evaluate(
                {"hook_event_name": "UserPromptSubmit", "prompt": prompt},
                trace="hook379", strategy_advice=True,
            )
        return output, constructed, factory, select_advice, generic_judge

    def test_ambiguous_hook_uses_one_fake_transport_request_and_local_catalog_text(self):
        requests = []

        def transport(_url, body, _key, _timeout):
            requests.append(json.loads(body))
            return json.dumps({
                "answers": {"strategy": {
                    "type": "choice",
                    "choice": "define_contract_then_implement",
                    "confidence": 0.91,
                }},
                "usage": {"input_tokens": 17, "output_tokens": 3},
            }).encode()

        output, constructed, factory, select, generic_judge = self._evaluate(
            AMBIGUOUS_PROMPT, transport,
        )

        self.assertEqual(len(requests), 1)
        factory.assert_called_once_with()
        self.assertEqual(len(constructed), 1)
        request = requests[0]
        self.assertEqual(set(request), {"model", "state", "questions"})
        self.assertEqual(request["state"], {
            "task_kind": "coding",
            "signals": ["behavior_change", "existing_symbol"],
        })
        serialized = json.dumps(request)
        self.assertNotIn("private-hook-strategy-sentinel", serialized)
        self.assertNotIn("normalize", serialized)
        self.assertIs(select.call_args.kwargs["local_only"], True)
        generic_judge.assert_not_called()

        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("strategy advice (remote", context)
        self.assertIn(
            "Define the expected behavior and contract, then implement against it.",
            context,
        )
        self.assertNotIn("private-hook-strategy-sentinel", context)
        self.assertNotIn("fixture-model", context)
        self.assertNotIn("synthetic-test-key", context)

    def test_transport_error_and_low_confidence_fall_back_without_blocking_hook(self):
        responses = (
            ("low-confidence", {"answers": {"strategy": {
                "type": "choice",
                "choice": "define_contract_then_implement",
                "confidence": 0.60,
            }}}),
            ("transport-error", None),
        )
        for case, response in responses:
            with self.subTest(case=case):
                requests = []

                def transport(_url, body, _key, _timeout):
                    requests.append(json.loads(body))
                    if response is None:
                        raise TimeoutError("synthetic timeout")
                    return json.dumps(response).encode()

                output, _constructed, _factory, _select, generic_judge = self._evaluate(
                    AMBIGUOUS_PROMPT, transport,
                )
                self.assertEqual(len(requests), 1)
                generic_judge.assert_not_called()
                context = output["hookSpecificOutput"]["additionalContext"]
                self.assertIn("strategy advice (local", context)
                self.assertIn("inspect_dependency_or_symbol_use", context)
                self.assertNotIn("strategy advice (remote", context)
                self.assertNotIn("private-hook-strategy-sentinel", context)

    def test_single_signal_hook_resolves_locally_without_constructing_client(self):
        def unexpected_transport(*_args):
            self.fail("local resolution must not use transport")

        output, constructed, factory, select, generic_judge = self._evaluate(
            LOCAL_PROMPT, unexpected_transport,
        )

        factory.assert_not_called()
        self.assertEqual(constructed, [])
        self.assertIs(select.call_args.kwargs["local_only"], True)
        generic_judge.assert_not_called()
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("strategy advice (local", context)
        self.assertIn("inspect_dependency_or_symbol_use", context)


if __name__ == "__main__":
    unittest.main()
