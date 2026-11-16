"""JSON-lines bridge server: persistent process the Pi extension talks to.

Protocol (one JSON object per line, ids echoed):

    request  {"id": "r1", "tool": "navigate", "args": {...}}
    response {"id": "r1", "ok": true, "result": {...}}   (or "error")

Also understands control lines:
    {"id": "x", "cmd": "describe"}          -> adapter backend describe
    {"id": "x", "cmd": "list_tools"}        -> tool names

Long-running Minecraft work is never done by waiting here: submit + poll
tasks return immediately; the caller polls on later lines (§do not block).
"""
from __future__ import annotations
import json
import sys

from ..adapter import MinecraftAgentAdapter


def handle_line(adapter: MinecraftAgentAdapter, line: str) -> str:
    try:
        req = json.loads(line)
    except json.JSONDecodeError as exc:
        return json.dumps({"id": None, "ok": False,
                           "error": {"code": "INVALID_JSON", "message": str(exc)}})
    rid = req.get("id")
    cmd = req.get("cmd")
    if cmd == "describe":
        try:
            return json.dumps({"id": rid, "ok": True,
                               "result": adapter.iface.describe()})
        except Exception as exc:
            return json.dumps({"id": rid, "ok": False,
                               "error": {"code": type(exc).__name__,
                                         "message": str(exc)}})
    if cmd == "list_tools":
        return json.dumps({"id": rid, "ok": True,
                           "result": {"tools": adapter.list_tools()}})
    if cmd == "shutdown":
        adapter.shutdown = True
        return json.dumps({"id": rid, "ok": True, "result": {"shutdown": True}})
    tool = req.get("tool")
    args = req.get("args") or {}
    envelope = adapter.call(tool, args)
    ok = envelope["success"]
    out = {"id": rid, "ok": ok}
    if ok:
        out["result"] = envelope["result"]
    else:
        out["error"] = envelope["error"]
    return json.dumps(out, default=str)


def main(mode: str = "simulation", stream_in=sys.stdin, stream_out=sys.stdout):
    adapter = MinecraftAgentAdapter(mode=mode)
    adapter.shutdown = False
    for line in stream_in:
        line = line.strip()
        if not line:
            continue
        stream_out.write(handle_line(adapter, line) + "\n")
        stream_out.flush()
        if getattr(adapter, "shutdown", False):
            break


if __name__ == "__main__":
    import sys as _sys

    mode = _sys.argv[1] if len(_sys.argv) > 1 else "simulation"
    main(mode)
