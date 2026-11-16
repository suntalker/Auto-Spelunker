"""Location registry: save/get/update/delete + protected-category guards +
process-restart persistence (§6)."""
import os
import sqlite3
import subprocess
import unittest

from . import _common
from mcagent.adapter import MinecraftAgentAdapter


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.dbdir = _common.isolate_db()
        self.a = MinecraftAgentAdapter()

    def test_save_named_home(self):
        r = self.a.call("remember_location",
                        {"name": "home", "x": 0, "y": 64, "z": 0,
                         "category": "home"})
        self.assertTrue(r["success"])
        rec = r["result"]["record"]
        self.assertTrue(rec["protected"])  # home is a protected category
        self.assertEqual(rec["category"], "home")

    def test_duplicate_save_refused(self):
        self.a.call("remember_location", {"name": "home", "x": 0, "y": 64, "z": 0,
                                          "category": "home"})
        r = self.a.call("remember_location", {"name": "home", "x": 9, "y": 9, "z": 9})
        self.assertFalse(r["success"])
        self.assertEqual(r["error"]["code"], "DUPLICATE_REQUEST")

    def test_update_requires_overwrite_for_important(self):
        self.a.call("remember_location", {"name": "home", "x": 0, "y": 64, "z": 0,
                                          "category": "home"})
        bad = self.a.call("update_location", {"name": "home", "x": 5})
        self.assertFalse(bad["success"])
        good = self.a.call("update_location", {"name": "home", "x": 5,
                                               "overwrite": True})
        self.assertTrue(good["success"])
        got = self.a.call("get_location", {"name": "home"})
        self.assertEqual(got["result"]["record"]["x"], 5)

    def test_forgetting_important_needs_force(self):
        self.a.call("remember_location", {"name": "den", "x": 1, "y": 64, "z": 1,
                                          "category": "base"})
        soft = self.a.call("forget_location", {"name": "den"})
        self.assertFalse(soft["success"])
        hard = self.a.call("forget_location", {"name": "den", "force": True})
        self.assertTrue(hard["success"])
        self.assertIsNone(self.registry.get("den"))

    def test_search_and_list(self):
        for name, cat in (("north_mine", "mine"), ("home", "home")):
            self.a.call("remember_location", {"name": name, "x": 3, "y": 64, "z": 3,
                                              "category": cat})
        listed = self.a.call("list_locations", {"category": "mine"})
        names = [r["name"] for r in listed["result"]["locations"]]
        self.assertEqual(names, ["north_mine"])
        s = self.a.call("search_world_memory", {"query": "mine"})
        self.assertTrue(any(d.get("name") == "north_mine"
                            for d in s["result"]["locations"]))

    # ---- registry-level unit checks -----------------------------------------
    @property
    def registry(self):
        return self.a.registry

    def test_dimension_validation(self):
        r = self.registry.save("shadow_keep", 0, 0, 0, "mystic realm")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "INVALID_DIMENSION")

    def test_non_integer_coords_refused(self):
        r = self.registry.save("bad", 1.5, 0, 0, "minecraft:overworld")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "INVALID_ARGS")

    def test_persistence_survives_process_restart(self):
        self.a.call("remember_location", {"name": "home", "x": 0, "y": 64, "z": 0,
                                          "category": "home"})
        root = os.path.dirname(os.path.dirname(os.path.abspath(_common.__file__)))
        code = (
            "import sys; sys.path.insert(0, %r);"
            "from mcagent.config import configure; configure(db_path=%r);"
            "import sqlite3;"
            "conn = sqlite3.connect(%r);"
            "print([r[0] for r in conn.execute('SELECT name FROM locations')])" %
            (root, self.dbdir + "/world.db", self.dbdir + "/world.db"))
        out = subprocess.run([sys_py(), "-c", code], capture_output=True,
                             text=True, timeout=30)
        names = eval(out.stdout.strip())
        self.assertIn("home", names)


def sys_py():
    import sys
    return sys.executable


if __name__ == "__main__":
    unittest.main()
