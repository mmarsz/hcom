# SETUP — hermes-hcom-bridge

Passo a passo reproduzível para subir a ponte **hcom ↔ hermes-agent** do zero.
PT-BR. Comandos copy-paste. Sem secrets reais (use placeholders).

> Pré-requisito de leitura: [SPEC.md](../SPEC.md) e [docs/OPEN_QUESTIONS.md](./OPEN_QUESTIONS.md).

---

## 1. Visão rápida da ponte

A ponte liga o **hcom** (barramento de agentes — Claude orquestrador + devin-cli
executores) ao **hermes-agent** (Nous Research — front-end humano com Telegram,
multiuser e API OpenAI-compat nativos). O humano fala por **Telegram**, o hermes
encaminha para a ponte, a ponte fala com o `hcom` via subprocess, e o enxame
executa. Um **kanban web** mostra o estado dos agentes sem tmux, e um
**forwarder** devolve mensagens proativas do enxame ao Telegram.

```
Humano ──Telegram──▶ Hermes Gateway ──▶ [ponte] ──▶ hcom ──▶ Claude (orquestra) ──▶ devin-cli (executa)
                          ▲                                                    │
Humano ◀──Telegram──── Hermes ◀──── forwarder (lê hcom events) ◀─────────────────┘
                          ▲
                    Kanban web (estado dos agentes hcom, sem tmux)
```

---

## 2. Pré-requisitos

- **Python 3.10+** (`python3 --version`).
- **hcom** (Rust, via cargo):
  ```bash
  cargo install hcom          # ou siga o README do repo ContAxis/hcom
  hcom --version              # confirme (testado com 0.7.22)
  ```
