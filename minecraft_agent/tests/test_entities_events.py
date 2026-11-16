"""Entity tracking (hostility classification + detection/loss events) and
event queue throttling/dedup/capacity (§8)."""
import time
import unittest

from . import _common
from mcagent.world.entity_tracker import EntityTracker
from mcagent.world.events import EventQueue


class EntityTests(unittest.TestCase):
    def setUp(self):
        self.ev = EventQueue()
        self.tr = EntityTracker(self.ev)

    def test_hostility_classification(self):
        raw = {"entities": [
            # client flags it
            {"uuid": "a1", "type": "minecraft:zombie",
             "position": {"x": 1, "y": 64, "z": 1}, "hostile": True},
            # client does NOT flag; type table says hostile
            {"uuid": "b2", "type": "minecraft:wolf",
             "position": {"x": 2, "y": 64, "z": 0}, "hostile": None},
            # unflagged peaceful
            {"uuid": "c3", "type": "minecraft:fox",
             "position": {"x": 3, "y": 64, "z": 0}, "hostile": None},
        ]}
        self.tr.update(raw)
        hostile_uuids = {r["uuid"] for r in self.tr.hostiles()}
        self.assertEqual(hostile_uuids, {"a1", "b2"})
        peaceful = {r["uuid"] for r in self.tr.peaceful()}
        self.assertEqual(peaceful, {"c3"})

    def test_hostile_detected_and_lost_events(self):
        self.tr.update({"entities": [
            {"uuid": "z1", "type": "minecraft:zombie",
             "position": {"x": 1, "y": 64, "z": 1}, "hostile": True}]})
        events = self.ev.drain()
        self.assertEqual([e["event"] for e in events], ["hostile_detected"])
        # z1 leaves the query window -> hostile_lost exactly once
        self.tr.update({"entities": []})
        events = self.ev.drain()
        self.assertEqual([e["event"] for e in events], ["hostile_lost"])
        self.assertEqual(self.tr.hostiles(), [])

    def test_distance_computed_when_backend_omits_it(self):
        self.tr.update({"entities": [
            {"uuid": "p1", "type": "minecraft:zombie",
             "position": {"x": 10, "y": 64, "z": 0}}]},
            observed_at=100.0,
            player_pos={"x": 0, "y": 64, "z": 0})
        rec = next(iter([r for r in self.tr.snapshot()
                         if r["uuid"] == "p1"]), None)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["distance"], 10.0)


class EventTests(unittest.TestCase):
    def test_throttle_and_dedup_suppress(self):
        q = EventQueue(throttle_s=10.0)
        first = q.emit("hostile_detected", {"uuid": "z"}, dedup_key="hostile:z")
        dup = q.emit("hostile_detected", {"uuid": "z"}, dedup_key="hostile:z")
        self.assertTrue(first)
        self.assertFalse(dup)  # same dedup key within throttle window
        self.assertEqual(len(q.drain()), 1)

    def test_distinct_keys_not_suppressed(self):
        q = EventQueue(throttle_s=10.0)
        self.assertTrue(q.emit("hostile_detected", {"uuid": "a"},
                               dedup_key="hostile:a"))
        self.assertTrue(q.emit("hostile_detected", {"uuid": "b"},
                               dedup_key="hostile:b"))
        self.assertEqual(len(q.drain()), 2)

    def test_pending_queue_is_bounded(self):
        q = EventQueue(throttle_s=0.0, max_pending=4)
        for i in range(10):
            q.emit("resource_discovered", {"i": i}, dedup_key=f"r{i}")
        drained = q.drain()
        self.assertEqual(len(drained), 4)
        self.assertEqual([e["payload"]["i"] for e in drained], [6, 7, 8, 9])

    def test_events_are_notifications_not_state(self):
        # drain consumes them; world state must not be derived from them
        q = EventQueue()
        q.emit("location_reached", {"name": "home"})
        self.assertEqual(len(q.drain()), 1)
        self.assertEqual(len(q.drain()), 0)  # drained once; not replayable


if __name__ == "__main__":
    unittest.main()
