"""Resource scanning: exposure classification, explored-area cooldown,
deposit registry separation, and depletion re-check (§5.6)."""
import unittest

from . import _common
from mcagent.adapter import MinecraftAgentAdapter
from mcagent.enums import KnowledgeState


class ScannerTests(unittest.TestCase):
    def setUp(self):
        _common.isolate_db()
        self.a = _common.fresh_adapter()

    def _scan(self, allow_rescan=True):
        return self.a.scanner.scan("iron_ore",
                                   center={"x": 40, "y": 64, "z": 0},
                                   radius=16, allow_rescan=allow_rescan)

    def test_exposure_classification(self):
        found = self._scan()
        by_coord = {(r["coordinates"]["x"], r["coordinates"]["y"],
                    r["coordinates"]["z"]): r["exposure"] for r in found}
        self.assertEqual(by_coord.get((45, 64, 0)), "buried")
        self.assertEqual(by_coord.get((40, 64, 0)), "air_exposed")

    def test_nearest_first(self):
        found = self._scan()
        dists = [r["distance"] for r in found]
        self.assertEqual(dists, sorted(dists))

    def test_recently_scanned_area_skipped_without_reason(self):
        first = self._scan(allow_rescan=True)
        self.assertTrue(first)
        again = self._scan(allow_rescan=False)
        self.assertEqual(again, [])
        forced = self._scan(allow_rescan=True)
        self.assertEqual(len(forced), len(first))

    def test_deposit_goes_to_resource_table_not_locations(self):
        self._scan()
        deps = self.a.scanner.list_known("iron_ore")
        self.assertTrue(deps)
        self.assertEqual(deps[0]["status"], "candidate")
        # registries are separate: nothing landed in locations
        self.assertIsNone(self.a.registry.get("minecraft:iron_ore"))

    def test_depletion_is_detected(self):
        self._scan()
        dep = self.a.scanner.list_known("iron_ore")[0]
        # remove all ore from the world, then re-verify the deposit
        for k in list(self.a.backend.world):
            if "iron_ore" in self.a.backend.world[k]:
                self.a.backend.world[k] = "minecraft:dirt"
        out = self.a.scanner.validate_deposit(dep)
        self.assertFalse(out["present"])
        deps = self.a.scanner.list_known("iron_ore")
        self.assertEqual(deps[0]["status"], "depleted")

    def test_observed_block_becomes_known_to_model(self):
        found = self._scan()
        state, detail = self.a.model.block_knowledge(**{"x": 40, "y": 64, "z": 0})
        self.assertEqual(state, KnowledgeState.OBSERVED)

    def test_scan_radius_is_bounded(self):
        # the village marker at (100,·,100) must not be returned from far away
        res = self.a.iface.query_blocks({"x": 0, "y": 64, "z": 0}, 500)
        names = [b["name"] for b in res["blocks"]]
        self.assertNotIn("minecraft:planks", names)
        # and deep-inspection tool refuses radii above tier3 cap
        bad = self.a.call("inspect_area", {"x": 0, "y": 64, "z": 0, "radius": 99})
        self.assertFalse(bad["success"])
        self.assertEqual(bad["error"]["code"], "INVALID_ARGS")

    def test_structure_tier2_finds_landmarks(self):
        # move the player near the structure, then run Tier-2 structure scan
        self.a.backend.move(99, 64, 99)
        self.a.model.player_status(force=True)
        out = self.a.perception.objective_find_structure(radius=16)
        self.assertTrue(any(b["name"] == "minecraft:planks" for b in out))


if __name__ == "__main__":
    unittest.main()
