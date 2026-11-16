/**
 * Minecraft World-Awareness & Navigation subsystem (mcagent) for Pi.
 *
 * Registers high-level, validated tools that forward to the persistent Python
 * bridge process (mcagent/bridge/server.py, a JSON-lines protocol). Long work
 * (navigate, mine) is submit + poll: the tool returns a task_id immediately
 * and the agent polls status — Pi is never blocked waiting on Minecraft.
 *
 * Install: copy or symlink this file into ~/.pi/agent/extensions/ (global)
 * or <project>/.pi/extensions/ (project-local).
 *
 * Environment (read when the bridge starts):
 *   MCAGENT_ROOT  project root, default ~/minecraft_agent
 *   MCAGENT_MODE  simulation | pyritone | minescript, default simulation
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { spawn } from "node:child_process";
import { join } from "node:path";
import { homedir } from "node:os";

const ROOT = process.env.MCAGENT_ROOT ?? join(homedir(), "minecraft_agent");
const MODE = process.env.MCAGENT_MODE ?? "simulation";

// ------------------------------------------------------------------ bridge --

class McBridge {
  private proc: ReturnType<typeof spawn> | null = null;
  private exited = false;
  private buffer = "";
  private waiters = new Map<string, { resolve: (line: string) => void; reject: (err: Error) => void }>();
  private nonce = 0;

  public ensure() {
    if (this.proc && !this.exited) return;
    const p = spawn("python", ["-m", "mcagent.bridge.server", MODE], { cwd: ROOT, stdio: ["pipe", "pipe", "pipe"] });
    p.stdout!.on("data", (chunk: string) => {
      this.buffer += chunk;
      let idx: number;
      while ((idx = this.buffer.indexOf("\n")) >= 0) {
        const line = this.buffer.slice(0, idx).trim();
        this.buffer = this.buffer.slice(idx + 1);
        if (!line) continue;
        const id = lineId(line);
        const waiter = id ? this.waiters.get(id) : undefined;
        if (waiter) {
          this.waiters.delete(id!);
          waiter.resolve(line);
        }
      }
    });
    p.stderr!.on("data", () => {});
    p.on("exit", () => {
      this.exited = true;
      for (const w of this.waiters.values()) w.reject(new Error("mcagent bridge process exited"));
      this.waiters.clear();
      this.proc = null;
    });
    p.on("error", (err: Error) => {
      for (const w of this.waiters.values()) w.reject(err);
      this.waiters.clear();
      this.proc = null;
    });
    this.proc = p;
  }

  public ask(tool: string, args: Record<string, unknown>, timeoutMs = 60000): Promise<object> {
    this.ensure();
    const id = `pi${++this.nonce}`;
    const payload = JSON.stringify({ id, tool, args }) + "\n";
    return new Promise((resolve, reject) => {
      this.waiters.set(id, { resolve, reject });
      this.proc!.stdin!.write(payload);
      setTimeout(() => {
        const pending = this.waiters.get(id);
        if (pending) {
          this.waiters.delete(id);
          this.kill();
          pending.reject(new Error(`mcagent bridge timed out after ${timeoutMs}ms`));
        }
      }, timeoutMs);
    }).then((line: string) => JSON.parse(line));
  }

  public kill() {
    if (this.proc) this.proc.kill();
    this.proc = null;
  }
}

function lineId(line: string): string | null {
  try {
    const parsed = JSON.parse(line);
    return parsed.id != null ? String(parsed.id) : null;
  } catch {
    return null;
  }
}

export const bridge = new McBridge();

async function callBridge(tool: string, args: Record<string, unknown>, timeoutMs = 60000) {
  const reply = await bridge.ask(tool, args, timeoutMs);
  // Pass the Python-side structured envelope through verbatim: success flags,
  // error codes, and OBSERVED/KNOWN knowledge tags are already decided there.
  return {
    content: [{ type: "text", text: JSON.stringify(reply) }],
    details: reply,
  };
}

// ------------------------------------------------------------------- tools --

const num = Type.Number({ description: "coordinate (integer)" });
const blockName = Type.String({ description: "block/item name, e.g. minecraft:iron_ore" });
const radius = Type.Number({ description: "radius in blocks (bounded; see tool notes)" });

const SPECS = [
  {
    name: "mc_look",
    tool: "look",
    description: "Concise live snapshot: player status, nearby hostiles, recent hazards. Runs a cheap awareness pass.",
    params: Type.Object({}),
    snippet: "Snapshot the player's current surroundings before deciding nearby actions.",
    guidelines: ["Use mc_look when the agent needs to know what is currently around the player; results carry freshness tags."],
  },
  {
    name: "mc_get_status",
    tool: "get_status",
    description: "Full status: player, active tasks, memory counts, last observation.",
    params: Type.Object({}),
    snippet: "Full world-model status including active tasks and world-memory counts.",
    guidelines: [],
  },
  {
    name: "mc_get_player_status",
    tool: "get_player_status",
    description: "Player position, health, hunger, dimension, biome, freshness of those readings.",
    params: Type.Object({}),
    snippet: "Read player state (never invents position; errors if the backend is unreachable).",
    guidelines: [],
  },
  {
    name: "mc_inspect_area",
    tool: "inspect_area",
    description: "Bounded deep inspection around a point (radius <= 32); reports knowledge state per relevant block.",
    params: Type.Object({
      x: num, y: num, z: num,
      radius: Type.Optional(radius),
    }),
    snippet: "Deep-inspect a small area when KNOWN data is not enough; knowledge states are reported per block.",
    guidelines: [],
  },
  {
    name: "mc_find_resource",
    tool: "find_resource",
    description: "Objective-driven targeted scan for a block type; reports found / not_found_in_searched_volume / inconclusive — never 'none'.",
    params: Type.Object({
      block_type: blockName,
      near_x: Type.Optional(num), near_y: Type.Optional(num), near_z: Type.Optional(num),
      radius: Type.Optional(radius),
    }),
    snippet: "Look for a specific resource with an honest search-volume result.",
    guidelines: ["Never conclude a resource is absent from mc_find_resource's negative result; it only covers the searched volume."],
  },
  {
    name: "mc_get_nearby_entities",
    tool: "get_nearby_entities",
    description: "Entity list around a point with hostile classification (by type table or client hostility flag).",
    params: Type.Object({
      x: num, y: num, z: num,
      radius: Type.Optional(radius),
    }),
    snippet: "List nearby entities with hostile/peaceful/uncertain classification.",
    guidelines: [],
  },
  {
    name: "mc_navigate",
    tool: "navigate",
    description: "Start navigation via Baritone (#goto). Returns a task_id immediately — poll with mc_get_navigation_status.",
    params: Type.Object({
      destination: Type.Optional(Type.String({ description: "saved location name" })),
      x: Type.Optional(num), y: Type.Optional(num), z: Type.Optional(num),
    }),
    snippet: "Delegate pathfinding/movement to Baritone; returns immediately with a task_id.",
    guidelines: [
      "Navigate via mc_navigate only; mcagent never controls the player's movement itself.",
      "Use mc_navigate with a location saved by mc_remember_location instead of re-deriving coordinates.",
      "Do not call mc_navigate twice for the same goal while a navigation task is in progress.",
    ],
  },
  {
    name: "mc_mine",
    tool: "mine",
    description: "Start mining via Baritone with an explicit strategy (EXPOSED/TUNNEL/AUTO). Returns a task_id; completion verified by inventory delta.",
    params: Type.Object({
      block_type: blockName,
      quantity: Type.Optional(Type.Number({ description: "units to mine (default 1)" })),
      mode: Type.Optional(Type.String({ description: "EXPOSED | TUNNEL | AUTO (default: current session mode)" })),
    }),
    snippet: "Delegate a bounded mining goal to Baritone with an explicit strategy.",
    guidelines: [
      "Mining completion is verified by an inventory delta, not by Baritone's success message.",
      "Pass mode explicitly when the deposit is KNOWN-buried (TUNNEL) rather than letting the system guess."],
  },
  {
    name: "mc_get_navigation_status",
    tool: "get_navigation_status",
    description: "Poll a navigation task: in_progress/arrived/blocked/failed plus progress and interruption state.",
    params: Type.Object({
      task_id: Type.Optional(Type.String({ description: "task id; current task when omitted" })),
    }),
    snippet: "Poll navigation progress; reports interruption ownership from other subsystems.",
    guidelines: ["Poll mc_get_navigation_status after mc_navigate until the task reports arrived, blocked, failed, or cancelled."],
  },
  {
    name: "mc_get_mining_status",
    tool: "get_mining_status",
    description: "Poll a mining task: collected-so-far vs requested quantity; never reports completion from submission alone.",
    params: Type.Object({
      task_id: Type.Optional(Type.String()),
    }),
    snippet: "Poll mining progress and verify yield by inventory delta.",
    guidelines: [],
  },
  {
    name: "mc_cancel_action",
    tool: "cancel_action",
    description: "Cancel an active navigation or mining task by task_id.",
    params: Type.Object({
      task_id: Type.String({ description: "task_id from navigate/mine" }),
    }),
    snippet: "Cancel an active navigation or mining task.",
    guidelines: [],
  },
  {
    name: "mc_get_mining_mode",
    tool: "get_mining_mode",
    description: "Current session mining mode (EXPOSED/TUNNEL/AUTO) and last strategy used.",
    params: Type.Object({}),
    snippet: "Read the session mining-mode policy.",
    guidelines: [],
  },
  {
    name: "mc_set_mining_mode",
    tool: "set_mining_mode",
    description: "Set session mining mode: EXPOSED, TUNNEL, or AUTO.",
    params: Type.Object({
      mode: Type.String({ description: "EXPOSED | TUNNEL | AUTO" }),
    }),
    snippet: "Set the session mining-mode policy.",
    guidelines: [],
  },
  // -- world memory ----------------------------------------------------------
  {
    name: "mc_remember_location",
    tool: "remember_location",
    description: "Save a location to world memory (protected names: base, home).",
    params: Type.Object({
      name: Type.String(),
      x: num, y: num, z: num,
      category: Type.Optional(Type.String({ description: "base | home | POI | stash | route" })),
      note: Type.Optional(Type.String()),
    }),
    snippet: "Record coordinates in world memory so the agent can return later.",
    guidelines: ["Use mc_remember_location before navigating to places the agent should be able to come back to."],
  },
  {
    name: "mc_get_location",
    tool: "get_location",
    description: "Read a saved location (returns KNOWN — it is memory, not observation).",
    params: Type.Object({ name: Type.String() }),
    snippet: "Recall saved coordinates (tagged KNOWN).",
    guidelines: [],
  },
  {
    name: "mc_update_location",
    tool: "update_location",
    description: "Change coordinates of a saved location; important locations require overwrite=true.",
    params: Type.Object({
      name: Type.String(),
      x: num, y: num, z: num,
      overwrite: Type.Optional(Type.Boolean({ description: "required true for protected locations" })),
    }),
    snippet: "Update saved coordinates; protected locations need overwrite=true.",
    guidelines: [],
  },
  {
    name: "mc_list_locations",
    tool: "list_locations",
    description: "List saved locations, optionally filtered by category.",
    params: Type.Object({ category: Type.Optional(Type.String()) }),
    snippet: "List saved locations.",
    guidelines: [],
  },
  {
    name: "mc_forget_location",
    tool: "forget_location",
    description: "Delete a saved location; protected ones require force=true.",
    params: Type.Object({
      name: Type.String(),
      force: Type.Optional(Type.Boolean()),
    }),
    snippet: "Delete a saved location (force=true for protected).",
    guidelines: [],
  },
  {
    name: "mc_search_world_memory",
    tool: "search_world_memory",
    description: "Query memory (locations + recorded deposits) by substring/category; everything returned is KNOWN, not observed.",
    params: Type.Object({
      query: Type.Optional(Type.String()),
      category: Type.Optional(Type.String()),
      limit: Type.Optional(Type.Number()),
    }),
    snippet: "Query world memory (results are KNOWN records, never current observations).",
    guidelines: ["Treat mc_search_world_memory results as KNOWN; re-observe before acting on them where freshness matters."],
  },
  // -- events ----------------------------------------------------------------
  {
    name: "mc_get_events",
    tool: "get_events",
    description: "Drain pending world alerts (hostile spotted, health critical, task finished...). Each is delivered exactly once.",
    params: Type.Object({ limit: Type.Optional(Type.Number()) }),
    snippet: "Drain pending world alerts; each event is delivered exactly once.",
    guidelines: ["Check mc_get_events before planning when idle: it reports hostiles detected, survival alerts, and finished tasks."],
  },
  // Combat tools (engage/disengage/get_combat_status) are intentionally not
  // registered: combat is a separate subsystem and must not be simulated here.
];

export default async function (pi: ExtensionAPI) {
  for (const spec of SPECS) {
    pi.registerTool({
      name: spec.name,
      label: spec.name,
      description: spec.description,
      parameters: spec.params,
      promptSnippet: spec.snippet,
      promptGuidelines: spec.guidelines,
      async execute(_toolCallId, params) {
        return await callBridge(spec.tool, params as Record<string, unknown>);
      },
    });
  }
}
