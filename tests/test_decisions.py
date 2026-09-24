"""Offline contract tests for the OpenRouter Decisions client and advisor."""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError
import unittest
from unittest import mock

from jevcompass import advisor
from jevcompass.decisions import (
    DecisionsClient,
    DecisionsError,
    ENDPOINT,
    MODELS_ENDPOINT,
    model_available,
    model_status,
)


STATE = {
    "task_kind": "coding",
    "role": "planner",
    "domain": "python",
    "candidates": [
        {"id": "serena", "capability": "Navigate symbols", "use_when": "code references",
         "avoid_when": "trivial text", "availability": "available"},
        {"id": "exec_command", "capability": "Run local checks", "use_when": "test commands",
         "avoid_when": "unclear mutations", "availability": "available"},
    ],
}
QUESTIONS = {"tool": {"type": "choice", "criteria": {"serena": "Navigate symbols", "exec_command": "Run local checks"}}}


class DecisionsClientTests(unittest.TestCase):
    def test_fake_transport_receives_typed_request_and_parses_answers(self):
        captured = {}

        def transport(url, body, api_key, timeout):
            captured.update(url=url, payload=json.loads(body), api_key=api_key, timeout=timeout)
            return b'{"answers":{"tool":{"type":"choice","choice":"serena","confidence":0.91}}}'

        result = DecisionsClient(api_key="test-key", model="test/model", timeout=0.25,
                                 transport=transport).decide(STATE, QUESTIONS)
        self.assertEqual(captured["url"], ENDPOINT)
        self.assertEqual(captured["api_key"], "test-key")
        self.assertEqual(captured["timeout"], 0.25)
        self.assertEqual(captured["payload"], {
            "model": "test/model", "state": STATE, "questions": QUESTIONS,
        })
        self.assertEqual(result["tool"]["choice"], "serena")

    def test_missing_credentials_and_questions_fail_closed(self):
        with self.assertRaisesRegex(DecisionsError, "not-configured"):
            DecisionsClient(api_key="").decide(STATE, QUESTIONS)
        with self.assertRaisesRegex(DecisionsError, "no-questions"):
            DecisionsClient(api_key="test-key").decide(STATE, {})

    def test_http_statuses_fail_without_exposing_response_body_or_credentials(self):
        secret = "token-do-not-leak"
        for status in (400, 401, 429, 500, 503):
            def transport(*_args, status=status):
                raise HTTPError(ENDPOINT, status, "failure", {}, None)
            with self.subTest(status=status), self.assertRaises(DecisionsError) as raised:
                DecisionsClient(api_key=secret, transport=transport).decide(STATE, QUESTIONS)
            self.assertIn(f"http-{status}", str(raised.exception))
            self.assertNotIn(secret, str(raised.exception))

    def test_timeout_and_malformed_json_are_normalized(self):
        with self.assertRaisesRegex(DecisionsError, "unavailable"):
            DecisionsClient(api_key="test-key", transport=lambda *_: (_ for _ in ()).throw(TimeoutError())).decide(STATE, QUESTIONS)
        with self.assertRaisesRegex(DecisionsError, "invalid-json"):
            DecisionsClient(api_key="test-key", transport=lambda *_: b"not-json").decide(STATE, QUESTIONS)

    def test_non_object_or_missing_answers_are_rejected(self):
        for response in (b"[]", b'{"result":{}}', b'{"answers":[]}'):
            with self.subTest(response=response), self.assertRaises(DecisionsError):
                DecisionsClient(api_key="test-key", transport=lambda *_: response).decide(STATE, QUESTIONS)

    def test_request_contains_only_allowlisted_metadata(self):
        secrets = ("private-prompt-9f87", "/home/toni/private/repo", "source-code-secret",
                   "smem-summary-secret", "client-private-data")
        captured = []
        def transport(url, body, api_key, timeout):
            captured.append(body.decode())
            return b'{"answers":{"tool":{"type":"choice","choice":"serena","confidence":0.9}}}'

        safe_state = {**STATE, "candidates": [dict(STATE["candidates"][0])]}
        DecisionsClient(api_key="fake", transport=transport).decide(safe_state, QUESTIONS)
        encoded = "".join(captured)
        for secret in secrets:
            self.assertNotIn(secret, encoded)
        self.assertEqual(json.loads(encoded)["state"], safe_state)


    def test_model_status_distinguishes_listed_missing_and_unavailable_metadata(self):
        listed = json.dumps({"data": [{
            "id": "test/model", "architecture": {"output_modalities": ["text", "decisions"]},
        }]}).encode()
        no_decisions = json.dumps({"data": [{
            "id": "test/model", "architecture": {"output_modalities": ["text"]},
        }]}).encode()

        def fetch_with(payload, expected_timeout=2.0):
            def fetch(url, *, timeout):
                self.assertEqual(url, MODELS_ENDPOINT)
                self.assertEqual(timeout, expected_timeout)
                return io.BytesIO(payload)
            return fetch

        self.assertEqual(model_status("test/model", timeout=0.25, fetch=fetch_with(listed, 0.25)), "available")
        self.assertEqual(model_status("other/model", fetch=fetch_with(listed)), "missing")
        self.assertEqual(model_status("test/model", fetch=fetch_with(no_decisions)), "missing")
        unavailable = lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError())
        self.assertEqual(model_status("test/model", fetch=unavailable), "unavailable")
        self.assertEqual(model_status("test/model", fetch=fetch_with(b'{}')), "unavailable")
        self.assertEqual(model_status("test/model", fetch=fetch_with(b'{"data":"invalid"}')), "unavailable")
        self.assertTrue(model_available("test/model", fetch=fetch_with(listed)))
        self.assertFalse(model_available("test/model", fetch=unavailable))


