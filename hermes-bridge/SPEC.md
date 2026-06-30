# hermes-hcom-bridge — SPEC

> Ponte entre **hcom** (barramento de agentes: Claude orquestrador + devin-cli executores) e
> **hermes-agent** (Nous Research — front-end humano com Telegram/multiuser nativos + API).
> Autor do spec: Claude (Opus, orquestrador). Implementação: devin-cli (GLM/Kimi) via hcom.

---

## Visão

```
Humano ──Telegram──▶ Hermes Gateway ──▶ [ponte] ──▶ hcom ──▶ Claude (orquestra) ──▶ devin-cli (executa)
                          ▲                                                    │
Humano ◀──Telegram──── Hermes ◀──── [forwarder lê hcom events sub] ◀───────────┘
                          ▲
                    Kanban web (estado dos agentes hcom, sem tmux)
```

Hermes é a porta de entrada humana. Claude orquestra o enxame no hcom. devin-cli executa.
O usuário fala por **Telegram** (e é **multiuser**), vê o enxame num **kanban**, sem mexer em tmux.

## Princípio (NÃO reinventar)

Hermes-agent **já tem** nativo, então **configura-se, não se constrói**:
- **Messaging Gateway**: Telegram/Discord/Slack/+20 plataformas. → Telegram = só `TELEGRAM_BOT_TOKEN` + allowlist.
- **Multiuser**: allowlist de user IDs + DM pairing no gateway.
- **API OpenAI-compat**: `http://127.0.0.1:8642/v1/chat/completions` (habilita via `API_SERVER_ENABLED=true` + `API_SERVER_KEY`).
- **TUI Gateway JSON-RPC** + **custom tools/skills** (Python) + lib `AIAgent`.

