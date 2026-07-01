# ContAxis hcom Fork

Changelog do fork ContAxis do [aannoo/hcom](https://github.com/aannoo/hcom) (MIT).

## Tooling enxugado

Registry público (`integration_spec.rs::ALL`) reduzido para o enxame ContAxis:

**Mantidos (lançáveis via `hcom [N] <tool>`):** claude, codex, opencode, antigravity, cursor, devin.

**Removidos do código (specs + variants Tool/LaunchTool/model::Tool + módulos):** gemini, kilo, pi, kimi, copilot.
- `hcom <tool>` para qualquer um dos removidos retorna `Unknown command`.
- Help text, config `*_args`, branches de delivery e testes que referenciavam esses tools foram limpos.

**GEMINI** — spec mantida compilada com `released: false` porque o **Antigravity reutiliza `hooks::gemini`** (compartilham os hook commands `gemini-*`). Não está em `ALL`, não é lançável, não aparece no help. É dependência interna do Antigravity, não tool de usuário.

**HERMES** — não é uma tool do registry. A compat humana (Telegram + kanban) roda como sidecar via ponte vendorada em [`hermes-bridge/`](../hermes-bridge/) (mirror de `ContAxis/hermes-hcom-bridge`, tag `devin/bridge-v1`).

## Devin CLI first-class

`hcom devin` integrado ao registry com hooks em formato Claude (JSON stdin, eventos `devin-*`). Resume/fork headless suporta Claude e Devin (`--headless`).

## Cross-compilation

`dist.toml` (cargo-dist) com targets:
- Linux: `x86_64-unknown-linux-gnu`, `aarch64-unknown-linux-gnu`
- macOS: `x86_64-apple-darwin`, `aarch64-apple-darwin`

```bash
cargo build --release                                    # local
sudo dnf install gcc-aarch64-linux-gnu                   # Fedora (cross aarch64)
cargo build --release --target aarch64-unknown-linux-gnu
```

CI via cargo-dist em `.github/workflows/release.yml` — push de tag dispara build dos targets.

## Release

`v0.7.22-contaxis` publicado em [`mmarsz/hcom`](https://github.com/mmarsz/hcom/releases/tag/v0.7.22-contaxis): binários Linux amd64 + aarch64 (+ checksums). Sem binários macOS nem `installer.sh` ainda.

## Update checker

`hcom update` e o auto-checker apontam para `mmarsz/hcom` (não mais upstream `aannoo/hcom`) — evita que o fork detecte releases upstream como "update available" e tente instalar upstream por cima. O apply via `installer.sh` depende de asset ainda não empacotado (follow-up).

## Metadados

- `Cargo.toml`: homepage/repository → `ContAxis/hcom` (org canônica); código e releases ativos em `mmarsz/hcom`.
- Descrição: "ContAxis fork of hcom".
- Versão: 0.7.22.

## Follow-ups

- Espelhar release + branch `contaxis-consolidation` para `ContAxis/hcom` (ou aceitar `mmarsz/hcom` como canônico e alinhar `Cargo.toml`).
- Publicar binários macOS (targets já no `dist.toml`).
- Empacotar `hcom-installer.sh` no release para `hcom update` funcionar end-to-end.
