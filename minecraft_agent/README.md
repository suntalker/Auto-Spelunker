# mcagent — World Awareness & Navigation subsystem for an autonomous Minecraft agent

A three-layer system that gives an LLM agent (running in Pi) high-level,
validated tools over Minecraft — seeing, remembering, navigating (via Baritone)
and bounded mining — without ever handing raw game state directly to the model.

```
Layer A  bridge/      raw access to the world (simulation | Minescript | Pyritone)
Layer B  world/       structured, qualified world model + SQLite persistence
Layer C  adapter/agent tools validated + exposed to the LLM (Pi registers them)
```

The agent's core principle: **never present memory as if it were current sight.**
Every claim carries a knowledge state — `OBSERVED` (seen just now), `KNOWN`
(recorded earlier, still valid as a record), `INFERRED` (derived), `UNKNOWN`.

## Layout

```
minecraft_agent/
├── mcagent/
│   ├── config.py            # frozen configuration + overrides
│   ├── enums.py             # shared vocabularies (KnowledgeState, TaskState, …)
│   ├── adapter.py           # composition: wires every subsystem, validates calls
│   ├── bridge/
│   │   ├── drivers.py       # SimulationBackend (test world) + Minescript/Pyritone drivers
│   │   └── server.py        # JSON-lines bridge server (persistent process)
│   ├── world/
│   │   ├── interface.py     # Layer-A façade with operation vocabulary + capability checks
│   │   ├── world_model.py   # knowledge qualification, freshness, event feed, survival alerts
│   │   ├── perception.py    # tiered perception: cheap passive / objective-driven / deep bounded
│   │   ├── scanner.py       # bounded resource scans + deposit records
│   │   ├── location_registry.py  # place memory (protected: base, home)
│   │   ├── entity_tracker.py# hostility detection from changing entity queries
│   │   ├── events.py        # deduplicated event queue (each alert delivered once)
│   │   └── database.py      # SQLite persistence (locations, deposits, nav history)
│   ├── navigation/
│   │   ├── manager.py       # navigation tasks: resolve, submit, poll, interrupt/recover
│   │   ├── baritone.py      # command bridge: capability detect, chat fallback, poll
│   │   ├── tasks.py         # task lifecycle + logging
│   │   └── mining.py        # strategy (EXPOSED/TUNNEL/AUTO), yield verification
│   └── agent/
│       ├── tools.py         # tool schemas + handlers (the Layer-C surface)
│       └── action_arbiter.py# single owner of movement (navigation↔combat↔emergency)
├── tests/                   # 71 stdlib-unittest tests against the simulation backend
├── pi-extension/
│   ├── minecraft-adapter.ts # Pi extension: registers the tools for the LLM
│   └── _bridge_probe.ts     # no-LLM probe of the bridge protocol
└── data/                    # world.db lives here (created on first use)
```

## Requirements (verified on this machine)

- Python 3.14 (system). Code targets modern typing; ≥3.10 should also work.
- `minescript` (1.1.9) and `pyritone` (0.2.0) Python packages: **importable** here.
- `websockets` (17.4): installed (pyritone transport).
- Pi (`@earendil-works/pi-coding-agent`): installed; extension is plain
  TypeScript using `pi.registerTool`.
- In-game side (NOT yet present): the Minescript and/or Pyritone bridge mods must
  be installed into the PrismLauncher instance (`instances/1.21.11/`). Baritone and
  MeteorClient mods are already present in that instance.

Until the game-side bridge mods exist, everything runs against
`SimulationBackend`, which behaves like a real backend (player, blocks, entities,
Baritone-style task polling) so all logic is testable now and the same code paths
run against a real client later.

## Running

All commands below are from this project root (`C:\Users\esmon\minecraft_agent`).

Bridge server (JSON lines on stdin/stdout — used by the Pi extension):

```
python -m mcagent.bridge.server simulation   # or: pyritone | minescript
```

Protocol per line: `{"id": …, "tool": name, "args": {...}}` →
`{"id": …, "ok": true|false, "result": … | "error": {...}}`.
Control lines: `{"cmd": "describe"}` (capabilities), `{"cmd": "shutdown"}`.
Long work never blocks: `navigate`/`mine` submit and return a `task_id`; the
agent polls `get_navigation_status`/`get_mining_status`.

Library use (no process):

```python
from mcagent.adapter import MinecraftAgentAdapter
adapter = MinecraftAgentAdapter()            # simulation backend by default
adapter.call("navigate", {"x": -120, "y": 64, "z": 8})
```

Tests:

```
python -m unittest discover -s tests -t .    # 71 tests, all green
```

Bridge probe from the TypeScript side (no LLM involved):

```
node pi-extension/_bridge_probe.ts           # needs the typebox junction in node_modules/
```

