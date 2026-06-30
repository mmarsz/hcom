"""Kanban web — board read-only + ações por botão (FastAPI + HTML vanilla)."""

from .app import app, create_app, set_client

__all__ = ["app", "create_app", "set_client"]
