"""Config surface: defaults, immutable overrides, reference metadata."""
import unittest

from mcagent.config import current_config, configure, reset, BIOMES_REFERENCE
from mcagent.enums import KnowledgeState, NavState, MiningMode, MovementOwner, TaskState


class ConfigTests(unittest.TestCase):
    def test_defaults_match_spec_tiers(self):
        cfg = current_config()
        self.assertLessEqual(cfg.tier1_radius, cfg.tier2_radius)
        self.assertLessEqual(cfg.tier3_max_radius, cfg.tier1_radius)
        self.assertEqual(cfg.baritone_goto_template, "#goto {x} {y} {z}")
        # Baritone 1.15.0 #mine takes only a block (verified) — no quantity
        self.assertEqual(cfg.baritone_mine_template, "#mine {block}")

    def test_override_does_not_mutate_class_default(self):
        before = current_config().tier1_radius
        configure(tier1_radius=99)
        self.assertEqual(current_config().tier1_radius, 99)
        self.assertNotEqual(before, current_config().tier1_radius)
        # reset back so other test modules see defaults
        configure(tier1_radius=before)

    def test_reset_is_throwable_view(self):
        r = reset(nav_timeout_s=1.0)
        self.assertEqual(r.nav_timeout_s, 1.0)
        self.assertNotEqual(current_config().nav_timeout_s, 1.0)

    def test_biomes_reference_is_readonly_set(self):
        self.assertIsInstance(BIOMES_REFERENCE, frozenset)
        with self.assertRaises(AttributeError):
            BIOMES_REFERENCE.add("anything")  # frozenset has no mutation API

    def test_enums_present(self):
        self.assertEqual({k.name for k in KnowledgeState},
                         {"OBSERVED", "KNOWN", "INFERRED", "UNKNOWN"})
        self.assertIn(MovementOwner.EMERGENCY, list(MovementOwner))
        self.assertIn(TaskState.PAUSED, list(TaskState))


if __name__ == "__main__":
    unittest.main()
