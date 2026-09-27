"""Exercise the opt-in prompt hook through the real typed selector and fake transport."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from jevcompass import advisor, strategy
from jevcompass.decisions import DecisionsClient


PROMPT = (
    "Implement a Python change to the behavior of the existing function normalize: "
    "preserve output ordering. private-transport-sentinel"
)
CATALOG_ITEMS = [
    {"id": "tool-a", "kind": "tool", "capability": "Inspect code references",
     "use_when": "existing code", "avoid_when": "trivial text", "availability": "available"},
    {"id": "tool-b", "kind": "tool", "capability": "Run local checks",
     "use_when": "validation", "avoid_when": "unclear mutation", "availability": "available"},
    {"id": "skill-a", "kind": "skill", "capability": "Review behavior contracts",
     "use_when": "behavior changes", "avoid_when": "trivial text", "availability": "available"},
    {"id": "skill-b", "kind": "skill", "capability": "Trace affected code",
     "use_when": "data flow", "avoid_when": "unrelated task", "availability": "available"},
]


class StrategyBridgeTransportTests(unittest.TestCase):
    def _evaluate(self, transport, *, cache_enabled=False, cache_dir=None):
        constructed = []

        def client_factory(**kwargs):
            client = DecisionsClient(
                api_key="test-only-key",
                model="fixture-model",
                transport=transport,
                **kwargs,
            )
            constructed.append(client)
            return client

        env = {"JEVCOMPASS_TYPED_DECISION_CACHE": "1"} if cache_enabled else {}
        if cache_enabled:
            env["JEVCOMPASS_TYPED_CACHE_DIR"] = str(cache_dir)
        with mock.patch.dict(os.environ, env, clear=False):
            if not cache_enabled:
                os.environ.pop("JEVCOMPASS_TYPED_DECISION_CACHE", None)
                os.environ.pop("JEVCOMPASS_TYPED_CACHE_DIR", None)
            with mock.patch.object(strategy, "DecisionsClient", side_effect=client_factory) as factory, \
                    mock.patch.object(advisor, "candidates", return_value=CATALOG_ITEMS), \
                    mock.patch.object(advisor, "catalog_version", return_value="fixture-catalog"), \
                    mock.patch.object(advisor, "_read_cache", return_value=None), \
                    mock.patch.object(advisor, "_write_cache"), \
                    mock.patch.object(advisor, "_metric"), \
                    mock.patch.object(advisor, "_judge") as generic_judge:
                output = advisor.evaluate(
                    {"hook_event_name": "UserPromptSubmit", "prompt": PROMPT},
                    trace="1234abcd", strategy_advice=True,
                )
        return output, constructed, factory, generic_judge

    def test_hook_uses_one_real_typed_request_without_prompt_or_generic_request(self):
        calls = []

        def transport(_url, body, _key, _timeout):
            calls.append(json.loads(body))
            return json.dumps({
                "answers": {"strategy": {
                    "type": "choice", "choice": "define_contract_then_implement", "confidence": 0.91,
                }},
                "usage": {"input_tokens": 21, "output_tokens": 4},
            }).encode()

        output, constructed, factory, generic_judge = self._evaluate(transport)
        self.assertEqual(len(calls), 1)
        factory.assert_called_once_with()
        self.assertEqual(len(constructed), 1)
        generic_judge.assert_not_called()
        request = calls[0]
        self.assertEqual(set(request), {"model", "state", "questions"})
        self.assertEqual(request["state"], {
            "task_kind": "coding", "signals": ["behavior_change", "existing_symbol"],
        })
        serialized = json.dumps(request)
        self.assertNotIn("private-transport-sentinel", serialized)
        self.assertNotIn("normalize", serialized)
        self.assertNotIn("contract_evidence", request["state"])
        self.assertNotIn("resolved_strategy", request["state"])
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("strategy advice (remote", context)
        self.assertIn("define_contract_then_implement", context)
        self.assertIn("Signals reflect the request wording", context)
        self.assertNotIn("private-transport-sentinel", context)

    def test_invalid_choice_low_confidence_and_transport_error_fall_back_locally(self):
        cases = (
            ("unknown-choice", {"type": "choice", "choice": "invented", "confidence": .99}, False),
            ("low-confidence", {"type": "choice", "choice": "define_contract_then_implement", "confidence": .60}, False),
            ("transport-error", None, True),
        )
        for name, answer, raises in cases:
            with self.subTest(case=name):
                calls = []

                def transport(_url, body, _key, _timeout):
                    calls.append(json.loads(body))
                    if raises:
                        raise TimeoutError("synthetic timeout")
                    return json.dumps({
                        "answers": {"strategy": answer},
                        "usage": {"input_tokens": 21, "output_tokens": 4},
                    }).encode()

                output, _constructed, _factory, generic_judge = self._evaluate(transport)
                self.assertEqual(len(calls), 1)
                generic_judge.assert_not_called()
                context = output["hookSpecificOutput"]["additionalContext"]
                self.assertIn("strategy advice (local", context)
                self.assertNotIn("strategy advice (remote", context)
                self.assertIn("inspect_dependency_or_symbol_use", context)
                self.assertNotIn("private-transport-sentinel", context)

    def test_second_hook_uses_validated_cache_without_transport_or_replayed_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def transport(_url, body, _key, _timeout):
                calls.append(json.loads(body))
                return json.dumps({
                    "answers": {"strategy": {
                        "type": "choice", "choice": "define_contract_then_implement", "confidence": .91,
                    }},
                    "usage": {"input_tokens": 21, "output_tokens": 4},
                }).encode()

            # Both hook invocations use the actual typed selector; only the first
            # reaches the fake HTTP transport, and the cache contains no prompt.
            outputs = []
            for _ in range(2):
                constructed = []

                def client_factory(**kwargs):
                    client = DecisionsClient(
                        api_key="test-only-key", model="fixture-model", transport=transport, **kwargs,
                    )
                    constructed.append(client)
                    return client

                with mock.patch.dict(os.environ, {
                    "JEVCOMPASS_TYPED_DECISION_CACHE": "1",
                    "JEVCOMPASS_TYPED_CACHE_DIR": directory,
                }, clear=False), \
                        mock.patch.object(strategy, "DecisionsClient", side_effect=client_factory), \
                        mock.patch.object(advisor, "candidates", return_value=CATALOG_ITEMS), \
                        mock.patch.object(advisor, "catalog_version", return_value="fixture-catalog"), \
                        mock.patch.object(advisor, "_read_cache", return_value=None), \
                        mock.patch.object(advisor, "_write_cache"), \
                        mock.patch.object(advisor, "_metric"), \
                        mock.patch.object(advisor, "_judge") as judge:
                    outputs.append(advisor.evaluate(
                        {"hook_event_name": "UserPromptSubmit", "prompt": PROMPT},
                        trace="1234abcd", strategy_advice=True,
                    ))
                    judge.assert_not_called()
            self.assertEqual(len(calls), 1)
            first = outputs[0]["hookSpecificOutput"]["additionalContext"]
            second = outputs[1]["hookSpecificOutput"]["additionalContext"]
            self.assertIn("strategy advice (remote", first)
            self.assertIn("strategy advice (cached", second)
            self.assertIn("Cached selection made no new API call", second)
            disk = "".join(path.name + path.read_text() for path in Path(directory).glob("*.json"))
            self.assertNotIn("private-transport-sentinel", disk)
            self.assertNotIn("normalize", disk)
            self.assertNotIn("test-only-key", disk)


if __name__ == "__main__":
    unittest.main()
