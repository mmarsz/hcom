"""Plugin hermes `swarm` — registra as 4 tools via ctx.register_tool.

Lógica vive em hermes_hcom_bridge.tool_swarm (testável); aqui só roteia o
registro p/ o runtime do hermes descobrir em plugins/swarm/.
"""

from __future__ import annotations

from hermes_hcom_bridge.tool_swarm import register

__all__ = ["register"]