> **Confirme tudo isso na fonte antes de codar** (deepwiki `nousresearch/hermes-agent`,
> repo, https://hermes-agent.nousresearch.com/). Se algum mecanismo divergir, ajuste o spec e avise no hcom.

## Componentes (net-new)

### 1. Hermes tool `swarm` (controle: humano → enxame)
Custom tool/skill Python registrada no hermes. Ações que fazem shell-out pro `hcom`:
- `swarm_list` → `hcom list -v` (ou `--json` se existir) → estado dos agentes.
- `swarm_send(name|tag, text, intent)` → `hcom send @X --intent ... -- "..."`.
- `swarm_spawn(tool, prompt, tag, dir)` → spawn devin-cli/claude (YOLO env já setado).
- `swarm_kill(name|tag)` → `hcom kill`.
Assim, do Telegram: "status do enxame", "manda o vino rodar os testes", "sobe 1 devin pra X".

### 2. Forwarder (proativo: enxame → humano)
Processo que assina `hcom events sub [filtros]` (stream) e empurra mensagens/estados relevantes
de agentes pro usuário no hermes/Telegram. **Resolver na doc do hermes**: como entregar mensagem
agent-initiated a um usuário do gateway (API de envio do gateway vs. injeção na sessão).
v1: encaminhar eventos `--type message` e `--status blocked`. Anti-flood: dedup + rate-limit.

### 3. Kanban web (visual, sem tmux)
- **Backend**: FastAPI pequeno. Fonte de verdade = estado do hcom. **Prefira o CLI do hcom**
  (`hcom list`/`events` com `--json` se houver) em vez de ler o SQLite cru de `~/.hcom`
  (inspecione `~/.hcom` só se o CLI não der JSON). Endpoints: `GET /agents`,
  `POST /send`, `POST /spawn`, `POST /kill`.
- **Frontend**: 1 página HTML estática + JS vanilla (sem build, sem React). Colunas por estado
  (listening / active / blocked / done). Poll `/agents` a cada ~2s. Clicar agente → caixa de envio.
  Botões spawn/kill. ponytail: read-only board + ações por botão; nada de drag-and-drop na v1.

### 4. Config hermes (Telegram + multiuser) — documentar, não codar
`docs/SETUP.md`: `.env` do hermes (TELEGRAM_BOT_TOKEN, allowlist multiuser, API_SERVER_*),
como registrar a tool `swarm`, subir o forwarder e o kanban. Passo a passo reproduzível.

## Stack & layout

Python (casa com hermes). Repo:
```
hermes_hcom_bridge/  tool_swarm.py  forwarder.py  hcom_client.py(wrapper subprocess)  kanban/{app.py,static/index.html}
docs/SETUP.md  tests/  pyproject.toml  README.md
```
`hcom_client.py` = único ponto que fala com o `hcom` CLI (subprocess), com timeout e parse robusto.

## Teste

- **Dev local**: subir hermes-agent local (docker/compose conforme repo) + hcom local. Validar:
  Telegram→swarm_send chega no agente; agente→forwarder chega no Telegram; kanban reflete `hcom list`.
- **OCI** (alvo do usuário): hermes do usuário em `100.87.96.23`. **ssh deste host falhou (timeout) —
  validar acesso primeiro** (`ssh mmarsz@100.87.96.23`; checar known_hosts/chave). Tailscale do OCI
  está ATIVO, então é problema de credencial ssh local, não de rede. Descobrir porta/host do hermes lá
  e apontar a ponte. Não mutar nada no OCI sem necessidade.
- 1 teste runnable por lógica não-trivial (parse de `hcom list`, dedup do forwarder, shell-out).

## Segurança (multiuser)

- Allowlist de usuários no hermes = fronteira de autorização. `swarm_spawn`/`swarm_kill` são
  destrutivos/custosos → restringir a IDs admin (não todo usuário do gateway).
- Nunca commitar `TELEGRAM_BOT_TOKEN`/`API_SERVER_KEY` — `.env` fora do git, `.env.example` só com chaves.
- Shell-out pro hcom: nada de interpolar texto do usuário direto em shell sem sanitizar (use listas de args).

## Open questions (devin resolve na doc do hermes, registra resposta no hcom)

1. Mecanismo exato de tool/skill custom do hermes (assinatura, registro).
2. Como o gateway entrega mensagem proativa (agent-initiated) a um usuário Telegram.
3. `hcom list`/`events` têm saída `--json`? (define se kanban usa CLI ou SQLite).
4. Como o hermes do OCI está rodando (docker? porta? telegram já configurado?).

## Escopo v1 (ship primeiro, depois enriquece)

MUST: Telegram→hcom (swarm_send/list) + kanban read-only com ações + SETUP.md reproduzível.
NEXT: forwarder proativo, spawn/kill pelo kanban, multiuser admin-tiering, teste OCI ponta-a-ponta.

---

## OCI REALITY (verificado 2026-06-30, ssh `mmarsz@instance-20260223-1326`)

O hermes do usuário **já está rodando** no OCI — isto SUBSTITUI suposições do spec acima:

- **Instalação nativa** (venv, não docker): `~/.hermes/hermes-agent` (`venv/bin/python -m hermes_cli.main gateway run`). Gateway VIVO (pid persistente).
- **Telegram + multiuser JÁ configurados e ativos**: `~/.hermes/.env` tem `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS` (multiuser), `TELEGRAM_HOME_CHANNEL`. → **Não configurar do zero; testar contra este.** Pedir ao usuário um user de teste allowlisted se precisar.
- **Sem API server `:8642`** (não habilitado). → **Integrar via SKILLS do hermes, não via API OpenAI-compat.** O gateway não tem porta inbound (fala outbound com Telegram).
- **Sistema de skills**: `~/.hermes/skills/<categoria>/<skill>/`. Categorias já existentes: apple, autonomous-ai-agents, computer-use, creative, data-science, dogfood, email, github, media, mlops, note-taking, software-development. Existe `~/.hermes/skills/software-development/hermes-agent-skill-authoring/` → **seguir esse padrão pra autorar a skill `swarm`**.
- **Já existe um webui**: `~/hermes/hermes-webui/server.py` servindo em **`100.87.96.23:8787`** (Python stdlib `http.server.ThreadingHTTPServer`, não FastAPI). Tem ARCHITECTURE.md/DESIGN.md. → **Avaliar integrar o kanban como página/rota nova nesse webui** (ponytail: reusar o servidor que já existe) **vs. app separado**. Ler o ARCHITECTURE.md antes de decidir.

### Reframe da arquitetura v1 (sobrepõe a seção "Componentes")

1. **`swarm` = skill hermes** (não custom tool via API). Em `~/.hermes/skills/<cat>/swarm/`, seguindo `hermes-agent-skill-authoring`. Faz shell-out pro `hcom` (list/send/spawn/kill). Source versionado neste repo, deployado pro OCI.
2. **Kanban**: preferir estender `hermes-webui` (:8787) com painel de agentes (lê `hcom list`/eventos). Se o webui não for extensível de forma limpa, app standalone mínimo.
3. **Forwarder**: lê subscription/eventos do hcom (`hcom events sub --mention <bot> --as <bot>` ou `hcom events`) → entrega no Telegram **via o gateway hermes já rodando** (descobrir o mecanismo de envio agent-initiated do gateway; ver skills/gateway docs).
4. **Telegram/multiuser**: já feito no OCI. `docs/SETUP.md` documenta o que existe + como replicar, sem expor secrets.

### Regras OCI

- **Não derrubar** o gateway nem o webui que já rodam. Deploy aditivo (nova skill, nova rota).
- ssh: `mmarsz@instance-20260223-1326` (hostname tailscale; o IP 100.87.96.23 deu timeout antes — usar hostname).
- Secrets do `.env` do OCI nunca vão pro git.
