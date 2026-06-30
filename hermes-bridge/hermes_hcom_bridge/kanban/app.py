"""FastAPI app — kanban board de agentes hcom.

ponytail: read-only board + ações por botão, sem drag-and-drop. HcomClient
instanciado uma vez (module-level); testes injetam fake via `set_client()`.
Config via env: KANBAN_PORT (default 8643).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ..hcom_client import HcomClient, HcomError

# Colunas na ordem de exibição. "done" = inactive (agentes parados).
COLUMNS: dict[str, str] = {
    "listening": "Listening",
    "active": "Active",
    "blocked": "Blocked",
    "inactive": "Done",
}
# status unknown cai numa coluna extra implícita (não mapeada)
_UNKNOWN_COL = "unknown"

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# ponytail: cliente module-level, instanciado uma vez. Testes trocam via set_client.
_client: HcomClient = HcomClient(
    hcom_path=os.environ.get("HCOM_PATH"),
    default_timeout=float(os.environ.get("HCOM_TIMEOUT", "30")),
    name=os.environ.get("HCOM_NAME", "kanban"),
)


def set_client(client: HcomClient) -> None:
    """Injeta um HcomClient (uso em testes)."""
    global _client
    _client = client


def get_client() -> HcomClient:
    return _client


# ---- models ----------------------------------------------------------------


class SendBody(BaseModel):
    targets: list[str] | None = Field(default=None, description="@nomes; vazio = broadcast")
    text: str
    intent: str | None = Field(default=None, description="request|inform|ack")


class SpawnBody(BaseModel):
    tool: str
    count: int = Field(default=1, ge=1)
    tag: str | None = None
    directory: str | None = None
    headless: bool = False
    model: str | None = None
    hcom_prompt: str | None = None


class KillBody(BaseModel):
    target: str


# ---- app -------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="hermes-hcom kanban", version="0.1.0")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/agents")
    async def agents() -> JSONResponse:
        """list_agents() agrupado por status. Colunas garantidas presentes."""
        try:
            raw = get_client().list_agents()
        except HcomError as e:
            raise HTTPException(status_code=502, detail=f"hcom: {e}")
        grouped: dict[str, list[dict[str, Any]]] = {col: [] for col in COLUMNS}
        grouped[_UNKNOWN_COL] = []
        for a in raw:
            status = (a.get("status") or _UNKNOWN_COL).strip().lower()
            col = status if status in grouped else _UNKNOWN_COL
            grouped[col].append(a)
        if not grouped[_UNKNOWN_COL]:
            grouped.pop(_UNKNOWN_COL)
        return JSONResponse(grouped)

    @app.post("/send")
    async def send(body: SendBody) -> dict[str, Any]:
        try:
            res = get_client().send(body.targets, body.text, intent=body.intent)
        except HcomError as e:
            raise HTTPException(status_code=502, detail=f"hcom: {e}")
        return {"ok": res.ok, "exit_code": res.exit_code, "stdout": res.stdout, "stderr": res.stderr}

    @app.post("/spawn")
    async def spawn(body: SpawnBody) -> dict[str, Any]:
        try:
            res = get_client().spawn(
                body.tool,
                count=body.count,
                tag=body.tag,
                directory=body.directory,
                headless=body.headless,
                model=body.model,
                hcom_prompt=body.hcom_prompt,
            )
        except HcomError as e:
            raise HTTPException(status_code=502, detail=f"hcom: {e}")
        return {"ok": res.ok, "exit_code": res.exit_code, "stdout": res.stdout, "stderr": res.stderr}

    @app.post("/kill")
    async def kill(body: KillBody) -> dict[str, Any]:
        try:
            res = get_client().kill(body.target)
        except HcomError as e:
            raise HTTPException(status_code=502, detail=f"hcom: {e}")
        return {"ok": res.ok, "exit_code": res.exit_code, "stdout": res.stdout, "stderr": res.stderr}

    return app


app = create_app()


def main() -> None:
    import uvicorn

    port = int(os.environ.get("KANBAN_PORT", "8643"))
    # Default 127.0.0.1: o kanban tem POST /spawn e /kill sem auth — não expor
    # na LAN sem um reverse proxy com auth na frente. Override via KANBAN_HOST.
    host = os.environ.get("KANBAN_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
