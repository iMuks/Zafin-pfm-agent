"""The component contract and the tool registry — the two guard rails around the model."""

from __future__ import annotations

import unittest

from penny.application.components.contract import (
    MODEL_COMPONENTS,
    SERVER_COMPONENTS,
    is_component_object,
    validate,
)
from penny.application.components.presenters import smart_loading, transaction_list
from penny.application.tools.registry import ToolRegistry, ToolSpec


class ComponentContract(unittest.TestCase):
    def assert_degrades(self, obj):
        result = validate(obj)
        self.assertEqual(result["component"], "unsupported")
        self.assertIn("reason", result)
        return result

    def test_valid_table_passes(self):
        obj = {"component": "table", "headers": ["a", "b"], "data": [["1", "2"]]}
        self.assertEqual(validate(obj)["component"], "table")

    def test_table_rows_must_match_header_count(self):
        self.assert_degrades({"component": "table", "headers": ["a", "b"], "data": [["1"]]})

    def test_pie_slice_is_one_percentage(self):
        base = {"component": "pie-chart", "valueTitle": "x", "labels": ["s"]}
        self.assertEqual(
            validate({**base, "data": [{"label": "a", "values": [34.2]}]})["component"], "pie-chart"
        )
        self.assert_degrades({**base, "data": [{"label": "a", "values": [140]}]})
        self.assert_degrades({**base, "data": [{"label": "a", "values": [10, 20]}]})

    def test_bar_chart_allows_multiple_series(self):
        obj = {
            "component": "bar-chart",
            "valueTitle": "x",
            "labels": ["s1", "s2"],
            "data": [{"label": "Jul", "values": [1, 2]}],
        }
        self.assertEqual(validate(obj)["component"], "bar-chart")

    def test_chart_values_must_be_numbers(self):
        self.assert_degrades(
            {
                "component": "line-chart",
                "valueTitle": "x",
                "labels": ["s"],
                "data": [{"label": "a", "values": ["12"]}],
            }
        )

    def test_booleans_are_not_numbers(self):
        self.assert_degrades(
            {
                "component": "bar-chart",
                "valueTitle": "x",
                "labels": ["s"],
                "data": [{"label": "a", "values": [True]}],
            }
        )

    def test_missing_required_field(self):
        self.assert_degrades({"component": "chat-response"})

    def test_unknown_component_degrades_rather_than_raising(self):
        self.assert_degrades({"component": "sankey-diagram", "flows": []})

    def test_empty_collections_rejected(self):
        self.assert_degrades({"component": "ordered-list", "items": []})
        self.assert_degrades({"component": "suggested-user-intents", "intents": []})

    def test_thinking_trace_is_not_a_component(self):
        self.assertFalse(is_component_object({"thinking": "..."}))
        self.assertFalse(is_component_object(["chat-response"]))
        self.assertTrue(is_component_object({"component": "feedback"}))

    def test_model_and_server_components_are_disjoint(self):
        """The model must not be able to fabricate a transaction-list."""
        self.assertEqual(MODEL_COMPONENTS & SERVER_COMPONENTS, set())
        self.assertIn("transaction-list", SERVER_COMPONENTS)


class Presenters(unittest.TestCase):
    def test_transaction_list_from_find_transactions(self):
        card = transaction_list(
            "find_transactions",
            {
                "transactions": [
                    {
                        "merchant": "Dunkin'",
                        "amount": 9.1,
                        "date": "2026-07-01",
                        "category": "Dining",
                        "location": "Boston, MA",
                        "logo_domain": "dunkindonuts.com",
                    }
                ]
            },
        )
        self.assertEqual(card["transactions"][0]["subtitle"], "2026-07-01 · Dining · Boston, MA")

    def test_subscription_cards_note_variable_amounts(self):
        card = transaction_list(
            "list_subscriptions",
            {
                "subscriptions": [
                    {
                        "merchant": "Netflix",
                        "typical_amount": 19.99,
                        "cadence": "monthly",
                        "charge_count": 3,
                        "amount_varies": True,
                        "logo_domain": "netflix.com",
                    }
                ]
            },
        )
        self.assertIn("amount varies", card["transactions"][0]["subtitle"])

    def test_malformed_tool_output_yields_no_cards_rather_than_raising(self):
        self.assertIsNone(transaction_list("find_transactions", {"transactions": [{"oops": 1}]}))
        self.assertIsNone(transaction_list("find_transactions", "not a dict"))
        self.assertIsNone(transaction_list("get_data_coverage", {"months": []}))

    def test_cards_are_capped(self):
        rows = [
            {"merchant": f"M{i}", "amount": 1.0, "date": "2026-07-01", "category": "Dining"}
            for i in range(20)
        ]
        card = transaction_list("find_transactions", {"transactions": rows})
        self.assertEqual(len(card["transactions"]), 8)
        self.assertEqual(card["total_count"], 20)

    def test_loading_labels_are_human(self):
        self.assertEqual(
            smart_loading("list_subscriptions")["body"], "Scanning for recurring charges"
        )
        self.assertEqual(smart_loading("unknown_tool")["body"], "Running unknown_tool")


class Registry(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry(
            [
                ToolSpec(
                    name="demo",
                    description="d",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "month": {"type": "string"},
                            "category": {"type": "string", "enum": ["Dining", "Fuel"]},
                            "limit": {"type": "integer"},
                        },
                        "required": ["month"],
                    },
                    handler=lambda month, category=None, limit=3: {"ok": [month, category, limit]},
                )
            ]
        )

    def test_happy_path(self):
        self.assertEqual(
            self.registry.run("demo", {"month": "2026-07"})["ok"], ["2026-07", None, 3]
        )

    def test_unknown_tool_lists_the_real_ones(self):
        error = self.registry.run("nope", {})["error"]
        self.assertIn("Unknown tool", error)
        self.assertIn("demo", error)

    def test_missing_required_argument(self):
        self.assertIn("requires", self.registry.run("demo", {})["error"])

    def test_unexpected_argument_is_named(self):
        self.assertIn(
            "does not accept", self.registry.run("demo", {"month": "2026-07", "zzz": 1})["error"]
        )

    def test_enum_violation_lists_valid_values(self):
        error = self.registry.run("demo", {"month": "2026-07", "category": "Crypto"})["error"]
        self.assertIn("Dining", error)

    def test_type_violation(self):
        self.assertIn(
            "must be a integer",
            self.registry.run("demo", {"month": "2026-07", "limit": "x"})["error"],
        )

    def test_handler_exception_becomes_an_error_result_not_a_raise(self):
        """A tool failure must not end the turn — the model can try another tool."""

        def boom(**_):
            raise RuntimeError("database on fire")

        registry = ToolRegistry([ToolSpec("boom", "d", {"type": "object", "properties": {}}, boom)])
        self.assertIn("database on fire", registry.run("boom", {})["error"])

    def test_duplicate_tool_names_are_rejected_at_construction(self):
        spec = ToolSpec("dupe", "d", {"type": "object", "properties": {}}, lambda: {})
        with self.assertRaises(ValueError):
            ToolRegistry([spec, spec])


if __name__ == "__main__":
    unittest.main()