- **hermes-agent** (Nous Research) — ver seção 3.
- **Telegram bot token**: crie um bot com [@BotFather](https://t.me/BotFather)
  (`/newbot`) e guarde o token `123456:ABC-DEF...`.
- (Opcional) `docker` + `docker compose` p/ rodar hermes em container.
- `openssl` (gerar `API_SERVER_KEY`), `curl`, `git`.

---

## 3. Setup do hermes-agent local (dev)

### 3a. Instalação nativa (Linux/macOS)

```bash
# instalador oficial
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# clone + deps em modo dev (editable com extras)
git clone https://github.com/nousresearch/hermes-agent.git ~/hermes-agent
cd ~/hermes-agent
uv pip install -e ".[all,dev]"

# config base
cp cli-config.yaml.example ~/.hermes/config.yaml
mkdir -p ~/.hermes/{cron,sessions,logs,memories,skills,plugins}
```

### 3b. Docker (alternativa)

```bash
# setup inicial (cria ~/.hermes)
docker run -it --rm -v ~/.hermes:/opt/data nousresearch/hermes-agent setup

# gateway em background
docker run -d --name hermes --restart unless-stopped \
  -v ~/.hermes:/opt/data -p 8642:8642 \
  nousresearch/hermes-agent gateway run
```

### 3c. Docker Compose (alternativa)

No dir do hermes-agent (ou onde estiver o `compose.yaml`):

```bash
HERMES_UID=$(id -u) HERMES_GID=$(id -g) docker compose up -d
```

> Confirme porta/imagem no `compose.yaml` do hermes-agent antes de subir
> (a doc oficial é a fonte de verdade — ver [OPEN_QUESTIONS.md](./OPEN_QUESTIONS.md)).

---

## 4. Config Telegram + multiuser

Crie/edit `~/.hermes/.env` (fora do git, `chmod 600`):

```bash
TELEGRAM_BOT_TOKEN=123456:ABC-DEF_your_token_from_BotFather
TELEGRAM_ALLOWED_USERS=123456789,987654321   # CSV de user IDs do Telegram
```

**Como descobrir seu user ID**: mande qualquer mensagem ao bot
[@userinfobot](https://t.me/userinfobot) — ele responde com seu `Id:` numérico.
Cada usuário autorizado deve fazer o mesmo; cole os IDs no CSV acima.

`TELEGRAM_ALLOWED_USERS` é a **allowlist** do gateway: só esses IDs conseguem
falar com o bot. Sem isso, qualquer um que achasse o @handle do bot poderia
comandar o enxame.

---

## 5. Habilitar a API OpenAI-compat do hermes

No `~/.hermes/.env`:

```bash
API_SERVER_ENABLED=true
API_SERVER_KEY=$(openssl rand -hex 32)   # gere e cole o valor; >= 8 chars
```

Para gerar e gravar de uma vez:

```bash
echo "API_SERVER_KEY=$(openssl rand -hex 32)" >> ~/.hermes/.env
```

Endpoint exposto (OpenAI-compat):

```
POST http://127.0.0.1:8642/v1/chat/completions
Authorization: Bearer <API_SERVER_KEY>
```

> A ponte v1 usa a tool `swarm` (shell-out pro `hcom`), não a API REST. A API
> fica habilitada para usos futuros / debugging / clients OpenAI-compat.

---

## 6. Registrar a tool `swarm` no hermes

A tool `swarm` é um **plugin Python** do hermes (ver [OPEN_QUESTIONS.md Q1](./OPEN_QUESTIONS.md#q1--mecanismo-exato-de-toolskill-custom-do-hermes)).
Estrutura esperada no repo da ponte:

```
plugins/swarm/
  plugin.yaml
  __init__.py      # def register(ctx): ctx.register_tool(name=..., schema=..., handler=..., description=...)
```

Instale no hermes:

```bash
# 1) instale o pacote da ponte NO MESMO ENV PYTHON DO HERMES — o plugin importa
#    `from hermes_hcom_bridge.tool_swarm import register`, então o pacote precisa
#    ser importável de dentro do processo do hermes.
#    (modo nativo: mesmo venv/uv env do hermes; docker: ver nota abaixo)
pip install -e .                  # no env do hermes

# 2) copie o plugin para o diretório de descoberta do hermes
cp -r plugins/swarm ~/.hermes/plugins/swarm

# 3) habilite (uma das duas formas)
hermes plugins enable swarm
# OU edite ~/.hermes/config.yaml e adicione `swarm` em plugins.enabled:
#   plugins:
#     enabled:
#       - swarm

# reinicie o gateway p/ carregar
docker restart hermes            # se docker
# ou mate e suba o `gateway run` novamente no modo nativo

# confirme que carregou
hermes plugins list              # swarm deve aparecer como enabled/loaded
```

> **Docker**: se o hermes roda em container, o pacote `hermes_hcom_bridge` e o
> plugin precisam estar visíveis dentro do container. Monte o repo da ponte como
> volume (`-v /path/hermes-hcom-bridge:/opt/bridge`) e instale-o no env do
> container, ou build uma imagem custom do hermes com a ponte embutida. O
> `~/.hermes/plugins` já é montado em `/opt/data/plugins` pelo volume `~/.hermes`.

A tool expõe: `swarm_list`, `swarm_send`, `swarm_spawn`, `swarm_kill`
(assinaturas em `hermes_hcom_bridge/tool_swarm.py` e `plugins/swarm/`).

---

## 7. Multiuser admin-tiering (spawn/kill restrito)

`swarm_spawn` e `swarm_kill` são destrutivos/custosos — não devem ser acessíveis
a qualquer usuário da allowlist. Defina os **admin IDs** no `.env` **da ponte**
(não no `~/.hermes/.env`):

```bash
# no <repo>/.env da ponte (veja seção 8)
HERMES_ADMIN_USERS=123456789          # CSV, subconjunto de TELEGRAM_ALLOWED_USERS
```

Só esses IDs podem disparar `swarm_spawn`/`swarm_kill` via Telegram. Usuários
comuns ficam restritos a `swarm_list`/`swarm_send`.

---

## 8. Subir a ponte

No repo `hermes-hcom-bridge`:

```bash
# deps da ponte (FastAPI, uvicorn, pydantic, pytest, httpx)
pip install -e ".[dev]"

# config da ponte
cp .env.example .env
$EDITOR .env      # preencha (ver abaixo)
```

Variáveis do `.env` da ponte (nomes reais do `.env.example`):

| Variável | O que é |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Mesmo token do `~/.hermes/.env` (a ponte precisa p/ `hermes send`). |
| `TELEGRAM_ALLOWED_USERS` | CSV de user IDs autorizados (espelha o hermes). |
| `API_SERVER_ENABLED` | `true` (espelha o hermes). |
| `API_SERVER_KEY` | Mesma chave do hermes. |
| `HERMES_FORWARD_TELEGRAM_CHAT_ID` | Chat ID admin p/ onde o forwarder manda proativas (ex: `-1001234567890`). |
| `HERMES_FORWARD_TO` | Destino no formato `hermes send --to` (default: `telegram:<chat_id>`). |
| `HERMES_FORWARD_POLL_INTERVAL` | Intervalo de polling do `hcom events` (seg). Default `2`. |
| `HCOM_PATH` | Binário hcom (default `hcom` do PATH). |
| `HERMES_PATH` | Binário hermes (default `hermes` do PATH). |
| `KANBAN_PORT` | Porta do kanban FastAPI. Default `8643`. |
| `KANBAN_HOST` | Host do kanban. Default `127.0.0.1` (POST /spawn e /kill sem auth — só exponha via reverse proxy com auth). |
| `HERMES_ADMIN_USERS` | CSV de user IDs admin (podem spawn/kill). |

Suba os dois processos (terminais separados ou supervisor/tmux):

```bash
# kanban web (FastAPI + HTML vanilla, read-only com ações)
python -m hermes_hcom_bridge.kanban.app        # default http://localhost:8643

# forwarder (hcom events -> Telegram via `hermes send`)
python -m hermes_hcom_bridge.forwarder
```

> O `hcom_client.py` é o único ponto que fala com o CLI `hcom` (subprocess, args
> em lista, timeout, parse JSON). Tudo na ponte passa por ele.

---

## 9. Validar ponta-a-ponta (dev local)

Com hermes gateway + ponte + hcom no ar:

1. **swarm_list responde no Telegram**: mande `status` ao bot → a tool `swarm`
   roda `hcom list --json` e devolve o estado do enxame.
2. **swarm_send chega no agente hcom**: mande `manda o vino rodar os testes` →
   `swarm_send` executa `hcom send @vino --intent request -- "..."` e o agente
   `vino` recebe.
3. **Kanban reflete o hcom**: abra `http://localhost:8643` → colunas
   listening/active/blocked/done espelham `hcom list` (poll ~2s).
4. **Forwarder avisa bloqueio**: deixe um agente bloqueado (sem `--go` ou
   esperando approval) → o forwarder detecta `--status blocked` em
   `hcom events` e manda `hermes send --to telegram:<chat_id>` ao admin.

Se qualquer passo falhar, vá para a seção 10.

---

## 10. Troubleshooting

| Sintoma | Causa provável | Fix |
|---|---|---|
| `hcom não encontrado em 'hcom'` (`HcomError`) | `hcom` fora do PATH do processo. | `which hcom`; defina `HCOM_PATH=/camino/abs/hcom` no `.env` da ponte. |
| `hermes send` falha / bot não responde | `TELEGRAM_BOT_TOKEN` errado/vazio. | Confira token no `~/.hermes/.env` e no `.env` da ponte (devem ser iguais). |
| Usuário fala e o bot ignora | `TELEGRAM_ALLOWED_USERS` não contém o user ID. | Descubra o ID via @userinfobot e adicione ao CSV. |
| `swarm_*` não aparece como tool | Plugin `swarm` não carregou. | `hermes plugins list`; confirme `swarm` em `plugins.enabled` do `~/.hermes/config.yaml`; reinicie o gateway. |
| Kanban vazio / `GET /agents` 500 | `hcom list --json` falhando ou sem agentes vivos. | Rode `hcom list --json` manualmente; suba um agente (`hcom 1 devin --headless ...`). |
| Forwarder não entrega proativas | `HERMES_FORWARD_TO`/`HERMES_FORWARD_TELEGRAM_CHAT_ID` errados. | Valide o chat ID (grupos começam com `-100...`); teste `hermes send --to telegram:<chat_id> "ping"` na mão. |
| `API_SERVER_KEY` rejeitado | Chave divergente entre hermes e ponte. | Use o mesmo valor em ambos `.env`. |
| Porta 8642/8643 ocupada | Outro processo. | Troque `KANBAN_PORT`; confirme porta do gateway no `compose.yaml`. |

---

## 11. OCI (alvo final) — PENDENTE / validar no fim

> **Status: PENDENTE.** Não mutar nada no OCI sem necessidade. Validar acesso
> primeiro; deixar por último (SPEC: não travar nisso).

Host alvo: `100.87.96.23` (Tailscale ativo no OCI). `ssh mmarsz@100.87.96.23`
deu **timeout** deste host — Tailscale está UP, então é problema de **credencial
SSH local**, não de rede.

Checklist (a executar quando for a vez do OCI):

1. **Validar credencial SSH** antes de qualquer passo:
   ```bash
   ssh -v mmarsz@100.87.96.23            # ver onde trava
   ssh-add -l                             # chaves carregadas?
   tailscale status | grep 100.87.96.23   # host visível no tailnet?
   ```
   Se necessário: `ssh-copy-id mmarsz@100.87.96.23` ou corrigir
   `~/.ssh/known_hosts`/chave.
2. **Descobrir como o hermes roda lá** (provável: docker compose com gateway na
   `8642`):
   ```bash
   ssh mmarsz@100.87.96.23 'docker ps --format "{{.Names}}\t{{.Ports}}" | grep -i hermes'
   ssh mmarsz@100.87.96.23 'cat ~/.hermes/config.yaml 2>/dev/null | head'
   ssh mmarsz@100.87.96.23 'ss -ltnp | grep -E 8642'   # porta do gateway
   ```
3. **Confirmar Telegram já configurado** no hermes do OCI (token + allowlist no
   `~/.hermes/.env` de lá).
4. **Apontar a ponte** ao hermes do OCI — no `.env` da ponte (que roda deste
   host de dev, ou no OCI conforme decisão):
   ```bash
   HERMES_FORWARD_TO=telegram:<chat_id_do_admin_no_OCI>
   KANBAN_PORT=8643        # ou outra livre no OCI
   # se a ponte rodar no OCI: HERMES_PATH=hermes local dali
   ```
5. **Repetir a validação ponta-a-ponta da seção 9** contra o hermes do OCI.

Tudo aqui só depois de credencial SSH confirmada. Registrar descobertas no hcom.

---

## 11. Deploy REAL no OCI (executado em 2026-06-30)

> Esta seção SOBREPÕE a seção 10 — é o que foi realmente feito e validado.
> SSH: `mmarsz@instance-20260223-1326` (hostname Tailscale; o IP `100.87.96.23`
> dá timeout — use o hostname). OCI = Ubuntu 24.04 aarch64.

### 11.1 Realidade do hermes no OCI (verificada, não assumida)

- Instalação **nativa** (venv): `~/.hermes/hermes-agent/venv/bin/hermes`.
  Gateway vivo (pid em `~/.hermes/gateway.pid`), webui vivo em `:8787`.
- **Telegram + multiuser JÁ configurados** em `~/.hermes/.env`
  (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`, `TELEGRAM_HOME_CHANNEL`).
  **Não reconfigure do zero.**
- **PLUGINS PYTHON são suportados** (`hermes plugins install/list/enable/disable`).
  `PluginManager` descobre de `~/.hermes/plugins/`, `./.hermes/plugins/` e pip
  entry-points. Cada plugin: `plugin.yaml` + `__init__.py` com `register(ctx)`;
  `ctx.register_tool(name, toolset, schema, handler, description)`. Handler
  retorna **string JSON**. → confirma Q1 do OPEN_QUESTIONS (kite acertou plugin).
- O gateway carrega plugins **por sessão** (`gateway/run.py` chama
  `discover_plugins()` por sessão) → plugin novo entra **sem reiniciar o gateway**.
- `hermes send --to telegram:<chat>` vivo (reusa creds do gateway, sem LLM).
- `hermes kanban` nativo existe (SQLite task board) — conceito diferente do
  nosso (status de agentes hcom); nosso kanban standalone segue.

### 11.2 Rede OCI ↔ local: hcom relay + mosquitto (CRÍTICO)

O enxame hcom (orq/vino/…) roda no **momoko** (local, x86_64); o hermes roda no
**OCI** (aarch64). O plugin no OCI chama `hcom` via subprocess — precisa alcançar
o bus local. Solução: **hcom relay** (sync cross-device via broker MQTT).

Brokers MQTT públicos (emqx/hivemq/mosquitto) estão **inalcançáveis** do momoko
(egress bloqueado). Rodamos um broker **mosquitto no OCI**, bound ao IP Tailscale
`100.87.96.23:1883` (plain `mqtt://` + password auth; só Tailscale enxerga).

```bash
# no OCI (sudo passwordless disponível):
sudo apt-get install -y mosquitto mosquitto-clients
HCOM_PASS=$(openssl rand -base64 18 | tr -d '/+=' | head -c 22)
echo "$HCOM_PASS" > ~/.hcom_broker_pass; chmod 600 ~/.hcom_broker_pass   # NÃO commitar
sudo mosquitto_passwd -c -b /etc/mosquitto/hcom.passwd hcom "$HCOM_PASS"
sudo tee /etc/mosquitto/conf.d/hcom.conf >/dev/null <<'CONF'
listener 1883 100.87.96.23
allow_anonymous false
password_file /etc/mosquitto/hcom.passwd
log_dest stdout
log_type error
log_type warning
log_type notice
CONF
sudo systemctl reset-failed mosquitto && sudo systemctl restart mosquitto
sudo ss -tlnp | grep 1883   # confirme listening no IP Tailscale
```

Instalar hcom no OCI (prebuilt aarch64 — sem cargo):
```bash
curl -fsSL https://github.com/aannoo/hcom/releases/latest/download/hcom-installer.sh | sh
# instala em ~/.local/bin/hcom
```

Criar o grupo relay no **momoko** (local) apontando ao broker OCI e conectar no OCI:
```bash
# momoko (local):
HCOM_PASS=$(ssh mmarsz@instance-20260223-1326 'cat ~/.hcom_broker_pass')
hcom relay new --broker mqtt://100.87.96.23:1883 --password "$HCOM_PASS"
# → anote o token impresso
hcom relay daemon start

# OCI:
TOKEN=<token_impresso_acima>
~/.local/bin/hcom start --as bridge          # registra identidade sender no OCI
~/.local/bin/hcom relay connect "$TOKEN" --password "$(cat ~/.hcom_broker_pass)"
~/.local/bin/hcom relay daemon start
```

Validação: `~/.local/bin/hcom list` no OCI deve ver os agentes locais marcados
`[remote]` com sufixo `:<NODE>` (ex.: `orq:FELE`). O node name do momoko aparece
como sufixo (aqui `FELE`); o do OCI como `DUSI`.

### 11.3 Sufixo de destino (HCOM_TARGET_SUFFIX)

No OCI, agentes remotos exigem o sufixo do node de origem: `@orq:FELE` (não
`@orq`). O `hcom_client` anexa `HCOM_TARGET_SUFFIX` (ex.: `:FELE`) a targets base
sem `:`; qualificados (`orq:FELE`) e `tag:T` ficam inalterados. Local (sem relay)
= no-op. Setado via env no `.env` do hermes (ver 11.4).

### 11.4 Deploy do plugin + daemons (aditivo, sem restart de gateway/webui)

```bash
# 1. Sincronizar o repo pro OCI (tar over ssh; repo é privado no GitHub):
cd ~/contaxis/hermes-hcom-bridge
tar czf - --exclude=.venv --exclude=.git --exclude='__pycache__' \
        --exclude='*.egg-info' --exclude=.pytest_cache . \
  | ssh mmarsz@instance-20260223-1326 'mkdir -p ~/contaxis/hermes-hcom-bridge && cd ~/contaxis/hermes-hcom-bridge && tar xzf -'

# 2. pip install -e no venv do hermes (deps já presentes: fastapi/uvicorn/pydantic/httpx):
ssh mmarsz@instance-20260223-1326 '~/.hermes/hermes-agent/venv/bin/pip install -e ~/contaxis/hermes-hcom-bridge --no-deps'

# 3. Copiar o plugin p/ ~/.hermes/plugins/swarm:
ssh mmarsz@instance-20260223-1326 'rm -rf ~/.hermes/plugins/swarm && cp -r ~/contaxis/hermes-hcom-bridge/plugins/swarm ~/.hermes/plugins/swarm'

# 4. Habilitar plugin + toolset (entra na próxima sessão, sem restartar gateway):
ssh mmarsz@instance-20260223-1326 '~/.hermes/hermes-agent/venv/bin/hermes plugins enable swarm'
ssh mmarsz@instance-20260223-1326 '~/.hermes/hermes-agent/venv/bin/hermes tools list --platform telegram | grep swarm'
#   → "✓ enabled  swarm  🔌 Swarm"

# 5. Env no ~/.hermes/.env do OCI (gateway recarrega .env por turno):
#   HERMES_ADMIN_USERS=<seu_telegram_user_id>   # p/ swarm_spawn/kill (admin tiering)
#   HCOM_PATH=/home/mmarsz/.local/bin/hcom
#   HCOM_TARGET_SUFFIX=:FELE                    # node name do momoko (ver 11.2)
ssh mmarsz@instance-20260223-1326 'cp ~/.hermes/.env ~/.hermes/.env.bak.$(date +%Y%m%d_%H%M%S)
  grep -q "^HERMES_ADMIN_USERS=" ~/.hermes/.env || echo "HERMES_ADMIN_USERS=7054724026" >> ~/.hermes/.env
  grep -q "^HCOM_PATH=" ~/.hermes/.env || echo "HCOM_PATH=/home/mmarsz/.local/bin/hcom" >> ~/.hermes/.env
  grep -q "^HCOM_TARGET_SUFFIX=" ~/.hermes/.env || echo "HCOM_TARGET_SUFFIX=:FELE" >> ~/.hermes/.env'
```

### 11.5 Daemons forwarder + kanban

O script `run/start.sh` (no repo, versionado) sobe ambos com `nohup` + pidfile +
log em `run/`. Idempotente. **Additivo — não toca gateway nem webui.**

```bash
ssh mmarsz@instance-20260223-1326 'bash ~/contaxis/hermes-hcom-bridge/run/start.sh'
# forwarder started (pid …) log=…/run/forwarder.log
# kanban    started (pid …) log=…/run/kanban.log
```

Env do script (ajuste em `run/start.sh`):
- `HERMES_FORWARD_TO=telegram:<TELEGRAM_HOME_CHANNEL>` (destino proativo)
- `HERMES_FORWARD_INTENTS=request` (só encaminha `request` + `blocked`; vazio = todas)
- `KANBAN_HOST=100.87.96.23` `KANBAN_PORT=8643` (Tailscale-only; sem auth — ver 11.7)
- `HCOM_PATH`, `HCOM_TARGET_SUFFIX`, `HERMES_PATH` (venv hermes)

Kanban: `http://100.87.96.23:8643/` (board) e `http://100.87.96.23:8643/agents` (JSON).

### 11.6 Validação ponta-a-ponta (executada, verde)

- **(c) kanban**: `curl http://100.87.96.23:8643/agents` → agentes do swarm
  agrupados por status (via relay). ✓
- **(a) Telegram→swarm→hcom**: validado via `hermes chat -q "use swarm_list …"`
  no OCI — o agente invocou `swarm_list`, reportou 18 agentes (ex.:
  `wave2-dosa:FELE`). O runtime do `hermes chat` é o mesmo do gateway Telegram
  (toolset `swarm` habilitado p/ telegram). O hop não-exercitado é a mensagem
  real do Telegram do Mario (ausente); o lado agente está provado. ✓
- **(b) agente→forwarder→Telegram**: mensagem hcom local marcada → relay →
  forwarder no OCI → `hermes send --to telegram:7054724026` → Telegram. Log
  `encaminhado <id> -> telegram:7054724026` (só após `hermes send` ok). ✓

### 11.7 Gaps conhecidos (v1 / ponytail debt)

- **Kanban sem auth**: `POST /spawn` e `/kill` expostos no IP Tailscale sem auth.
  Tailscale é a fronteira de confiança (nós do Mario), mas idealmente um reverse
  proxy com auth na frente. Bind `127.0.0.1` + tunnel SSH se for expor além do
  tailnet.
- **Daemons não sobrevivem reboot**: `nohup` + pidfile (não são systemd). O
  gateway/webui do OCI também rodam como processos plain (4+ dias up). NEXT:
  systemd --user units p/ forwarder/kanban/mosquitto (mosquitto já é systemd).
- **Forwarder encaminha `request` + `blocked`** (não tudo) para não spammar o
  Telegram com chatter `inform/ack` do swarm. Ajuste via `HERMES_FORWARD_INTENTS`.
- **Sufixo `:FELE` hardcoded no env**: se o node name do momoko mudar (novo
  relay group), atualizar `HCOM_TARGET_SUFFIX`.
