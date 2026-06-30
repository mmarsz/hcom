"""Testes do kanban (FastAPI) — monkeypatcha HcomClient, sem hcom real.

Cobre: GET /agents estrutura por status; POST /send, /spawn, /kill repassam
p/ o client. 1 teste runnable por lógica não-trivial.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from hermes_hcom_bridge.hcom_client import HcomResult
from hermes_hcom_bridge.kanban import app, set_client


@dataclass
class FakeClient:
    """Grava chamadas; responde HcomResult canned."""
    agents: list[dict]
    send_calls: list[dict]
    spawn_calls: list[dict]
    kill_calls: list[str]

    def list_agents(self, **kw) -> list[dict]:
        return self.agents

    def send(self, targets, text, *, intent=None, **kw) -> HcomResult:
        self.send_calls.append({"targets": list(targets or []), "text": text, "intent": intent})
        return HcomResult(exit_code=0, stdout="ok", stderr="")

    def spawn(self, tool, *, count=1, tag=None, directory=None, headless=False,
              model=None, hcom_prompt=None, **kw) -> HcomResult:
        self.spawn_calls.append({
            "tool": tool, "count": count, "tag": tag, "directory": directory,
            "headless": headless, "model": model, "hcom_prompt": hcom_prompt,
        })
        return HcomResult(exit_code=0, stdout="spawned", stderr="")

    def kill(self, target: str) -> HcomResult:
        self.kill_calls.append(target)
        return HcomResult(exit_code=0, stdout="killed", stderr="")


def _client_with(agents: list[dict]) -> tuple[TestClient, FakeClient]:
    fake = FakeClient(agents=agents, send_calls=[], spawn_calls=[], kill_calls=[])
    set_client(fake)  # type: ignore[arg-type]
    return TestClient(app), fake


# ---- GET /agents ----------------------------------------------------------


def test_agents_returns_200_and_columns_present():
    c, _ = _client_with([
        {"name": "vino", "status": "listening", "unread_count": 2, "tool": "claude"},
        {"name": "nova", "status": "blocked", "unread_count": 0, "tool": "devin", "tag": "hbridge"},
        {"name": "ghost", "status": "inactive", "unread_count": 0},
        {"name": "weird", "status": "zzz", "unread_count": 1},  # unknown
    ])
    r = c.get("/agents")
    assert r.status_code == 200
    data = r.json()
    # colunas garantidas
    for col in ("listening", "active", "blocked", "inactive"):
        assert col in data
    assert [a["name"] for a in data["listening"]] == ["vino"]
    assert [a["name"] for a in data["blocked"]] == ["nova"]
    assert [a["name"] for a in data["inactive"]] == ["ghost"]
    # status não-mapeado cai em unknown
    assert [a["name"] for a in data["unknown"]] == ["weird"]


def test_agents_empty_columns_when_no_agents():
    c, _ = _client_with([])
    r = c.get("/agents")
    assert r.status_code == 200
    data = r.json()
    assert data["listening"] == [] and data["active"] == []
    assert data["blocked"] == [] and data["inactive"] == []
    assert "unknown" not in data  # col unknown omitida quando vazia


# ---- POST /send -----------------------------------------------------------


def test_send_repasses_targets_text_intent():
    c, fake = _client_with([])
    r = c.post("/send", json={"targets": ["vino"], "text": "olá", "intent": "request"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["exit_code"] == 0
    assert fake.send_calls == [{"targets": ["vino"], "text": "olá", "intent": "request"}]


def test_send_broadcast_empty_targets():
    c, fake = _client_with([])
    r = c.post("/send", json={"targets": [], "text": "hi all"})
    assert r.status_code == 200
    assert fake.send_calls[0]["targets"] == []
    assert fake.send_calls[0]["text"] == "hi all"
    assert fake.send_calls[0]["intent"] is None


# ---- POST /spawn ----------------------------------------------------------


def test_spawn_repasses_all_fields():
    c, fake = _client_with([])
    r = c.post("/spawn", json={
        "tool": "devin", "count": 2, "tag": "hbridge", "directory": "/tmp/x",
        "headless": True, "model": "glm", "hcom_prompt": "rode testes",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert fake.spawn_calls == [{
        "tool": "devin", "count": 2, "tag": "hbridge", "directory": "/tmp/x",
        "headless": True, "model": "glm", "hcom_prompt": "rode testes",
    }]


def test_spawn_defaults_count_one():
    c, fake = _client_with([])
    r = c.post("/spawn", json={"tool": "claude"})
    assert r.status_code == 200
    assert fake.spawn_calls[0]["count"] == 1
    assert fake.spawn_calls[0]["headless"] is False


# ---- POST /kill -----------------------------------------------------------


def test_kill_repasses_target():
    c, fake = _client_with([])
    r = c.post("/kill", json={"target": "tag:hbridge"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert fake.kill_calls == ["tag:hbridge"]


# ---- GET / (index.html) ---------------------------------------------------


def test_index_serves_html():
    c, _ = _client_with([])
    r = c.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "kanban" in r.text.lower()
