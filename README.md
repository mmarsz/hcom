# hcom (ContAxis fork)

> Barramento de mensagens do enxame multi-agente

Fork ContAxis do [hcom](https://github.com/aannoo/hcom) (original por aannoo, licença MIT). Este fork é otimizado para o enxame ContAxis: **Claude orquestra, devin-cli executa, hermes = porta humana**.

Binário Rust único, sem serviços de fundo. Inicie um agente com `hcom` na frente, depois prompt normalmente.

---

## Onboarding

### Subir agente

```bash
hcom claude   # codex / opencode / antigravity / cursor-agent / kimi / copilot / devin
```

### Relay cross-device (Tailscale)

```bash
hcom relay new               # gera token
hcom relay connect <token>   # em cada dispositivo
```

### Ponte Hermes (porta humana)

O fork ContAxis inclui integração com [hermes-agent](https://github.com/nousresearch/hermes-agent) via [hermes-hcom-bridge](https://github.com/ContAxis/hermes-hcom-bridge):

```
Humano ──Telegram──▶ Hermes Gateway ──▶ [ponte] ──▶ hcom ──▶ Claude (orquestra) ──▶ devin-cli (executa)
                          ▲                                                    │
Humano ◀──Telegram──── Hermes ◀──── forwarder (lê hcom events) ◀─────────────────┘
                          ▲
                    Kanban web (estado dos agentes hcom, sem tmux)
```

Veja [hermes-bridge/README.md](./hermes-bridge/README.md) para setup.

---

## Comandos básicos

### Mensagem

```bash
hcom send @nome [@tag|@all] --intent request|inform|ack [--reply-to <id>] [--thread <nome>] --name X -- "texto"
```

- `request` = quero resposta (sempre respondem)
- `inform` = só aviso (respondem se útil)
- `ack` = confirmação (não respondem)

Código/markdown → use `--file <path>` ou heredoc (crases quebram).

### Observar

```bash
hcom list -v                    # o quê agora
hcom events --last N            # stream/histórico
hcom events --agent X           # filtra por agente
hcom events --type message      # filtra por tipo
hcom events --status blocked    # filtra por status
hcom transcript <nome>          # raciocínio
hcom term <nome>                # tela crua
```

### Gerenciar

```bash
hcom [N] <tool> [--model opus|sonnet|haiku] [--headless] [--tag T] [--dir P] [--hcom-prompt "..."] [--hcom-system-prompt "..."]
hcom r <nome>                   # resume
hcom f <nome>                   # fork
hcom kill <nome>|tag:T|all      # matar
```

### Gotchas

- **Spawn de claude exige `--go`** (mostra `LAUNCH PREVIEW`, gate anti-loop)
- opencode/antigravity sobem direto
- **Modelo só no spawn** — não há hot-swap; trocar = `kill` + respawn
- headless = sem janela, mas consome tokens → `hcom kill tag:T` ao terminar

---

## Como funciona

Hooks gravam atividade em SQLite local e entregam mensagens:

```
agent → hooks → db → hooks → other agent
```

Mensagens chegam mid-turn (injetadas entre tool calls) ou acordam agentes idle imediatamente.

Cada agente tem identidade consultável:
- name
- status (active, blocked, listening)
- inbox
- live terminal screen
- transcript em chunks estruturados
- event log de toda mudança de status, file edit, tool call

Hooks vão em config dirs sob `~/` (ou `HCOM_DIR`) no primeiro run. Sem hooks, qualquer AI tool pode entrar rodando `hcom start`.

---

## Terminal

Todo agente roda em terminal real que você pode ver, scroll e interromper. Qualquer emulator funciona para spawn; **kitty**, **wezterm**, **tmux**, **zellij**, **waveterm**, **cmux**, **herdr** também suportam fechar panes via `hcom kill`.

Configurar terminal custom:

```bash
hcom config terminal --info
```

---

## Cross-device

Conecte agentes via MQTT relay:

```bash
hcom relay new               # gera token
hcom relay connect <token>   # em cada dispositivo
hcom relay status            # checa conexão
hcom relay off|on            # toggle
```

---

## Troubleshoot

```bash
hcom status                  # diagnósticos
hcom reset all               # limpa e arquiva: database + hooks + config
```

---

## Uninstall

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
