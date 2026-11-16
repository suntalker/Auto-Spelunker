"""mcagent — world-awareness and navigation subsystem for an autonomous
Minecraft agent driven by the Pi LLM harness.

Layers:
  A  mcagent.bridge.drivers     raw observation/command access (Minescript/Pyritone/simulation)
  B  mcagent.world.*            structured filtered world model + persistence
     mcagent.navigation.*       task orchestration over Baritone
  C  mcagent.adapter            LLM-facing tool surface (thin; see mcagent.agent.tools)
"""
from .enums import *  # noqa: F401  re-export shared enums
__version__ = "0.1.0"