Using the tools through Pi: place `pi-extension/minecraft-adapter.ts` in
`~/.pi/agent/extensions/` (or the project's `.pi/extensions/`), then
`pi -e` is unnecessary — tools auto-register. Set `MCAGENT_ROOT`/`MCAGENT_MODE`
if the project lives elsewhere or you want a live client.

## Tools (what the LLM may call)

| Group | Tool | Purpose |
| --- | --- | --- |
| See | `look` | cheap live snapshot of the player's surroundings |
| | `get_status` | full status: player, tasks, memory counts |
| | `get_player_status` | position/health/hunger/dimension + freshness of each |
| | `inspect_area` | bounded deep scan (r ≤ 32), knowledge state per block |
| | `find_resource` | targeted scan; honest outcomes: found / not_found_in_searched_volume / inconclusive |
| | `get_nearby_entities` | nearby entities with hostile/peaceful/uncertain tags |
| Act | `navigate` | Baritone `#goto` (saved name or coordinates) → task_id |
| | `mine` | Baritone `#mine` with EXPOSED/TUNNEL/AUTO strategy → task_id |
| | `get_navigation_status` | poll: in_progress / arrived / blocked / interrupted… |
| | `get_mining_status` | poll with inventory-delta verification |
| | `cancel_action` | cancel a task by id |
| | `get/set_mining_mode` | session mining policy |
| Memory | `remember_location`, `get_location`, `update_location`, `list_locations`, `forget_location` | place memory; `base`/`home` are protected |
| | `search_world_memory` | locations + recorded deposits (everything returns as KNOWN) |
| Signals | `get_events` | pending alerts (hostile seen, health critical, tasks finished) — each delivered once |

Combat tools are deliberately **not** exposed: combat is a separate subsystem
(`adapter.register_combat_controller()` is the registration hook for it).

## Example calls and structured responses

```python
adapter.call("mine", {"block_type": "minecraft:iron_ore", "quantity": 2})
```

```json
{"success": true, "result": {"task_id": "mine_001", "status": "in_progress",
  "block": "minecraft:iron_ore", "requested_quantity": 2,
  "strategy": "EXPOSED", "initial_count": 0,
  "deposit_validated": true, "method": "native"}}
```

Polling until verified (a later poll):

```json
{"success": true, "result": {"task_id": "mine_001", "collected": 2,
  "task_status": "completed", "initial_count": 0, "final_count": 2}}
```

Failures are structured, never exceptions for the model:

```json
{"success": false,
 "error": {"code": "BACKEND_UNAVAILABLE",
           "message": "the game bridge said no: ..."}}
```

Navigation interruption reports ownership honestly: an outside controller (e.g.
combat) pausing navigation yields `"blocked"`, never silently "arrived" — and
`resume` re-validates movement ownership before re-submitting a goal.

## Design decisions (why it is shaped this way)

1. **Knowledge qualification is mandatory** — asking about anything unobserved
   returns KNOWN/INFERRED/UNKNOWN, never a fabricated current value; asking about
   player position when the backend is down raises a structured error rather than
   inventing (0,0,0).
2. **Bounded scans only** — passive tier ≈ every action (radius 48), objective
   tier only when there is a purpose (≤96), deep inspection ≤32 per request;
   no indiscriminate 12-chunk scans.
3. **Baritone owns movement.** This layer only submits goals, cancels, and
   watches outcomes; it never moves the player itself.
4. **Mining completion = inventory delta** — Baritone stopping is not proof the
   material was collected; completion reports compare `inventory_count` before/after.
5. **`#mine` has no quantity argument** (verified against Baritone 1.15.0
   command set): quantity limits are enforced at this orchestration layer
   (chunked goals + verification).
6. **One movement owner** — `action_arbiter` is the single authority;
   combat/emergency must explicitly take over, and navigation resumes only after
   re-checking validity (stale goal re-validated against current memory).
7. **Protected memory** — `base`/`home` refuse silent overwrite/delete;
   changing them requires `overwrite`/`force` flags.
8. **Events are notifications, not state** — each alert is delivered once;
   they never rewrite the world model by themselves.
9. **SQLite for persistence, JSON only at the boundary** — locations, deposits
   and nav history survive restarts in `data/world.db`.

## Limitations & start/stop procedure

- **Start/stop**: the bridge server is one persistent process per Pi session;
  it starts lazily on the first tool call and is killed after a 60 s timeout or
  when the extension's `shutdown()` is called; restart re-opens a fresh one.
- **Combat is out of scope here** — only its registration hook and arbiter
  ownership slots exist.
- **Not yet run against the real game**: bridge mods are not installed in the
  Minecraft instance yet. Drivers were API-verified against the installed
  `minescript`/`pyritone` Python packages (pyritone's `Client` methods are
  async; the driver runs them on a private event loop), but the in-game half is
  untested until the user installs the game-side bridge mods.
- **Biome lists are informational metadata** in config — never validated at
  runtime; wrong entries cannot break anything.
- **Freshness caches are in-memory** — deposits, locations and task history are
  persisted; the per-block observation cache resets when the process restarts.
- **Baritone command templates are configurable** — if a future Baritone build
  changes `#goto`/`#mine` syntax, adjust `config.py` overrides, and capability
  detection (native vs chat-only) adapts automatically.
- **Negative search results are always volume-scoped** — "not found" never means
  "does not exist"; the response includes the searched volume.
- Tests exercise the simulation backend; behavior against a live client depends
  on the game-side mods' actual responses.
