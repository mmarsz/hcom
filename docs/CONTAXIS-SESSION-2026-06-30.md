# Sessão ContAxis — 2026-06-30 (consolidação hcom + ponte hermes)

Registro do que foi feito (orquestrador: Claude/Opus `orq`; grind: devin-cli via hcom).

## 1. ContAxis/hcom (repo da org)
- Confirmado: hcom + integração devin-cli já estavam no fork `mmarsz/hcom` (`feat/devin-integration` = `main`, `b04a9d6`; fork MIT de `aannoo/hcom`).
- Criado **`ContAxis/hcom`** (privado); push de `main` + `feat/devin-integration`.
- Adicionados: `QUICKSTART.md` (Claude orquestra / devin-cli executa) e `docs/DELEGACAO.md` (extrato de-personalizado do `~/.claude/CLAUDE.md` — sem IPs/creds). Mergeado via PR #1, #2.

## 2. Security review (background) — agent_node.py (cfo-ai-monorepo)
- 2 findings: **CRITICAL** IDOR cross-tenant via `tool_org = tool_args.get("org_id") or org_id` (linha ~1401, deixa LLM sobrepor escopo) + **HIGH** PII não-scrubada no boundary tool_result→LLM (linhas ~3058/3067).
- Verificado: branch atual = `rebase-3188` (PR #3188, escopo NÃO relacionado). O fix de scrub existe em `b18658a8` (branch `conntest-16i`) mas NÃO é alcançável de `rebase-3188`; o IDOR não está corrigido em lugar nenhum.
- **Decisão**: NÃO editar em `rebase-3188` (contaminaria PR #3188 + hook auto-push). Rotear: scrub → landar `conntest-16i`; IDOR → fix de 1 linha (`tool_org = org_id`) em cima de `conntest-16i`. Nenhum código alterado nesta sessão.

## 3. Ponte hermes-hcom (ContAxis/hermes-hcom-bridge)
- Pesquisa (deepwiki): hermes-agent tem **Telegram + multiuser nativos** (Messaging Gateway), API OpenAI-compat `:8642`, sistema de plugins/skills, gateway. → net-new = só **ponte + kanban**; telegram/multiuser se **configuram**, não se constroem.
- Criado **`ContAxis/hermes-hcom-bridge`** (privado) com SPEC.
- OCI hermes mapeado (`ssh mmarsz@instance-20260223-1326`): instalação **nativa** (venv `~/.hermes/hermes-agent`), gateway VIVO, Telegram+multiuser já configurados (`~/.hermes/.env`), webui `:8787` (stdlib http.server), **sem** API `:8642`.
- Swarm devin-cli:
  - **kite**: implementou v1 (plugin `swarm` + kanban web + forwarder + 49 testes verdes); morto `by:cli` mid-build; branch `devin/bridge-v1` preservada (pushada).
  - **tuna** (`hb_lead`): verificou OCI — **plugins Python suportados**, gateway recarrega por-sessão (deploy sem restart), `hermes send --to telegram:<chat>` p/ forwarder, `hcom list --json` p/ kanban. Decisão ponytail: **kanban standalone FastAPI `:8643`** (não invade o webui). Transporte OCI↔momoko: **`hcom relay` com broker mosquitto no OCI** bound ao IP Tailscale `100.87.96.23:1883` (plain mqtt + password; WireGuard já cifra). Forwarder: parse NDJSON, filtro de intent, skip histórico no startup.
- Guardrails enviados ao lead: bind na interface Tailscale (nunca `0.0.0.0`), spawn/kill destrutivo **admin-only** (multiuser), forwarder+kanban como `systemd --user`/nohup.

## 4. Guidance ao usuário
- **hcom relay cross-device** via Tailscale: `relay new` → `relay connect <token>` → `daemon start`; broker próprio `--broker mqtt://100.x:1883 --password <s>`.
- WebUI hermes: `HERMES_WEBUI_PASSWORD` em `~/hermes/hermes-webui/.env` (OCI).
- Acesso de fora: Telegram (anywhere) + kanban `:8643`/webui `:8787` via Tailscale.

## 5. Identidade hcom
- Registrado como **`orq`** (`claude-orq` tinha hífen inválido). Subscription `sub-5897db48` em `@orq` mentions.

---
*Consolidação subsequente (merge da ponte no fork, enxugar tooling, cross-compile, reescrita de README/onboarding) executada por devin-cli GLM-5.2 — ver `docs/CONTAXIS-FORK.md`.*
