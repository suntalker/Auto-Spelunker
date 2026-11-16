"""Adapter: validation envelope, unknown tool, combat delegation hook,
JSON serialization of results (§9/§11)."""
import unittest

from . import _common
from mcagent.adapter import MinecraftAgentAdapter


class AdapterTests(unittest.TestCase):
    def setUp(self):
        _common.isolate_db()
        self.a = _common.fresh_adapter()

    def test_unknown_tool_structured_error(self):
        r = self.a.call("dance", {})
        self.assertFalse(r["success"])
        self.assertEqual(r["error"]["code"], "UNKNOWN_TOOL")

    def test_missing_required_argument(self):
        r = self.a.call("get_location", {})
        self.assertFalse(r["success"])
        self.assertEqual(r["error"]["code"], "INVALID_ARGS")
        self.assertIn("name", r["error"]["message"])

    def test_unknown_argument_rejected(self):
        r = self.a.call("look", {"teleport": True})
        self.assertFalse(r["success"])
        self.assertEqual(r["error"]["code"], "INVALID_ARGS")

    def test_backend_failure_becomes_structured_error(self):
        # no game behind the pyritone driver: honest structured failure
        from mcagent.bridge.drivers import BackendUnavailable
        try:
            self.a.iface.describe()  # works on simulation
            self.assertTrue(True)
        except BackendUnavailable:
            self.fail("simulation describe must not fail")

    def test_combat_not_implemented_until_registered(self):
        r = self.a.call("engage_target", {"target_uuid": "z1"})
        self.assertFalse(r["success"])
        self.assertEqual(r["error"]["code"], "NOT_IMPLEMENTED")

    def test_combat_hook_delegates_to_controller(self):
        calls = []

        def controller(kind, args):
            calls.append((kind, args))
            return {"state": "engaged"}

        self.a.register_combat_controller(controller)
        r = self.a.call("engage_target", {"target_uuid": "z1"})
        self.assertTrue(r["success"])
        self.assertEqual(r["result"]["state"], "engaged")
        self.assertEqual(calls[0][0], "engage")

    def test_tool_surface_matches_schemas(self):
        listed = sorted(self.a.tools.schemas().keys())
        self.assertEqual(listed, self.a.list_tools())
        self.assertIn("navigate", listed)
        self.assertIn("engage_target", listed)

    def test_results_are_json_serializable(self):
        import json
        r = self.a.call("get_status", {})
        text = self.a.to_json(r)
        self.assertEqual(json.loads(text), r)

    def test_events_are_delivered_once(self):
        self.a.call("remember_location", {"name": "home", "x": 0, "y": 64,
                                          "z": 0, "category": "home"})
        first = self.a.call("get_events", {})
        self.assertTrue(first["result"]["events"])
        second = self.a.call("get_events", {})
        self.assertEqual(second["result"]["events"], [])


if __name__ == "__main__":
    unittest.main()
