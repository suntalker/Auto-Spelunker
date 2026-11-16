"""Layer B world model: knowledge qualification, freshness, honest absence."""
import unittest

from . import _common
from mcagent.adapter import MinecraftAgentAdapter
from mcagent.enums import KnowledgeState


class ModelTests(unittest.TestCase):
    def setUp(self):
        _common.isolate_db()
        self.a = _common.fresh_adapter()

    def test_player_status_freshness_is_observed(self):
        r = self.a.call("get_player_status", {})
        self.assertTrue(r["success"])
        self.assertEqual(r["result"]["player"]["freshness"], "observed")

    def test_stale_cache_reports_known_not_observed(self):
        # first observation is fresh
        first = self.a.model.player_status(force=True)
        self.assertEqual(first["freshness"], "observed")
        # second call within the freshness window must NOT re-query backend:
        self.a.backend.move(9, 64, 9)  # world moved without our knowledge
        cached = self.a.model.player_status(force=False)
        self.assertEqual(cached["position"], first["position"])

    def test_forced_refresh_sees_movement(self):
        first = self.a.model.player_status(force=True)
        self.a.backend.move(9, 64, 9)
        fresh = self.a.model.player_status(force=True)
        self.assertEqual(fresh["position"], {"x": 9.0, "y": 64.0, "z": 9.0})
        self.assertNotEqual(fresh["position"], first["position"])

    def test_position_never_invented(self):
        m = self.a.model
        # asking must trigger a real observation, never a fabricated (0,0,0):
        self.a.backend.move(7, 65, -3)
        self.assertEqual(m.player_position(), (7.0, 65.0, -3.0))
        # if observation is impossible, position raises instead of inventing
        def _no_snapshot():
            raise ValueError("player never observed (no player snapshot)")
        m.player_status = _no_snapshot
        m._player = None
        with self.assertRaises(ValueError):
            m.player_position()

    def test_classify_age_stamps(self):
        now = self.a.model.now()
        cfg_state = self.a.model.classify_age(now - 1.0)
        old_state = self.a.model.classify_age(now - 100000.0)
        self.assertEqual(cfg_state, KnowledgeState.OBSERVED)
        self.assertEqual(old_state, KnowledgeState.KNOWN)
        self.assertEqual(self.a.model.classify_age(None), KnowledgeState.UNKNOWN)

    def test_note_blocks_marks_knowledge(self):
        r = self.a.call("inspect_area", {"x": 40, "y": 64, "z": 0,
                                         "radius": 3})
        self.assertTrue(r["success"])
        state, _ = self.a.model.block_knowledge(40, 64, 0)
        self.assertEqual(state, KnowledgeState.OBSERVED)
        state2, _ = self.a.model.block_knowledge(1, 2, 3)
        self.assertEqual(state2, KnowledgeState.UNKNOWN)

    def test_survival_alerts(self):
        self.a.backend.player["health"] = 3.0
        self.a.model.player_status(force=True)
        self.a.model.survival_events()
        events = self.a.events.drain()
        self.assertTrue(any(e["event"] == "health_critical" for e in events))


if __name__ == "__main__":
    unittest.main()