ITEMS = [
    {"id": "serena", "kind": "tool", "capability": "Navigate symbols", "use_when": "code references", "avoid_when": "trivial text"},
    {"id": "exec_command", "kind": "tool", "capability": "Run checks", "use_when": "test commands", "avoid_when": "unclear changes"},
    {"id": "create-plan", "kind": "skill", "capability": "Plan work", "use_when": "multi-step tasks", "avoid_when": "trivia"},
    {"id": "python-packaging", "kind": "skill", "capability": "Package Python", "use_when": "Python packaging", "avoid_when": "other work"},
]


class AdvisorDecisionValidationTests(unittest.TestCase):
    def call_judge(self, answers):
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers
            return advisor._judge("coding", "planner", "python", ITEMS)

    def test_valid_choices_are_selected_and_low_confidence_is_omitted(self):
        result = self.call_judge({
            "tool": {"type": "choice", "choice": "serena", "confidence": 0.8},
            "skill": {"type": "choice", "choice": "create-plan", "confidence": 0.2},
        })
        self.assertEqual(result, ["serena"])

    def test_missing_unknown_and_malformed_choices_are_rejected(self):
        invalid = (
            {"tool": {"type": "choice", "choice": "serena", "confidence": 0.9}},
            {"tool": {"type": "choice", "choice": "invented", "confidence": 0.9},
             "skill": {"type": "choice", "choice": "create-plan", "confidence": 0.9}},
            {"tool": {"type": "free_text", "choice": "serena", "confidence": 0.9},
             "skill": {"type": "choice", "choice": "create-plan", "confidence": 0.9}},
        )
        for answers in invalid:
            with self.subTest(answers=answers):
                self.assertIsNone(self.call_judge(answers))

    def test_low_confidence_only_answers_produce_no_recommendation(self):
        result = self.call_judge({
            "tool": {"type": "choice", "choice": "serena", "confidence": 0.1},
            "skill": {"type": "choice", "choice": "create-plan", "confidence": 0.49},
        })
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
