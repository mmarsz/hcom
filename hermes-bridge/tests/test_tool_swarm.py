"""Testes dos handlers da tool swarm.

Usa HcomClient fake (monkeypatch) — não depende de hcom real nem do runtime
do hermes. 1 teste runnable por lógica não-trivial:
- list retorna agentes (lista e detalhe)
- send chama client.send com args certos (string e lista de targets, broadcast)
- spawn repassa tag/dir/headless
- kill repassa target
- admin-tiering: spawn/kill recusam se user_id não em HERMES_ADMIN_USERS;
  liberam se for admin. list/send ignoram tiering.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from hermes_hcom_bridge import tool_swarm
from hermes_hcom_bridge.hcom_client import HcomError, HcomResult
from hermes_hcom_bridge.tool_swarm import (
    handle_swarm_kill,
    handle_swarm_list,
    handle_swarm_send,
    handle_swarm_spawn,
)


# ---- fakes -----------------------------------------------------------------


class FakeClient:
    """HcomClient fake. Registra chamadas e retorna resultados configurados."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.list_return: list[dict] = [{"name": "vino", "status": "listening"}]
        self.agent_return: dict = {"name": "vino", "status": "listening"}
        self.send_result = HcomResult(0, "ok", "")
        self.spawn_result = HcomResult(0, "spawned", "")
        self.kill_result = HcomResult(0, "killed", "")
        self.raise_error: bool = False

    # API espelhada de HcomClient
    def list_agents(self, *, include_stopped: bool = False) -> list[dict]:
        self.calls.append(("list_agents", {"include_stopped": include_stopped}))
        if self.raise_error:
            raise HcomError("boom")
        return self.list_return

    def agent(self, name: str) -> dict:
        self.calls.append(("agent", {"name": name}))
        if self.raise_error:
            raise HcomError("boom")
        return self.agent_return

    def send(
        self,
        targets,
        text: str,
        *,
        intent=None,
        reply_to=None,
        thread=None,
        name=None,
        go: bool = True,
    ) -> HcomResult:
        self.calls.append(
            (
                "send",
                {
                    "targets": list(targets) if targets is not None else None,
                    "text": text,
                    "intent": intent,
                    "reply_to": reply_to,
                    "thread": thread,
                    "name": name,
                    "go": go,
                },
            )
        )
        if self.raise_error:
            raise HcomError("boom")
        return self.send_result

    def spawn(
        self,
        tool: str,
        *,
        count: int = 1,
        tag=None,
        directory=None,
        headless: bool = False,
        model=None,
        hcom_prompt=None,
        extra_args=None,
        env=None,
        timeout=None,
    ) -> HcomResult:
        self.calls.append(
            (
                "spawn",
                {
                    "tool": tool,
                    "count": count,
                    "tag": tag,
                    "directory": directory,
                    "headless": headless,
                    "model": model,
                    "hcom_prompt": hcom_prompt,
                },
            )
        )
        if self.raise_error:
            raise HcomError("boom")
        return self.spawn_result

    def kill(self, target: str) -> HcomResult:
        self.calls.append(("kill", {"target": target}))
        if self.raise_error:
            raise HcomError("boom")
        return self.kill_result


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    fc = FakeClient()
    monkeypatch.setattr(tool_swarm, "_client", lambda: fc)
    return fc


@pytest.fixture
def admin_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_ADMIN_USERS", "111,222")


# ---- swarm_list ------------------------------------------------------------


def test_list_returns_agents(fake_client: FakeClient) -> None:
    fake_client.list_return = [
        {"name": "vino", "status": "listening", "unread_count": 2},
        {"name": "nova", "status": "blocked"},
    ]
    out = json.loads(handle_swarm_list({}))
    assert out["ok"] is True
    assert [a["name"] for a in out["agents"]] == ["vino", "nova"]
    assert out["agents"][0]["unread_count"] == 2
    assert fake_client.calls[0][0] == "list_agents"


def test_list_single_agent_detail(fake_client: FakeClient) -> None:
    fake_client.agent_return = {"name": "vino", "status": "active"}
    out = json.loads(handle_swarm_list({"name": "vino"}))
    assert out["ok"] is True
    assert out["agents"]["name"] == "vino"
    assert fake_client.calls[0] == ("agent", {"name": "vino"})


def test_list_handles_hcom_error(fake_client: FakeClient) -> None:
    fake_client.raise_error = True
    out = json.loads(handle_swarm_list({}))
    assert out["error"] == "hcom_error"
    assert "boom" in out["message"]


# ---- swarm_send ------------------------------------------------------------


def test_send_string_target_passes_correct_args(fake_client: FakeClient) -> None:
    out = json.loads(
        handle_swarm_send(
            {"targets": "vino", "text": "rode os testes", "intent": "request"}
        )
    )
    assert out["ok"] is True
    call = fake_client.calls[0]
    assert call[0] == "send"
    args = call[1]
    assert args["targets"] == ["vino"]
    assert args["text"] == "rode os testes"
    assert args["intent"] == "request"
    assert args["reply_to"] is None
    assert args["thread"] is None


def test_send_list_targets(fake_client: FakeClient) -> None:
    json.loads(handle_swarm_send({"targets": ["vino", "tag:T"], "text": "hi"}))
    assert fake_client.calls[0][1]["targets"] == ["vino", "tag:T"]


def test_send_broadcast_when_no_targets(fake_client: FakeClient) -> None:
    json.loads(handle_swarm_send({"text": "hi all"}))
    assert fake_client.calls[0][1]["targets"] is None
    assert fake_client.calls[0][1]["text"] == "hi all"


