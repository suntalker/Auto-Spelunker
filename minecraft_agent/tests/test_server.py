"""JSON-lines bridge server: id echo, tool dispatch, control lines, bad input."""
import io
import json
import unittest

from mcagent.bridge.server import handle_line, main
from mcagent.adapter import MinecraftAgentAdapter


class ServerTests(unittest.TestCase):
    def test_id_echo_and_result(self):
        a = MinecraftAgentAdapter()
        line = json.dumps({"id": "r1", "tool": "get_status", "args": {}})
        out = json.loads(handle_line(a, line))
        self.assertEqual(out["id"], "r1")
        self.assertTrue(out["ok"])
        self.assertIn("player", out["result"])

    def test_error_lines_carry_error(self):
        a = MinecraftAgentAdapter()
        out = json.loads(handle_line(a, json.dumps(
            {"id": "r2", "tool": "navigate", "args": {"destination": "nope"}})))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "LOCATION_NOT_FOUND")

    def test_control_describe_and_list_tools(self):
        a = MinecraftAgentAdapter()
        d = json.loads(handle_line(a, json.dumps({"id": "c1", "cmd": "describe"})))
        self.assertEqual(d["result"]["backend"], "simulation")
        t = json.loads(handle_line(a, json.dumps({"id": "c2", "cmd": "list_tools"})))
        self.assertIn("find_resource", t["result"]["tools"])

    def test_malformed_line_is_reported_not_fatal(self):
        a = MinecraftAgentAdapter()
        out = json.loads(handle_line(a, "{not json"))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "INVALID_JSON")
        # server survives and keeps serving afterwards
        ok = json.loads(handle_line(a, json.dumps({"id": "r3", "cmd": "describe"})))
        self.assertTrue(ok["ok"])

    def test_long_task_never_blocks_the_stream(self):
        # submit is immediate; completion observed via later poll lines
        a = MinecraftAgentAdapter()
        a.backend.world[(40, 64, 0)] = "iron_ore"
        a.backend.inventory["minecraft:iron_ore"] = 0
        submit = json.loads(handle_line(a, json.dumps(
            {"id": "m1", "tool": "mine", "args": {"block_type": "iron_ore",
                                                  "quantity": 1}})))
        self.assertTrue(submit["ok"])
        self.assertEqual(submit["result"]["status"], "in_progress")
        poll = json.loads(handle_line(a, json.dumps(
            {"id": "m2", "tool": "get_mining_status", "args": {}})))
        self.assertEqual(poll["result"]["status"], "in_progress")

    def test_stream_loop(self):
        lines = io.StringIO(json.dumps({"id": "1", "cmd": "describe"}) + "\n"
                            + json.dumps({"id": "2", "cmd": "shutdown"}) + "\n")
        out = io.StringIO()
        main(stream_in=lines, stream_out=out)
        got = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual([g["id"] for g in got], ["1", "2"])


if __name__ == "__main__":
    unittest.main()
