"""Navigation: submit, monitor (progress/stuck/blocked), cancel, and
interruption-with-revalidation (§7)."""
import unittest

from . import _common
from mcagent.adapter import MinecraftAgentAdapter


def poll_until_settled(tool, max_polls=12):
    for _ in range(max_polls):
        r = tool("get_navigation_status", {})
        if r["success"] and r["result"]["state"] != "NAVIGATING":
            return r
    raise AssertionError("navigation never settled")


class NavTests(unittest.TestCase):
    def setUp(self):
        _common.isolate_db()
        self.a = _common.fresh_adapter(seed=False)

    def test_navigate_to_saved_location(self):
        self.a.call("remember_location", {"name": "forge", "x": 40, "y": 64,
                                          "z": 0, "category": "mine"})
        r = self.a.call("navigate", {"destination": "forge"})
        self.assertTrue(r["success"])
        self.assertEqual(r["result"]["status"], "in_progress")
        self.assertEqual(r["result"]["method"], "native")
        final = poll_until_settled(lambda name, args: self.a.call(name, args))
        self.assertEqual(final["result"]["state"], "COMPLETED")
        # the bot physically arrived: re-observe the player honestly
        obs = self.a.call("get_player_status", {})
        self.assertAlmostEqual(obs["result"]["player"]["position"]["x"], 40.0)
        events = self.a.events.drain()
        self.assertTrue(any(e["event"] == "location_reached" for e in events))

    def test_unknown_destination_named_is_error(self):
        r = self.a.call("navigate", {"destination": "nowhere"})
        self.assertFalse(r["success"])
        self.assertEqual(r["error"]["code"], "LOCATION_NOT_FOUND")

    def test_duplicate_active_navigation_not_resubmitted(self):
        r1 = self.a.call("navigate", {"destination": {"x": 10, "y": 64, "z": 0}})
        self.assertTrue(r1["success"])
        r2 = self.a.call("navigate", {"destination": {"x": 10, "y": 64, "z": 0}})
        self.assertTrue(r2["success"])
        self.assertTrue(r2["result"].get("duplicate"))
        self.assertEqual(r2["result"]["task_id"], r1["result"]["task_id"])

    def test_cross_dimension_refused(self):
        self.a.call("get_player_status", {})  # player is in overworld
        r = self.a.call("navigate", {"destination": {"x": 5, "y": 5, "z": 5,
                                                    "dimension": "minecraft:nether"}})
        self.assertFalse(r["success"])
        self.assertEqual(r["error"]["code"], "INVALID_DIMENSION")

    def test_backend_reports_stuck_task(self):
        r = self.a.call("navigate", {"destination": {"x": 30, "y": 64, "z": 0}})
        task_id = r["result"]["task_id"]
        self.a.backend.set_blocked(task_id)
        settled = None
        for _ in range(6):
            settled = self.a.call("get_navigation_status", {})
            if settled["success"] and settled["result"]["state"] != "NAVIGATING":
                break
        self.assertEqual(settled["result"]["state"], "FAILED")
        self.assertEqual(settled["result"]["reason"], "blocked")
        events = self.a.events.drain()
        self.assertTrue(any(e["event"] == "navigation_failed" for e in events))

    def test_cancel_stops_task(self):
        r = self.a.call("navigate", {"destination": {"x": 20, "y": 64, "z": 0}})
        task_id = r["result"]["task_id"]
        c = self.a.call("cancel_action", {})
        self.assertTrue(c["success"])
        status = self.a.call("get_navigation_status", {})
        self.assertEqual(status["result"]["state"], "IDLE")

    def test_interruption_never_resumes_blindly(self):
        # navigation owns movement; combat takes over -> pause + cancel
        r = self.a.call("navigate", {"destination": {"x": 12, "y": 64, "z": 0}})
        self.assertTrue(r["success"])
        taken = self.a.arbiter.force("COMBAT", "ambush")
        self.assertTrue(taken["ok"])
        interrupted = self.a.nav.interrupted_by("ambush", "COMBAT")
        self.assertTrue(interrupted["ok"])
        self.assertIsNone(self.a.nav._active)
        # resuming without releasing combat must be refused (still busy)
        blocked = self.a.nav.resume_if_valid()
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["code"], "MOVEMENT_BUSY")
        # once combat releases, resume re-submits a fresh goal
        self.a.arbiter.release("COMBAT")
        resumed = self.a.nav.resume_if_valid()
        self.assertTrue(resumed["ok"])
        self.assertEqual(resumed["status"], "in_progress")


if __name__ == "__main__":
    unittest.main()
