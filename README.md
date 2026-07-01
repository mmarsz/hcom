# hcom (ContAxis fork)

> Barramento de mensagens do enxame multi-agente

Fork ContAxis do [hcom](https://github.com/aannoo/hcom) (original por aannoo, licença MIT). Este fork é otimizado para o enxame ContAxis: **Claude orquestra, devin-cli executa, hermes = porta humana**.

Binário Rust único, sem serviços de fundo. Inicie um agente com `hcom` na frente, depois prompt normalmente.

---

## Instalação

### A partir do código-fonte (fork ContAxis)

```bash
git clone https://github.com/ContAxis/hcom.git
cd hcom
cargo build --release
ln -sf $(pwd)/target/release/hcom ~/.cargo/bin/hcom
```

Alvos de cross-compilation: x86_64/aarch64 para Linux e macOS (veja `dist.toml`).

### A partir de releases

```bash
brew install contaxis/hcom/hcom
```

<details><summary>Outras opções de instalação</summary>

```bash
# Instalador shell para macOS, Linux, Android (Termux) e WSL
curl -fsSL https://github.com/ContAxis/hcom/releases/latest/download/hcom-installer.sh | sh
```

```bash
# Via PyPI
uv tool install hcom  # ou: pip install hcom
```

```bash
# Atualizar instalação existente
hcom update
```

</details>

---

## Onboarding do enxame ContAxis

### 1. Subir agente

Terminal 1:

```bash
hcom claude --name claude-1
```

Terminal 2:

```bash
hcom devin --name devin-1 --headless
```

**Nota importante:** Sempre use `--name <nome>` para identificar agentes. Claude exige `--go` no spawn (mostra `LAUNCH PREVIEW`), opencode/antigravity sobem direto.

### 2. Enviar mensagens entre agentes

```bash
hcom send @devin-1 --intent request --name claude-1 -- "implemente a feature X"
```

Intents:
- `request` = quero resposta (sempre respondem)
- `inform` = só aviso (respondem se útil)
- `ack` = confirmação (não respondem)

### 3. Observar agentes

```bash
hcom list -v                          # o quê agora
hcom events --last 10                 # histórico recente
hcom transcript claude-1 --last 20    # raciocínio
hcom term devin-1                     # tela crua do terminal
```

### 4. Relay cross-device via Tailscale

Para conectar agentes entre máquinas (ex: OCI ↔ vps-prod):

```bash
# Máquina 1
hcom relay new               # gera token
hcom relay connect <token>   # conecta

# Máquina 2
hcom relay connect <token>   # mesmo token

# Verificar
hcom relay status
```

### 5. Ponte Hermes (porta humana)

O fork ContAxis inclui integração com [hermes-agent](https://github.com/nousresearch/hermes-agent) para interfaces humanas (Telegram, web UI). Hermes **não** é integrado ao registry hcom — roda como serviço separado via [hermes-hcom-bridge](https://github.com/ContAxis/hermes-hcom-bridge).

```
Humano ──Telegram──▶ Hermes Gateway ──▶ [ponte] ──▶ hcom ──▶ Claude (orquestra) ──▶ devin-cli (executa)
                          ▲                                                    │
Humano ◀──Telegram──── Hermes ◀──── forwarder (lê hcom events) ◀─────────────────┘
                          ▲
                    Kanban web (estado dos agentes hcom)
```

Veja [hermes-hcom-bridge](https://github.com/ContAxis/hermes-hcom-bridge) para setup e documentação.

### 6. TUI dashboard

```bash
hcom
```

Abre interface interativa para listar, observar e gerenciar agentes.

---

## Comandos essenciais

### Mensagens

```bash
hcom send @nome [@tag|@all] --intent request|inform|ack --name X -- "texto"
```

Para código/markdown, use `--file <path>` em vez de `--`.

### Observação

```bash
hcom list -v                    # agentes ativos
hcom events --last N            # histórico recente
hcom transcript <nome>          # raciocínio do agente
hcom term <nome>                # tela crua do terminal
```

### Gerenciamento

```bash
hcom [N] <tool> [--headless] [--tag T] [--dir P] [--hcom-prompt "..."]
hcom r <nome>                   # resume sessão
hcom f <nome>                   # fork sessão
hcom kill <nome>|tag:T|all      # matar agentes
```

### Gotchas importantes

- **Claude exige `--go`** no spawn (gate anti-loop)
- opencode/antigravity sobem direto
- **Modelo só no spawn** — não há hot-swap; trocar = `kill` + respawn
- headless consome tokens → `hcom kill tag:T` ao terminar

---

## Como funciona

Hooks gravam atividade em SQLite local e entregam mensagens:

```
agente → hooks → db → hooks → outro agente
```

Mensagens chegam mid-turn (injetadas entre tool calls) ou acordam agentes idle imediatamente.

Cada agente tem identidade consultável:
- nome
- status (active, blocked, listening)
- inbox
- tela de terminal ao vivo
- transcript em chunks estruturados
- log de eventos de toda mudança de status, edição, tool call

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
rm $(which hcom)             # ou: brew uninstall hcom
```

---

## Build from source

```bash
git clone https://github.com/ContAxis/hcom.git
cd hcom
cargo build --release
ln -sf $(pwd)/target/release/hcom ~/.cargo/bin/hcom
```

Cross-compilation targets (veja `dist.toml`):
- Linux: x86_64-unknown-linux-gnu, aarch64-unknown-linux-gnu
- macOS: x86_64-apple-darwin, aarch64-apple-darwin

```bash
# Cross-compile aarch64-linux (requer toolchain)
sudo dnf install gcc-aarch64-linux-gnu  # Fedora
# ou
sudo apt install gcc-aarch64-linux-gnu  # Ubuntu
cargo build --release --target aarch64-unknown-linux-gnu
```

---

## License

[MIT](LICENSE) — original por [aannoo/hcom](https://github.com/aannoo/hcom), fork ContAxis.
