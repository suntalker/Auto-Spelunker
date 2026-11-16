"""Central movement-ownership arbiter (§9): one authority, priority takeover,
input release on handoff, and refusal while another owner holds."""
import unittest

from mcagent.adapter import MinecraftAgentAdapter
from mcagent.agent.action_arbiter import ActionArbiter
from mcagent.enums import MovementOwner


class ArbiterTests(unittest.TestCase):
    def setUp(self):
        a = MinecraftAgentAdapter()
        self.iface = a.iface
        self.ev = a.events
        self.a = a

    def test_free_request_grants_ownership(self):
        r = self.a.nav.navigate({"x": 5, "y": 64, "z": 0})
        self.assertTrue(r["ok"])
        self.assertEqual(self.a.arbiter.status()["owner"], "NAVIGATION")

    def test_second_owner_refused_while_busy(self):
        r = self.a.nav.navigate({"x": 5, "y": 64, "z": 0})
        self.assertTrue(r["ok"])
        ok = self.a.arbiter.request(MovementOwner.COMBAT, "sparring")
        self.assertFalse(ok)
        # navigation tool must report MOVEMENT_BUSY while combat holds it
        self.a.arbiter.force(MovementOwner.COMBAT, "sparring")
        r2 = self.a.call("navigate", {"destination": {"x": 9, "y": 64, "z": 0}})
        self.assertFalse(r2["success"])
        self.assertEqual(r2["error"]["code"], "MOVEMENT_BUSY")

    def test_emergency_forces_takeover_and_releases_inputs(self):
        self.a.arbiter.force(MovementOwner.NAVIGATION, "go")
        out = self.a.arbiter.force(MovementOwner.EMERGENCY, "get off the tracks")
        self.assertTrue(out["ok"])
        self.assertTrue(out["changed"])
        self.assertEqual(out["previous"], "NAVIGATION")
        self.assertTrue(out["released_inputs"])
        events = self.a.events.drain()
        self.assertTrue(any(e["event"] == "ownership_changed" for e in events))

    def test_same_owner_request_is_free(self):
        self.a.arbiter.force(MovementOwner.NAVIGATION, "go")
        self.assertTrue(self.a.arbiter.request(MovementOwner.NAVIGATION))

    def test_release_gives_up_only_for_current_owner(self):
        self.a.arbiter.force(MovementOwner.COMBAT, "fight")
        self.assertFalse(self.a.arbiter.release(MovementOwner.NAVIGATION))
        self.assertEqual(self.a.arbiter.status()["owner"], "COMBAT")
        self.assertTrue(self.a.arbiter.release(MovementOwner.COMBAT))
        self.assertIsNone(self.a.arbiter.status()["owner"])

    def test_arbiter_alone_without_adapter(self):
        from mcagent.world.events import EventQueue
        ev = ActionArbiter(self.iface, EventQueue())
        self.assertTrue(ev.request(MovementOwner.MANUAL))
        ev2 = ev.force(MovementOwner.EMERGENCY)
        self.assertTrue(ev2["changed"])


if __name__ == "__main__":
    unittest.main()
