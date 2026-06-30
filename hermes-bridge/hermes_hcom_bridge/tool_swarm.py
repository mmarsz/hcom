"""tool_swarm — handlers + schemas da custom tool `swarm` do hermes-agent.

Reutilizável e testável independente do runtime do hermes. O plugin
(`plugins/swarm/__init__.py`) importa daqui p/ não duplicar lógica.

Cada handler: `handler(params: dict, **kwargs) -> str` (json.dumps).
Admin-tiering: swarm_spawn/swarm_kill só admins (HERMES_ADMIN_USERS csv de
Telegram user IDs). swarm_list/swarm_send liberados p/ qualquer usuário
allowlistado (o gateway já filtra a fronteira de autorização).
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

from .hcom_client import HcomClient, HcomError

# ---- admin tiering ---------------------------------------------------------


def _admin_user_ids() -> set[str]:
    """Lê HERMES_ADMIN_USERS (csv de Telegram user IDs) do env -> set."""
    raw = os.environ.get("HERMES_ADMIN_USERS", "") or ""
    return {tok.strip() for tok in raw.split(",") if tok.strip()}


def _is_admin(user_id: Any) -> bool:
    """user_id vem em params (Telegram). Compara como string (env é csv)."""
    if user_id is None:
        return False
    return str(user_id) in _admin_user_ids()


def _unauthorized() -> str:
    return json.dumps({"error": "unauthorized"})


# ---- client factory --------------------------------------------------------


def _client() -> HcomClient:
    """HcomClient default p/ os handlers. name=bridge."""
    return HcomClient(name=os.environ.get("HCOM_BRIDGE_NAME", "bridge"))


# ---- schemas (JSON schema padrão) ------------------------------------------


SCHEMA_SWARM_LIST = {
    "name": "swarm_list",
    "description": "Lista agentes do enxame hcom (status, unread, tag, dir). "
    "Se `name` informado, retorna o detalhe de um agente.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Nome do agente específico (opcional). "
                "Sem name = lista todos.",
            },
        },
        "required": [],
    },
}

SCHEMA_SWARM_SEND = {
    "name": "swarm_send",
    "description": "Envia mensagem a agentes do enxame via hcom. "
    "targets = nome|tag:T|@all (string ou lista).",
    "parameters": {
        "type": "object",
        "properties": {
            "targets": {
                "description": "Destinatário(s): string ('vino'|'tag:T'|'@all') "
                "ou lista de strings. Omitir/vazio = broadcast.",
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ],
            },
            "text": {"type": "string", "description": "Corpo da mensagem."},
            "intent": {
                "type": "string",
                "enum": ["request", "inform", "ack"],
                "description": "Intent hcom (default: inform).",
            },
            "reply_to": {"type": "string", "description": "ID de evento p/ reply."},
            "thread": {"type": "string", "description": "Nome de thread hcom."},
        },
        "required": ["text"],
    },
}

SCHEMA_SWARM_SPAWN = {
    "name": "swarm_spawn",
    "description": "Sobe agentes no hcom (ADMIN ONLY). "
    "Ex: swarm_spawn(tool='devin', count=1, tag='hbridge', headless=True).",
    "parameters": {
        "type": "object",
        "properties": {
            "tool": {
                "type": "string",
                "description": "Tool hcom: devin|claude|opencode|antigravity.",
            },
            "count": {"type": "integer", "minimum": 1, "default": 1},
            "tag": {"type": "string"},
            "directory": {"type": "string", "description": "--dir do hcom."},
            "headless": {"type": "boolean", "default": False},
            "model": {"type": "string", "description": "ex: opus|sonnet|glm-5.2."},
            "hcom_prompt": {"type": "string", "description": "Prompt inicial do agente."},
        },
        "required": ["tool"],
    },
}

SCHEMA_SWARM_KILL = {
    "name": "swarm_kill",
    "description": "Mata agente(s) do hcom (ADMIN ONLY). "
    "target = name|tag:T|all.",
    "parameters": {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "name | tag:T | all.",
            },
        },
        "required": ["target"],
    },
}


# ---- helpers de args -------------------------------------------------------


def _normalize_targets(targets: Any) -> list[str] | None:
    """string -> [string]; list -> list; None/[] -> None (broadcast).

    '@all' (sozinho) vira broadcast (None) — hcom não aceita @all como target
    direto; broadcast é `send -- text` (sem @target).
    """
    if targets is None:
        return None
    if isinstance(targets, str):
        stripped = targets.strip()
        if not stripped:
            return None
        if stripped.lower() == "@all":
            return None
        return [stripped]
    if isinstance(targets, (list, tuple)):
        out = [str(t).strip() for t in targets if str(t).strip()]
        if not out:
            return None
        if len(out) == 1 and out[0].lower() == "@all":
            return None
        return out
    # fallback: coerce
    return [str(targets)]


# ---- handlers --------------------------------------------------------------


def handle_swarm_list(params: dict, **_kwargs: Any) -> str:
    """swarm_list -> lista agentes ou detalhe de um."""
    name = params.get("name")
    client = _client()
    try:
        if name:
            data: Any = client.agent(str(name))
        else:
            data = client.list_agents()
    except HcomError as e:
        return json.dumps({"error": "hcom_error", "message": str(e)})
    return json.dumps({"agents": data, "ok": True})


def handle_swarm_send(params: dict, **_kwargs: Any) -> str:
    """swarm_send -> client.send com args certos."""
    targets = _normalize_targets(params.get("targets"))
    text = params.get("text")
    if not text:
        return json.dumps({"error": "missing_text"})
    intent = params.get("intent")
    reply_to = params.get("reply_to")
    thread = params.get("thread")
    client = _client()
    try:
        res = client.send(
            targets,
            str(text),
            intent=str(intent) if intent else None,
            reply_to=str(reply_to) if reply_to else None,
            thread=str(thread) if thread else None,
        )
    except HcomError as e:
        return json.dumps({"error": "hcom_error", "message": str(e)})
    return json.dumps(
        {
            "ok": res.ok,
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
    )


def handle_swarm_spawn(params: dict, **kwargs: Any) -> str:
    """swarm_spawn (ADMIN ONLY) -> client.spawn repassando tag/dir/headless."""
    user_id = params.get("user_id", kwargs.get("user_id"))
    if not _is_admin(user_id):
        return _unauthorized()
    tool = params.get("tool")
    if not tool:
        return json.dumps({"error": "missing_tool"})
    count = params.get("count", 1)
    try:
        count_int = int(count) if count is not None else 1
    except (TypeError, ValueError):
        count_int = 1
    if count_int < 1:
        count_int = 1
    client = _client()
    try:
        res = client.spawn(
            str(tool),
            count=count_int,
            tag=params.get("tag"),
            directory=params.get("directory"),
            headless=bool(params.get("headless", False)),
            model=params.get("model"),
            hcom_prompt=params.get("hcom_prompt"),
        )
    except HcomError as e:
        return json.dumps({"error": "hcom_error", "message": str(e)})
    return json.dumps(
        {
            "ok": res.ok,
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
    )


def handle_swarm_kill(params: dict, **kwargs: Any) -> str:
    """swarm_kill (ADMIN ONLY) -> client.kill(target)."""
    user_id = params.get("user_id", kwargs.get("user_id"))
    if not _is_admin(user_id):
        return _unauthorized()
    target = params.get("target")
    if not target:
        return json.dumps({"error": "missing_target"})
    client = _client()
    try:
        res = client.kill(str(target))
    except HcomError as e:
        return json.dumps({"error": "hcom_error", "message": str(e)})
    return json.dumps(
        {
            "ok": res.ok,
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
    )


# ---- registro (p/ plugin) --------------------------------------------------

TOOLS: tuple[tuple[str, dict, Any], ...] = (
    ("swarm_list", SCHEMA_SWARM_LIST, handle_swarm_list),
    ("swarm_send", SCHEMA_SWARM_SEND, handle_swarm_send),
    ("swarm_spawn", SCHEMA_SWARM_SPAWN, handle_swarm_spawn),
    ("swarm_kill", SCHEMA_SWARM_KILL, handle_swarm_kill),
)


def register(ctx: Any) -> None:
    """Registra as 4 tools no ctx do hermes. Reusado pelo plugin __init__."""
    for name, schema, handler in TOOLS:
        ctx.register_tool(
            name=name,
            toolset="swarm",
            schema=schema,
            handler=handler,
            description=schema["description"],
        )
