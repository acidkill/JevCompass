"""Small starter tests; the independent supervisor gate is broader."""
from __future__ import annotations

import unittest

from ledger_adapter import load_document
from ledger_archive import Client


class LedgerAdapterStarterTests(unittest.TestCase):
    def test_legacy_positional_call_maps_to_v2_and_returns_copy(self):
        original = {"account": "A-9"}
        client = Client(original)

        result = load_document(client, "doc-7", True, {"region": "north"})

        self.assertEqual(result, original)
        self.assertIsNot(result, original)
        self.assertEqual(
            client.last_arguments,
            {
                "document_key": "doc-7",
                "allow_stale": True,
                "cache_hint": {"region": "north"},
            },
        )

    def test_none_payload_remains_none_and_entry_is_closed(self):
        client = Client(None)

        self.assertIsNone(load_document(client, "doc-8"))
        self.assertTrue(client.last_entry.closed)
        self.assertEqual(client.last_entry.close_count, 1)

    def test_empty_mapping_is_a_valid_document(self):
        self.assertEqual(load_document(Client({}), ""), {})
