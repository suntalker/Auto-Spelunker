// Bridge protocol probe: exercises the real request/response plumbing of the
// extension without involving an LLM. Run with:
//   node --node-modules=... pi-extension/_bridge_probe.ts
import { bridge } from "./minecraft-adapter.ts";

const reply = await bridge.ask("describe", {}, 10000);
console.log("describe:", JSON.stringify(reply).slice(0, 200));
const look = await bridge.ask("look", {}, 30000);
console.log("look.success:", look.success);
console.log("position:", JSON.stringify(look.result?.player?.position));
bridge.kill();
process.exit(0);
