# hermes-hcom-bridge

Ponte entre [hcom](https://github.com/ContAxis/hcom) (barramento de agentes) e
[hermes-agent](https://github.com/nousresearch/hermes-agent) (front-end humano com
Telegram + multiuser nativos + API OpenAI-compat).

```
Humano ──Telegram──▶ Hermes Gateway ──▶ [ponte] ──▶ hcom ──▶ Claude (orquestra) ──▶ devin-cli (executa)
                          ▲                                                    │
Humano ◀──Telegram──── Hermes ◀──── forwarder (lê hcom events) ◀─────────────────┘
                          ▲
                    Kanban web (estado dos agentes hcom, sem tmux)
```

- **Hermes** é a porta de entrada humana (Telegram, multiuser, API). **Configura-se, não se reconstrói.**
- **Claude** orquestra o enxame no hcom. **devin-cli** executa o grind.
- O usuário fala por **Telegram**, vê o enxame num **kanban web**, sem mexer em tmux.

## Componentes

| Componente | Arquivo | Função |
|---|---|---|
| `hcom_client` | `hermes_hcom_bridge/hcom_client.py` | Único wrapper do CLI `hcom` (subprocess, args em lista, timeout, JSON). |
| `tool_swarm` | `plugins/swarm/` + `hermes_hcom_bridge/tool_swarm.py` | Custom tool hermes: `swarm_list`/`swarm_send`/`swarm_spawn`/`swarm_kill`. |
| `forwarder` | `hermes_hcom_bridge/forwarder.py` | Assina `hcom events` e encaminha proativas ao Telegram via `hermes send`. |
| `kanban` | `hermes_hcom_bridge/kanban/` | FastAPI + 1 HTML vanilla, read-only com ações. Fonte = `hcom list`. |

## Setup

Veja **[docs/SETUP.md](./docs/SETUP.md)** (passo a passo reproduzível) e
**[docs/OPEN_QUESTIONS.md](./docs/OPEN_QUESTIONS.md)** (decisões de design validadas
na doc do hermes-agent).

```bash
pip install -e ".[dev]"
cp .env.example .env  # preencha TELEGRAM_BOT_TOKEN, allowlist, API_SERVER_KEY, etc.
python -m hermes_hcom_bridge.kanban.app   # kanban web (default :8643)
python -m hermes_hcom_bridge.forwarder    # forwarder hcom -> Telegram
```

## Testes

```bash
.venv/bin/pytest -q
```

## Stack

Python 3.10+, FastAPI, uvicorn, pydantic. Sem React, sem build frontend.

## Escopo v1

MUST: Telegram→hcom (`swarm_send`/`list`) + kanban read-only com ações + `SETUP.md` reproduzível.
NEXT: forwarder proativo, spawn/kill pelo kanban, multiuser admin-tiering, teste OCI ponta-a-ponta.
