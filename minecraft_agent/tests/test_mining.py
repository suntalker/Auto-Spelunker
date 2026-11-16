"""Dual-mode mining: strategy selection, quantity verification via
inventory delta, mode persistence, cancellation (§5)."""
import unittest

from . import _common
from mcagent.adapter import MinecraftAgentAdapter


class MiningTests(unittest.TestCase):
    def setUp(self):
        _common.isolate_db()
        self.a = _common.fresh_adapter()

    def _unused(self):
        pass

    def test_exposed_deposit_is_preferred_by_auto(self):
        # exposed ore exists near player after a find pass
        r = self.a.call("find_resource", {"block_type": "iron_ore", "radius": 64})
        self.assertTrue(r["result"]["found"])
        m = self.a.call("mine", {"block_type": "iron_ore", "quantity": 3})
        self.assertTrue(m["success"])
        self.assertEqual(m["result"]["strategy"], "EXPOSED")
        self.assertTrue(m["result"]["deposit_validated"])

    def test_auto_falls_back_to_tunnel_when_unfound(self):
        m = self.a.call("mine", {"block_type": "diamond", "quantity": 2})
        self.assertTrue(m["success"])
        self.assertEqual(m["result"]["strategy"], "TUNNEL")
        self.assertFalse(m["result"]["deposit_validated"])

    def test_explicit_exposed_mode_never_creates_tunnel(self):
        m = self.a.call("mine", {"block_type": "diamond", "quantity": 2,
                                 "mode": "EXPOSED"})
        self.assertFalse(m["success"])
        self.assertEqual(m["error"]["code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(m["error"]["strategy"], "EXPOSED")

    def test_completion_is_verified_by_inventory_not_submission(self):
        m = self.a.call("mine", {"block_type": "iron_ore", "quantity": 2})
        initial = m["result"]["initial_count"]
        settled = None
        for _ in range(8):
            settled = self.a.call("get_mining_status", {})
            if settled["result"].get("task_status") == "completed":
                break
        self.assertTrue(settled["success"])
        self.assertEqual(settled["result"]["task_status"], "completed")
        self.assertGreaterEqual(settled["result"]["collected"], 2)
        # and the backend really has the items
        actual = self.a.backend.inventory.get("minecraft:iron_ore", 0)
        self.assertEqual(settled["result"]["final_count"], actual)

    def test_insufficient_yield_reports_failed(self):
        # backend reports done, but the inventory delta falls short (§5.6):
        # simulate a bot that stopped early.
        m = self.a.call("mine", {"block_type": "iron_ore", "quantity": 5})
        self.assertTrue(m["success"])
        settled = None
        for _ in range(6):
            self.a.backend.inventory["minecraft:iron_ore"] = 0
            settled = self.a.call("get_mining_status", {})
            if settled["success"] and settled["result"].get("task_status") == "failed":
                break
            if not settled["success"]:
                break
        self.assertFalse(settled["success"])
        self.assertEqual(settled.get("task_status"), "failed")
        self.assertIn("below requested", settled.get("failure_reason", ""))

    def test_cancelling_active_mining(self):
        self.a.call("mine", {"block_type": "iron_ore", "quantity": 5})
        c = self.a.call("cancel_action", {})
        self.assertTrue(c["success"])
        again = self.a.call("get_mining_status", {})
        self.assertEqual(again["result"]["status"], "idle")

    def test_mode_setting_persists_within_session(self):
        self.a.call("set_mining_mode", {"mode": "TUNNEL"})
        got = self.a.call("get_mining_mode", {})
        self.assertEqual(got["result"]["mining_mode"], "TUNNEL")
        # a NEW adapter over the same db sees the stored mode (not code default)
        from mcagent.adapter import MinecraftAgentAdapter as MA
        b = MA()
        self.assertEqual(b.call("get_mining_mode", {})["result"]["mining_mode"],
                         "TUNNEL")

    def test_invalid_mode_rejected(self):
        m = self.a.call("mine", {"block_type": "iron_ore", "quantity": 1,
                                 "mode": "SIDEWINDER"})
        self.assertFalse(m["success"])
        self.assertEqual(m["error"]["code"], "INVALID_ARGS")


if __name__ == "__main__":
    unittest.main()
