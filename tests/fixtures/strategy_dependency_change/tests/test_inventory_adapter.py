"""Acceptance checks for the v2 dependency migration and stable app boundary."""
import unittest
from unittest.mock import patch

from inventory_adapter import list_sellable
from shop_view import render_inventory
from stock_service import StockRecord, StockReport, fetch_report


class InventoryAdapterTests(unittest.TestCase):
    def test_adapts_v2_records_to_exact_legacy_rows(self):
        report = fetch_report()
        self.assertIsInstance(report, StockReport)

        rows = list_sellable()

        self.assertEqual(rows, [("A-1", 2), ("B-2", 4)])

    def test_dependency_objects_do_not_leak_through_boundary(self):
        rows = list_sellable()

        self.assertIs(type(rows), list)
        self.assertTrue(all(type(row) is tuple and len(row) == 2 for row in rows))
        self.assertTrue(all(type(code) is str and type(count) is int for code, count in rows))

    def test_existing_tuple_consumer_still_renders_expected_view(self):
        self.assertEqual(
            render_inventory(list_sellable()),
            "A-1: 2\nB-2: 4",
        )

    def test_filters_unlisted_and_zero_quantity_records(self):
        self.assertNotIn(("X-9", 8), list_sellable())
        self.assertNotIn(("C-3", 0), list_sellable())

    def test_varied_report_is_sorted_filtered_and_not_hardcoded(self):
        report = StockReport(
            records=(
                StockRecord("Z-7", 11, True),
                StockRecord("F-2", 0, True),
                StockRecord("M-5", 9, False),
                StockRecord("A-4", 5, True),
                StockRecord("N-3", -1, True),
            ),
            revision="rev-varied",
        )

        with patch("stock_service.fetch_report", return_value=report), patch(
            "inventory_adapter.fetch_report", return_value=report, create=True
        ):
            self.assertEqual(list_sellable(), [("A-4", 5), ("Z-7", 11)])

    def test_each_call_returns_a_fresh_list(self):
        report = StockReport(
            records=(StockRecord("D-4", 6, True),),
            revision="rev-fresh",
        )

        with patch("stock_service.fetch_report", return_value=report), patch(
            "inventory_adapter.fetch_report", return_value=report, create=True
        ):
            first = list_sellable()
            second = list_sellable()

        self.assertEqual(first, [("D-4", 6)])
        self.assertEqual(second, [("D-4", 6)])
        self.assertIsNot(first, second)
        first.clear()
        self.assertEqual(second, [("D-4", 6)])

    def test_no_positive_listed_stock_returns_empty_list(self):
        report = StockReport(
            records=(
                StockRecord("Z-0", 0, True),
                StockRecord("N-1", -1, True),
                StockRecord("H-8", 8, False),
            ),
            revision="rev-empty",
        )

        with patch("stock_service.fetch_report", return_value=report), patch(
            "inventory_adapter.fetch_report", return_value=report, create=True
        ):
            rows = list_sellable()

        self.assertEqual(rows, [])
        self.assertIs(type(rows), list)


if __name__ == "__main__":
    unittest.main()
