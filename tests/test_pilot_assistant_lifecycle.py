from __future__ import annotations
import json
from pathlib import Path
import sys
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pilot_cli_core as core

class AssistantLifecycleTests(unittest.TestCase):
    def test_draft_events_cannot_establish_or_poison_delivery(self):
        for event_type in ("item.started", "item.updated"):
            for text in (None, "", "JevCompass instructions received"):
                event = {"type": event_type, "item": {"type": "agent_message", "text": text}}
                self.assertEqual(core.extract_event(event), (None, None, None))
        complete = {"type": "item.completed", "item": {"type": "agent_message", "text": "ack"}}
        self.assertEqual(core.extract_event(complete), ("assistant", "ack", None))

    def test_tool_before_completed_message_remains_before_delivery(self):
        events = [
            {"type": "item.started", "item": {"type": "agent_message", "text": "ack"}},
            {"type": "item.started", "item": {"type": "command_execution", "command": "python --version"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "ack"}},
        ]
        kinds = [core.extract_event(event)[0] for event in events]
        self.assertEqual(kinds, [None, "tool", "assistant"])

if __name__ == "__main__":
    unittest.main()
