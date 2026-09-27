"""Real subprocess coverage for the bounded collector's optional observer."""
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pilot_cli_core as core


class EventObserverTests(unittest.TestCase):
    def collect(self, callback, *, max_tokens=None):
        events = ['not-json', json.dumps({'type': 'item.completed', 'item': {'exit_code': 1}}),
                  json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 12, 'cached_input_tokens': 5, 'output_tokens': 3, 'reasoning_output_tokens': 0, 'cache_write_input_tokens': 0}})]
        code = 'import sys; sys.stdout.write(' + repr('\n'.join(events) + '\n') + '); sys.stdout.flush()'
        process = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE)
        return core._collect_events(process, started=time.monotonic(), timeout=5,
                                    event_observer=callback, max_tokens=max_tokens)

    def test_observer_receives_valid_events_and_malformed_line_is_retained(self):
        seen = []
        lines, timestamps, failure = self.collect(seen.append)
        self.assertIsNone(failure)
        self.assertEqual(len(lines), 3)
        self.assertEqual(len(timestamps), 3)
        self.assertEqual([event['type'] for event in seen], ['item.completed', 'turn.completed'])

    def test_callback_error_retains_observed_lines_without_exception_text(self):
        def broken(event):
            raise RuntimeError('private callback detail')
        lines, timestamps, failure = self.collect(broken)
        self.assertEqual(failure, 'event_observer_error')
        self.assertEqual(len(lines), 2)
        self.assertEqual(len(timestamps), 2)
        self.assertNotIn('private callback detail', '\n'.join(lines))

    def test_observer_and_token_cap_coexist_without_double_counting_cached_input(self):
        seen = []
        lines, _, failure = self.collect(seen.append, max_tokens=14)
        self.assertEqual(failure, 'token_budget_exceeded')
        self.assertEqual(len(lines), 3)
        self.assertEqual(len(seen), 2)
        _, _, failure = self.collect(lambda event: None, max_tokens=15)
        self.assertIsNone(failure)