def test_send_empty_string_target_becomes_broadcast(fake_client: FakeClient) -> None:
    json.loads(handle_swarm_send({"targets": "   ", "text": "hi"}))
    assert fake_client.calls[0][1]["targets"] is None


def test_send_at_all_becomes_broadcast(fake_client: FakeClient) -> None:
    # hcom não aceita @all como target direto — vira broadcast (None)
    json.loads(handle_swarm_send({"targets": "@all", "text": "hi all"}))
    assert fake_client.calls[0][1]["targets"] is None
    json.loads(handle_swarm_send({"targets": ["@all"], "text": "hi all2"}))
    assert fake_client.calls[1][1]["targets"] is None


def test_send_missing_text_returns_error(fake_client: FakeClient) -> None:
    out = json.loads(handle_swarm_send({"targets": "vino"}))
    assert out["error"] == "missing_text"
    assert fake_client.calls == []  # nem chamou o client


def test_send_passes_reply_to_and_thread(fake_client: FakeClient) -> None:
    json.loads(
        handle_swarm_send(
            {
                "targets": "vino",
                "text": "reply",
                "reply_to": "evt_42",
                "thread": "build",
            }
        )
    )
    args = fake_client.calls[0][1]
    assert args["reply_to"] == "evt_42"
    assert args["thread"] == "build"


# ---- swarm_spawn -----------------------------------------------------------


def test_spawn_non_admin_refused(fake_client: FakeClient) -> None:
    # sem env de admin -> recusa
    out = json.loads(handle_swarm_spawn({"tool": "devin", "user_id": "999"}))
    assert out["error"] == "unauthorized"
    assert fake_client.calls == []  # nem chamou spawn


def test_spawn_admin_passes_tag_dir_headless(
    fake_client: FakeClient, admin_env: None
) -> None:
    out = json.loads(
        handle_swarm_spawn(
            {
                "tool": "devin",
                "count": 2,
                "tag": "hbridge",
                "directory": "/tmp/repo",
                "headless": True,
                "model": "glm-5.2",
                "hcom_prompt": "rode os testes",
                "user_id": "111",
            }
        )
    )
    assert out["ok"] is True
    args = fake_client.calls[0][1]
    assert args["tool"] == "devin"
    assert args["count"] == 2
    assert args["tag"] == "hbridge"
    assert args["directory"] == "/tmp/repo"
    assert args["headless"] is True
    assert args["model"] == "glm-5.2"
    assert args["hcom_prompt"] == "rode os testes"


def test_spawn_admin_id_as_int_matches_env(
    fake_client: FakeClient, admin_env: None
) -> None:
    # user_id pode vir como int do gateway; comparação como string
    json.loads(handle_swarm_spawn({"tool": "devin", "user_id": 222}))
    assert fake_client.calls[0][0] == "spawn"


def test_spawn_missing_tool_admin(fake_client: FakeClient, admin_env: None) -> None:
    out = json.loads(handle_swarm_spawn({"user_id": "111"}))
    assert out["error"] == "missing_tool"
    assert fake_client.calls == []


def test_spawn_count_coerced_and_floored(
    fake_client: FakeClient, admin_env: None
) -> None:
    json.loads(handle_swarm_spawn({"tool": "devin", "count": "3", "user_id": "111"}))
    assert fake_client.calls[0][1]["count"] == 3
    json.loads(handle_swarm_spawn({"tool": "devin", "count": 0, "user_id": "111"}))
    assert fake_client.calls[1][1]["count"] == 1


# ---- swarm_kill ------------------------------------------------------------


def test_kill_non_admin_refused(fake_client: FakeClient) -> None:
    out = json.loads(handle_swarm_kill({"target": "tag:hbridge", "user_id": "999"}))
    assert out["error"] == "unauthorized"
    assert fake_client.calls == []


def test_kill_admin_passes_target(fake_client: FakeClient, admin_env: None) -> None:
    out = json.loads(
        handle_swarm_kill({"target": "tag:hbridge", "user_id": "111"})
    )
    assert out["ok"] is True
    assert fake_client.calls[0] == ("kill", {"target": "tag:hbridge"})


def test_kill_missing_target_admin(fake_client: FakeClient, admin_env: None) -> None:
    out = json.loads(handle_swarm_kill({"user_id": "111"}))
    assert out["error"] == "missing_target"
    assert fake_client.calls == []


# ---- tiering: list/send não exigem admin -----------------------------------


def test_list_works_without_admin(fake_client: FakeClient) -> None:
    out = json.loads(handle_swarm_list({}))
    assert out["ok"] is True


def test_send_works_without_admin(fake_client: FakeClient) -> None:
    out = json.loads(handle_swarm_send({"targets": "vino", "text": "hi"}))
    assert out["ok"] is True


# ---- registro (sanity) -----------------------------------------------------


def test_register_calls_ctx_for_all_tools() -> None:
    class FakeCtx:
        def __init__(self) -> None:
            self.registered: list[dict] = []

        def register_tool(self, **kw: Any) -> None:
            self.registered.append(kw)

    ctx = FakeCtx()
    tool_swarm.register(ctx)
    names = [r["name"] for r in ctx.registered]
    assert names == ["swarm_list", "swarm_send", "swarm_spawn", "swarm_kill"]
    for r in ctx.registered:
        assert r["toolset"] == "swarm"
        assert callable(r["handler"])
        assert "parameters" in r["schema"]
