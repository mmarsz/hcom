# hcom (ContAxis fork)

> Barramento de mensagens do enxame multi-agente

Fork ContAxis do [hcom](https://github.com/aannoo/hcom) (original por aannoo, licença MIT). Otimizado para o enxame ContAxis: **Claude orquestra, devin-cli executa, hermes = porta humana via Telegram**.

Binário Rust único, sem serviços de fundo. Inicie um agente com `hcom` na frente, depois prompt normalmente.

> **Repo ativo:** código e releases vivem em [`mmarsz/hcom`](https://github.com/mmarsz/hcom) (branch `contaxis-consolidation`, release `v0.7.22-contaxis`). O repo da org [`ContAxis/hcom`](https://github.com/ContAxis/hcom) é a página-canônica do fork.

---

## Instalação

### A partir do código-fonte (recomendado)

```bash
git clone https://github.com/mmarsz/hcom.git
cd hcom
git checkout contaxis-consolidation
cargo build --release
ln -sf $(pwd)/target/release/hcom ~/.cargo/bin/hcom
```

Requer Rust 1.88+ (`rustup default stable`).

### Binários pré-compilados (Linux)

Release `v0.7.22-contaxis` traz `hcom-x86_64-unknown-linux-gnu` e `hcom-aarch64-unknown-linux-gnu` (+ checksums):

```bash
# amd64
curl -fsSL https://github.com/mmarsz/hcom/releases/latest/download/hcom-x86_64-unknown-linux-gnu -o ~/.cargo/bin/hcom
chmod +x ~/.cargo/bin/hcom
# aarch64: troque o nome do asset por hcom-aarch64-unknown-linux-gnu
```

Cross-compilation para macOS (x86_64/aarch64) é suportada via `dist.toml`, mas ainda sem binários publicados — build from source nesses alvos.

---

## Onboarding do enxame ContAxis

### 1. Subir agente

Terminal 1 (orquestrador):

```bash
hcom claude --name claude-1
```

Terminal 2 (executor, headless):

```bash
hcom devin --name devin-1 --headless
```

**Sempre use `--name <nome>`** pra identificar agentes. Gotchas de spawn:
- **Claude exige `--go`** no spawn (mostra `LAUNCH PREVIEW`, gate anti-loop).
- opencode/antigravity/devin sobem direto.
- **Modelo só no spawn** — sem hot-swap; trocar = `kill` + respawn com `--model`.

### 2. Enviar mensagens entre agentes

```bash
hcom send @devin-1 --intent request --name claude-1 -- "implemente a feature X"
```

Intents:
- `request` = quero resposta (sempre respondem)
- `inform` = só aviso (respondem se útil)
- `ack` = confirmação (não respondem)

Para código/markdown, troque `--` por `--file <path>` ou heredoc (crases quebram o parser).

### 3. Observar agentes

```bash
hcom list -v                          # o quê agora
hcom events --last 10                 # histórico recente
hcom transcript claude-1 --last 20    # raciocínio
hcom term devin-1                     # tela crua do terminal
```

### 4. Relay cross-device via Tailscale

Para conectar agentes entre máquinas (ex: OCI ↔ vps-prod sobre Tailscale):

```bash
# Máquina 1 — cria o grupo e pega o token
hcom relay new
hcom relay connect <token>

# Máquina 2 — entra no mesmo grupo
hcom relay connect <token>

# Status
hcom relay status
```

O relay sincroniza mensagens e eventos entre instâncias hcom em hosts diferentes. Broker MQTT default; `hcom relay new --broker mqtts://host:port --password <secret>` usa broker próprio.

### 5. Ponte Hermes (porta humana)

O fork inclui a ponte vendorada em [`hermes-bridge/`](hermes-bridge/) que liga o hcom ao [hermes-agent](https://github.com/nousresearch/hermes-agent) — interface humana via Telegram + kanban web. **Hermes não é uma tool do registry hcom** (não se faz `hcom hermes`); roda como sidecar separado.

```
Humano ──Telegram──▶ Hermes ──▶ [hermes-bridge] ──▶ hcom ──▶ Claude (orquestra) ──▶ devin-cli (executa)
                       ▲                                                     │
Humano ◀──Telegram── Hermes ◀── forwarder (lê hcom events) ◀──────────────────┘
```

Setup e configuração: veja [`hermes-bridge/`](hermes-bridge/) (ponte vendorada de `ContAxis/hermes-hcom-bridge`, tag `devin/bridge-v1`).

### 6. TUI dashboard

```bash
hcom
```

Abre interface interativa pra listar, observar e gerenciar agentes em tempo real.

---

## Comandos essenciais

### Mensagens

```bash
hcom send @nome [@tag|@all] --intent request|inform|ack [--reply-to <id>] [--thread <nome>] --name X -- "texto"
```

### Observação

```bash
hcom list -v                    # agentes ativos
hcom events --last N            # histórico
hcom events sub [filtros]       # stream contínuo
hcom transcript <nome>          # raciocínio do agente
hcom term <nome>                # tela crua
```

### Gerenciamento

```bash
hcom [N] <tool> [--model opus|sonnet|...] [--headless] [--tag T] [--dir P] [--hcom-prompt "..."]
hcom r <nome>                   # resume sessão
hcom f <nome>                   # fork sessão
hcom kill <nome>|tag:T|all      # matar agentes
```

Tools no registry: **claude, codex, opencode, antigravity, cursor, devin**.

---

## Como funciona

Hooks gravam atividade em SQLite local e entregam mensagens:

```
agente → hooks → db → hooks → outro agente
```

Mensagens chegam mid-turn (injetadas entre tool calls) ou acordam agentes idle imediatamente. Cada agente tem identidade consultável: nome, status (active/blocked/listening), inbox, tela de terminal ao vivo, transcript em chunks estruturados e log de eventos.

Hooks vão em config dirs sob `~/` (ou `HCOM_DIR`) no primeiro run. Sem hooks, qualquer AI tool pode entrar rodando `hcom start`.

---

## Troubleshooting

```bash
hcom status                  # diagnósticos
hcom reset all               # limpa e arquiva: database + hooks + config
```

---

## Desinstalação

```bash
hcom hooks remove            # remove hooks com segurança
rm $(which hcom)
```

---

## License

[MIT](LICENSE) — original por [aannoo/hcom](https://github.com/aannoo/hcom), fork ContAxis.
