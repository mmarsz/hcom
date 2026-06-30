"""hermes-hcom-bridge: ponte hcom <-> hermes-agent.

Módulos:
- hcom_client: único wrapper do CLI hcom (subprocess).
- tool_swarm: plugin hermes com tools swarm_* (list/send/spawn/kill).
- forwarder: assina hcom events e encaminha proativas ao Telegram via `hermes send`.
- kanban.app: FastAPI read-only + ações, fonte = hcom.
"""

from .hcom_client import HcomClient, HcomError, HcomResult

__all__ = ["HcomClient", "HcomError", "HcomResult"]
