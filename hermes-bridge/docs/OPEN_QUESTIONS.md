# Open Questions — resolvidas (consulta à doc do hermes-agent)

Fonte: deepwiki `nousresearch/hermes-agent` + `hcom --help` local (hcom 0.7.22).
Registrado no hcom por `hb_lead` em 2026-06-30.

## Q1 — Mecanismo exato de tool/skill custom do hermes

**Resposta:** tool custom = **plugin** Python. Estrutura:

```
plugins/swarm/
  plugin.yaml
  __init__.py      # def register(ctx): ctx.register_tool(name=..., toolset=..., schema=..., handler=..., description=...)
```

- `handler(params, **kwargs)` recebe args como **dict** e retorna **string JSON** (`json.dumps({...})`).
- `schema` = JSON schema padrão (name, description, parameters{type:object, properties, required}).
- Descoberta: `<repo>/plugins/`, `~/.hermes/plugins/`, `./.hermes/plugins/`.
- Habilitar: listar o nome do plugin em `plugins.enabled` do `~/.hermes/config.yaml`
  (ou `hermes plugins enable <nome>`).
- Skills (markdown `SKILL.md` em `~/.hermes/skills/`) são alternativa p/ capacidades
  expressáveis como instruções/shell — NÃO usaremos p/ `swarm` (precisa de Python/subprocess).

## Q2 — Entrega de mensagem proativa (agent-initiated) a um usuário Telegram

**Resposta:** CLI `hermes send --to <destino> "<texto>"` — wrapper fino sobre
`tools.send_message_tool.send_message_tool`, **sem LLM/agent loop**, fala direto com a
API REST do Telegram (bot-token).

```bash
hermes send --to telegram "deploy finished"
echo "RAM 92%" | hermes send --to telegram:-1001234567890
hermes send --to telegram:<chat_id>:<thread_id> "msg"
```

Formatos de `--to`: `platform` | `platform:chat_id` | `platform:chat_id:thread_id`.
Reusa credenciais do gateway já configurado. **Forwarder v1** = subscrever
`hcom events` e chamar `hermes send --to telegram:<chat_id>` p/ eventos relevantes.

## Q3 — `hcom list`/`events` têm saída `--json`?

**Resposta: SIM.** Confirmado no `hcom --help` local (v0.7.22):
- `hcom list --json` → JSON array de todos os agentes (campos: name, status,
  status_context, status_detail, status_age_seconds, description, unread_count,
  tool, tag, directory, session_id, parent_name, agent_id, headless, created_at, ...).
- `hcom list <name> --json` → detalhe de um agente.
- `hcom events` → últimos 20 eventos **como JSON**; filtros `--agent`, `--type
  message|status|life`, `--status listening|active|blocked`, `--intent`, `--from`,
  `--mention`, `--after/--before`, `--last N`, `--wait [SEC]`, `--sql EXPR`.

**Decisão:** Kanban usa o **CLI do hcom com `--json`** (não lê SQLite cru de
`~/.hcom`). `hcom_client.py` encapsula isso.

## Q4 — Como o hermes do OCI (100.87.96.23) está rodando

**Resposta (RESOLVIDA 2026-06-30):** Instalação **nativa** (venv), NÃO docker.
Binário em `~/.hermes/hermes-agent/venv/bin/hermes`. Gateway + webui (`:8787`)
vivos como processos plain (4+ dias up). Telegram + multiuser JÁ configurados
em `~/.hermes/.env` — não reconfigurar do zero. SSH via **hostname Tailscale**
`mmarsz@instance-20260223-1326` (o IP `100.87.96.23` dá timeout de fora; o
hostname resolve). Deploy da ponte foi **aditivo** (plugin + daemons nohup),
sem restartar gateway/webui. Detalhes completos em `docs/SETUP.md` seção 11.

## Q5 — Como o plugin no OCI alcança o enxame hcom local (cross-device)

**Resposta (RESOLVIDA 2026-06-30):** Enxame roda no **momoko** (local, x86_64);
hermes no **OCI** (aarch64). Binário `hcom` não é portável entre arquiteturas,
e `hcom` não tem modo server de rede direto. Solução: **hcom relay** via broker
**mosquitto no OCI** (bound ao IP Tailscale `100.87.96.23:1883`, password auth).
Brokers MQTT públicos estão inalcançáveis do momoko (egress bloqueado) — broker
próprio no OCI é a saída. OCI `hcom` vê agentes locais com sufixo `:<NODE>`
(ex.: `orq:FELE`); `HCOM_TARGET_SUFFIX=:FELE` no `hcom_client` qualifica
targets base. Detalhes em `docs/SETUP.md` seção 11.2.

## Q6 — `hcom events` é JSON array ou NDJSON?

**Resposta (RESOLVIDA 2026-06-30):** **NDJSON** — um evento por linha, NÃO um
array JSON. `hcom list --json` é array JSON; `hcom events` é NDJSON. Bug
encontrado em produção: `_parse_json` pegava só a primeira linha → `events()`
retornava 1 evento só, travando o forwarder. Fix: `_parse_json_lines` dedicado
em `hcom_client.py` (NDJSON com fallback p/ array). Corrigia Q3 parcialmente:
`events` tem `--json`-like output mas em formato NDJSON, não array.
