//! Devin CLI native hook handlers and hooks config management.
//!
//! Devin CLI (https://windsurf.com/support) uses a hook format that is
//! **compatible with Claude Code hooks**: JSON on stdin, PascalCase event
//! names (`PreToolUse`, `PostToolUse`, `SessionStart`, …), and
//! `additionalContext` / `decision` JSON on stdout for delivery and stop
//! control. See the Devin CLI docs at
//! `~/.local/share/devin/cli/_versions/<ver>/share/devin/docs/extensibility/hooks/`.
//!
//! Config locations (Devin reads all of these, Claude-format compatible):
//!   - `~/.config/devin/config.json`        (user, `"hooks"` key)
//!   - `~/.config/devin/hooks.v1.json`      (user, standalone — whole file is hooks)
//!   - `.devin/config.json`                 (project, `"hooks"` key)
//!   - `.devin/hooks.v1.json`               (project, standalone)
//!   - `.claude/settings.json`              (Claude-format, when read_config_from.claude is on — default)
//!
//! hcom writes to `~/.config/devin/config.json` (user) by default, and to
//! `{project_root}/.devin/config.json` when `HCOM_DIR` is set, mirroring the
//! Claude integration's project/global split. We pick the standalone
//! `hooks.v1.json` form when the file already exists in that form, otherwise
//! the `"hooks"` key inside `config.json`. To keep the install path simple
//! and idempotent, we always write the `"hooks"`-key form into `config.json`
//! and let Devin merge it with any standalone file.

use std::io::Write;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use crate::db::{HcomDb, InstanceRow};
use crate::hooks::{DeliveryAck, HookPayload, common};
use crate::instance_binding;
use crate::instance_lifecycle as lifecycle;
use crate::instances;
use crate::log;
use crate::paths;
use crate::shared::context::HcomContext;
use crate::shared::{ST_ACTIVE, ST_LISTENING};

const HCOM_TRIGGER: &str = "<hcom>";
const HOOK_TIMEOUT_SECS: u64 = 15;

/// (Devin event name, hcom subcommand suffix, permissions-only, optional matcher).
///
/// Devin fires the same PascalCase events as Claude Code. We register one
/// hcom hook command per event. `PermissionRequest` is permissions-only
/// (mirrors copilot/claude). `PostCompaction` re-injects bootstrap after
/// context compaction (Devin fires it like Claude's `Stop`-adjacent event).
const DEVIN_HOOK_COMMANDS: &[(&str, &str, bool, Option<&str>)] = &[
    ("SessionStart", "devin-sessionstart", false, None),
    ("UserPromptSubmit", "devin-userpromptsubmit", false, None),
    ("PreToolUse", "devin-pretooluse", false, None),
    ("PermissionRequest", "devin-permissionrequest", true, None),
    ("PostToolUse", "devin-posttooluse", false, None),
    ("Stop", "devin-stop", false, None),
    ("PostCompaction", "devin-postcompaction", false, None),
    ("SessionEnd", "devin-sessionend", false, None),
];

#[derive(Debug, thiserror::Error)]
pub enum SetupError {
    #[error("existing Devin config at {} could not be read: {source}", path.display())]
    ExistingReadFailed {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("existing Devin config at {} is not valid JSON: {source}", path.display())]
    ExistingParseFailed {
        path: PathBuf,
        #[source]
        source: serde_json::Error,
    },
    #[error("existing Devin config at {} must be a JSON object", path.display())]
    ExistingRootNotObject { path: PathBuf },
    #[error("failed to create Devin config directory {}: {source}", path.display())]
    DirCreateFailed {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("JSON serialization failed: {0}")]
    SerializationFailed(#[from] serde_json::Error),
    #[error("atomic write to {} failed: {source}", path.display())]
    AtomicWriteFailed {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("post-write Devin hook verification failed for {}", .0.display())]
    PostWriteVerifyFailed(PathBuf),
}

/// Resolve the Devin user config directory.
///
/// Honors `DEVIN_CONFIG_DIR` (hcom-only override, mirrors the Claude
/// `CLAUDE_CONFIG_DIR` pattern) then falls back to the platform config dir:
///   - Unix: `~/.config/devin`
///   - Windows: `%APPDATA%\devin`
fn devin_config_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("DEVIN_CONFIG_DIR")
        && !dir.is_empty()
    {
        return PathBuf::from(dir);
    }
    if let Some(cfg) = dirs::config_dir() {
        return cfg.join("devin");
    }
    crate::runtime_env::tool_config_root().join(".devin")
}

/// Project-local Devin config dir: `{project_root}/.devin` when `HCOM_DIR`
/// is set (mirrors the Claude integration's project/global split).
fn devin_project_config_dir() -> Option<PathBuf> {
    let env: std::collections::HashMap<String, String> = std::env::vars().collect();
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let (hcom_dir, override_set) = crate::paths::resolve_hcom_dir_from_env(&env, &cwd);
    if !override_set {
        return None;
    }
    let project_root = hcom_dir.parent()?;
    Some(project_root.join(".devin"))
}

/// Pick the config file hcom writes hooks into.
///
/// Preference order:
/// 1. Project-local `.devin/config.json` if `HCOM_DIR` is set (project isolation).
/// 2. User `~/.config/devin/config.json` (or `DEVIN_CONFIG_DIR` override).
///
/// We write the `"hooks"` key inside `config.json` rather than the standalone
/// `hooks.v1.json` form so the install is idempotent and coexists with any
/// user-authored standalone hooks file (Devin merges both).
pub fn get_devin_settings_path() -> PathBuf {
    if let Some(proj) = devin_project_config_dir() {
        return proj.join("config.json");
    }
    devin_config_dir().join("config.json")
}

fn build_devin_hook_command(command: &str) -> String {
    // Mirror Claude's `${HCOM:-hcom}` resilience: if hcom is not on PATH the
    // hook exits 0 instead of surfacing a "command not found" error inside
    // Devin. Devin executes hook commands through a shell, so inline shell
    // logic is safe here (same posture as `build_hook_entry_command` in
    // claude.rs).
    format!(
        "cmd=${{HCOM:-hcom}}; command -v \"${{cmd%% *}}\" >/dev/null 2>&1 && exec $cmd {} || exit 0",
        command
    )
}

fn is_hcom_devin_command(command: &str) -> bool {
    DEVIN_HOOK_COMMANDS
        .iter()
        .any(|(_, suffix, _, _)| command == build_devin_hook_command(suffix) || command.ends_with(suffix))
}

/// Build one hooks-array entry in Devin/Claude's nested group shape:
/// `{matcher?, hooks:[{type, command, timeout}]}`. Devin rejects the flat
/// `{type, command}` form with "missing field hooks", so the inner command
/// must be wrapped in a group.
fn expected_hook(command: &str, matcher: Option<&str>) -> Value {
    let inner = json!({
        "type": "command",
        "command": build_devin_hook_command(command),
        "timeout": HOOK_TIMEOUT_SECS,
    });
    let mut group = serde_json::Map::new();
    if let Some(matcher) = matcher {
        group.insert("matcher".to_string(), Value::String(matcher.to_string()));
    }
    group.insert("hooks".to_string(), json!([inner]));
    Value::Object(group)
}

/// True if a hooks-array entry is hcom-owned. Handles the nested group shape
/// `{matcher?, hooks:[{command}]}` (current) and the legacy flat shape
/// `{command}` (so a previously-broken flat install is cleaned up on re-run).
fn entry_is_hcom(entry: &Value) -> bool {
    if entry
        .get("command")
        .and_then(Value::as_str)
        .is_some_and(is_hcom_devin_command)
    {
        return true;
    }
    entry
        .get("hooks")
        .and_then(Value::as_array)
        .is_some_and(|inner| {
            inner.iter().any(|h| {
                h.get("command")
                    .and_then(Value::as_str)
                    .is_some_and(is_hcom_devin_command)
            })
        })
}

fn read_json_object(path: &Path) -> Result<serde_json::Map<String, Value>, SetupError> {
    if !path.exists() {
        return Ok(serde_json::Map::new());
    }
    let content =
        std::fs::read_to_string(path).map_err(|source| SetupError::ExistingReadFailed {
            path: path.to_path_buf(),
            source,
        })?;
    let value = serde_json::from_str::<Value>(&content).map_err(|source| {
        SetupError::ExistingParseFailed {
            path: path.to_path_buf(),
            source,
        }
    })?;
    value
        .as_object()
        .cloned()
        .ok_or_else(|| SetupError::ExistingRootNotObject {
            path: path.to_path_buf(),
        })
}

fn write_json(path: &Path, value: &Value) -> Result<(), SetupError> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|source| SetupError::DirCreateFailed {
            path: parent.to_path_buf(),
            source,
        })?;
    }
    let content = serde_json::to_string_pretty(value)?;
    paths::atomic_write_io(path, &content).map_err(|source| SetupError::AtomicWriteFailed {
        path: path.to_path_buf(),
        source,
    })
}

fn merge_hcom_hooks(root: &mut Value, include_permissions: bool) {
    if !root.is_object() {
        *root = json!({});
    }
    let obj = root.as_object_mut().unwrap();
    let hooks = obj.entry("hooks".to_string()).or_insert_with(|| json!({}));
    if !hooks.is_object() {
        *hooks = json!({});
    }
    let hooks = hooks.as_object_mut().unwrap();

    // Drop any existing hcom-owned hook entries first (clean slate).
    for entries in hooks.values_mut() {
        if let Some(entries) = entries.as_array_mut() {
            entries.retain(|entry| !entry_is_hcom(entry));
        }
    }

    for (event, command, permissions_only, matcher) in DEVIN_HOOK_COMMANDS {
        if *permissions_only && !include_permissions {
            continue;
        }
        let entries = hooks
            .entry((*event).to_string())
            .or_insert_with(|| json!([]));
        if !entries.is_array() {
            *entries = json!([]);
        }
        entries
            .as_array_mut()
            .unwrap()
            .push(expected_hook(command, *matcher));
    }
    hooks.retain(|_, entries| {
        entries
            .as_array()
            .is_some_and(|entries| !entries.is_empty())
    });

    // Export $HCOM so the `${HCOM:-hcom}` fallback in hook commands resolves
    // to the actual invocation (hcom / uvx hcom / dev build).
    let env = obj
        .entry("env".to_string())
        .or_insert_with(|| json!({}));
    if !env.is_object() {
        *env = json!({});
    }
    env.as_object_mut().unwrap().insert(
        "HCOM".to_string(),
        Value::String(crate::runtime_env::build_hcom_command()),
    );
}

fn remove_hcom_hooks(root: &mut Value) {
    let Some(obj) = root.as_object_mut() else {
        return;
    };
    if let Some(hooks) = obj.get_mut("hooks").and_then(Value::as_object_mut) {
        for entries in hooks.values_mut() {
            let Some(entries) = entries.as_array_mut() else {
                continue;
            };
            entries.retain(|entry| !entry_is_hcom(entry));
        }
        hooks.retain(|_, entries| {
            entries
                .as_array()
                .is_some_and(|entries| !entries.is_empty())
        });
    }
    // Drop the HCOM env export only if no hcom hook commands remain.
    let any_hcom_left = obj
        .get("hooks")
        .and_then(Value::as_object)
        .is_some_and(|h| {
            h.values().any(|entries| {
                entries
                    .as_array()
                    .is_some_and(|e| e.iter().any(entry_is_hcom))
            })
        });
    if !any_hcom_left {
        if let Some(env) = obj.get_mut("env").and_then(Value::as_object_mut) {
            env.remove("HCOM");
            if env.is_empty() {
                obj.remove("env");
            }
        }
    }
}

fn verify_hooks_at(path: &Path, include_permissions: bool) -> bool {
    let Ok(content) = std::fs::read_to_string(path) else {
        return false;
    };
    let Ok(root) = serde_json::from_str::<Value>(&content) else {
        return false;
    };
    let Some(hooks) = root.get("hooks").and_then(Value::as_object) else {
        return false;
    };
    DEVIN_HOOK_COMMANDS
        .iter()
        .filter(|(_, _, permissions_only, _)| include_permissions || !*permissions_only)
        .all(|(event, command, _, _)| {
            hooks
                .get(*event)
                .and_then(Value::as_array)
                .is_some_and(|entries| {
                    entries.iter().any(|group| {
                        group
                            .get("hooks")
                            .and_then(Value::as_array)
                            .is_some_and(|inner| {
                                inner.iter().any(|h| {
                                    h.get("command").and_then(Value::as_str)
                                        == Some(build_devin_hook_command(command).as_str())
                                        && h.get("timeout").and_then(Value::as_u64).is_some()
                                })
                            })
                    })
                })
        })
}

pub fn remove_devin_hooks() -> bool {
    let mut paths = vec![get_devin_settings_path()];
    if let Some(proj) = devin_project_config_dir() {
        let p = proj.join("config.json");
        if p != paths[0] {
            paths.push(p);
        }
    }
    let mut any_ok = true;
    for path in paths {
        if !path.exists() {
            continue;
        }
        match read_json_object(&path) {
            Ok(root) => {
                let mut value = Value::Object(root);
                remove_hcom_hooks(&mut value);
                if write_json(&path, &value).is_err() {
                    any_ok = false;
                }
            }
            Err(_) => any_ok = false,
        }
    }
    any_ok
}

pub fn try_setup_devin_hooks(include_permissions: bool) -> Result<(), SetupError> {
    let settings_path = get_devin_settings_path();
    let mut settings = Value::Object(read_json_object(&settings_path)?);
    merge_hcom_hooks(&mut settings, include_permissions);
    write_json(&settings_path, &settings)?;
    if !verify_hooks_at(&settings_path, include_permissions) {
        return Err(SetupError::PostWriteVerifyFailed(settings_path));
    }
    Ok(())
}

pub fn verify_devin_hooks_installed(include_permissions: bool) -> bool {
    verify_hooks_at(&get_devin_settings_path(), include_permissions)
}

// ── Hook dispatch ───────────────────────────────────────────────────────

fn resolve_instance(db: &HcomDb, ctx: &HcomContext, payload: &HookPayload) -> Option<InstanceRow> {
    instance_binding::resolve_instance_from_binding(
        db,
        payload.session_id.as_deref(),
        ctx.process_id.as_deref(),
    )
}

fn update_position(db: &HcomDb, ctx: &HcomContext, payload: &HookPayload, instance_name: &str) {
    let mut updates = serde_json::Map::new();
    if let Some(session_id) = payload.session_id.as_ref().filter(|s| !s.is_empty()) {
        updates.insert("session_id".into(), Value::String(session_id.clone()));
    }
    if let Some(path) = payload.transcript_path.as_ref().filter(|s| !s.is_empty()) {
        updates.insert("transcript_path".into(), Value::String(path.clone()));
    }
    let cwd = payload
        .raw
        .get("cwd")
        .and_then(Value::as_str)
        .unwrap_or_else(|| ctx.cwd.to_str().unwrap_or(""));
    if !cwd.is_empty() {
        updates.insert("directory".into(), Value::String(cwd.to_string()));
    }
    instances::update_instance_position(db, instance_name, &updates);
}

fn handle_sessionstart(db: &HcomDb, ctx: &HcomContext, payload: &HookPayload) -> Value {
    let Some(session_id) = payload.session_id.as_deref().filter(|sid| !sid.is_empty()) else {
        return json!({});
    };
    let instance_name = ctx
        .process_id
        .as_deref()
        .and_then(|pid| instance_binding::bind_session_to_process(db, session_id, Some(pid)))
        .or_else(|| resolve_instance(db, ctx, payload).map(|instance| instance.name));
    let Some(instance_name) = instance_name else {
        return json!({});
    };
    let _ = db.rebind_instance_session(&instance_name, session_id);
    instance_binding::capture_and_store_launch_context(db, &instance_name);
    let Some(instance) = db.get_instance_full(&instance_name).ok().flatten() else {
        return json!({});
    };
    update_position(db, ctx, payload, &instance_name);
    lifecycle::set_status(
        db,
        &instance_name,
        ST_LISTENING,
        "start",
        Default::default(),
    );
    crate::runtime_env::set_terminal_title(&instance_name);
    crate::relay::worker::ensure_worker(true);
    common::notify_hook_instance_with_db(db, &instance_name);
    if let Some(bootstrap) =
        common::inject_bootstrap_once(db, ctx, &instance_name, &instance, "devin")
    {
        json!({ "additionalContext": bootstrap })
    } else {
        json!({})
    }
}

fn resolved_instance(db: &HcomDb, ctx: &HcomContext, payload: &HookPayload) -> Option<InstanceRow> {
    let instance = resolve_instance(db, ctx, payload)?;
    update_position(db, ctx, payload, &instance.name);
    Some(instance)
}

fn handle_userpromptsubmit(db: &HcomDb, ctx: &HcomContext, payload: &HookPayload) -> Value {
    if let Some(instance) = resolved_instance(db, ctx, payload) {
        let prompt = payload
            .raw
            .get("prompt")
            .or_else(|| payload.raw.get("initial_prompt"))
            .and_then(Value::as_str)
            .unwrap_or("");
        let context = if prompt.trim() == HCOM_TRIGGER {
            "trigger"
        } else {
            "prompt"
        };
        lifecycle::set_status(db, &instance.name, ST_ACTIVE, context, Default::default());
    }
    json!({})
}

fn handle_pretooluse(db: &HcomDb, ctx: &HcomContext, payload: &HookPayload) -> Value {
    if let Some(instance) = resolved_instance(db, ctx, payload) {
        common::update_tool_status(
            db,
            &instance.name,
            "devin",
            &payload.tool_name,
            &payload.tool_input,
        );
    }
    json!({})
}

fn pending_additional_context(db: &HcomDb, instance_name: &str) -> (Value, Option<DeliveryAck>) {
    match common::prepare_pending_messages(db, instance_name) {
        Some(prepared) => (
            json!({ "additionalContext": prepared.formatted }),
            Some(prepared.ack),
        ),
        None => (json!({}), None),
    }
}

fn handle_posttooluse(
    db: &HcomDb,
    ctx: &HcomContext,
    payload: &HookPayload,
) -> (Value, Option<DeliveryAck>) {
    let Some(instance) = resolved_instance(db, ctx, payload) else {
        return (json!({}), None);
    };
    pending_additional_context(db, &instance.name)
}

fn handle_stop(
    db: &HcomDb,
    ctx: &HcomContext,
    payload: &HookPayload,
) -> (Value, Option<DeliveryAck>) {
    let Some(instance) = resolved_instance(db, ctx, payload) else {
        return (json!({ "decision": "allow" }), None);
    };
    lifecycle::set_status(db, &instance.name, ST_LISTENING, "", Default::default());
    common::notify_hook_instance_with_db(db, &instance.name);
    match common::prepare_pending_messages(db, &instance.name) {
        Some(prepared) => (
            json!({ "decision": "block", "reason": prepared.formatted }),
            Some(prepared.ack),
        ),
        None => (json!({ "decision": "allow" }), None),
    }
}

/// PostCompaction: re-inject bootstrap after context compaction, mirroring
/// Claude's `source=compact` recovery path. Devin fires `PostCompaction`
/// with a `summary` field; we use it as a no-op signal and re-inject.
fn handle_postcompaction(db: &HcomDb, ctx: &HcomContext, payload: &HookPayload) -> Value {
    let Some(instance) = resolved_instance(db, ctx, payload) else {
        return json!({});
    };
    let Some(instance_full) = db.get_instance_full(&instance.name).ok().flatten() else {
        return json!({});
    };
    if let Some(bootstrap) =
        common::inject_bootstrap_once(db, ctx, &instance.name, &instance_full, "devin")
    {
        json!({ "additionalContext": bootstrap })
    } else {
        json!({})
    }
}

fn command_looks_safe_hcom(command: &str) -> bool {
    let trimmed = command.trim();
    for prefix in ["hcom", "uvx hcom"] {
        if trimmed == prefix {
            return true;
        }
        for safe in common::SAFE_HCOM_COMMANDS {
            let expected = format!("{prefix} {safe}");
            if trimmed == expected || trimmed.starts_with(&format!("{expected} ")) {
                return true;
            }
        }
    }
    false
}

fn handle_permissionrequest(_db: &HcomDb, _ctx: &HcomContext, payload: &HookPayload) -> Value {
    let command = payload
        .tool_input
        .get("command")
        .or_else(|| payload.tool_input.get("cmd"))
        .or_else(|| payload.tool_input.get("script"))
        .and_then(Value::as_str)
        .unwrap_or("");
    if matches!(payload.tool_name.as_str(), "exec" | "bash" | "shell")
        && command_looks_safe_hcom(command)
    {
        json!({ "decision": "approve", "reason": "hcom coordination command" })
    } else {
        json!({})
    }
}

fn handle_sessionend(db: &HcomDb, ctx: &HcomContext, payload: &HookPayload) -> Value {
    if let Some(instance) = resolved_instance(db, ctx, payload) {
        let reason = payload
            .raw
            .get("reason")
            .or_else(|| payload.raw.get("stop_reason"))
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        common::finalize_session(db, &instance.name, reason, None);
    }
    json!({})
}

fn hook_type_for_command(hook_name: &str) -> &'static str {
    DEVIN_HOOK_COMMANDS
        .iter()
        .find(|(_, command, _, _)| *command == hook_name)
        .map(|(event, _, _, _)| *event)
        .unwrap_or("Unknown")
}

/// Dispatch a Devin hook subcommand (`devin-sessionstart`, `devin-pretooluse`, …).
///
/// Reads JSON from stdin (Claude Code-compatible format), builds a
/// [`HookPayload`] via [`HookPayload::from_devin`], routes to the handler,
/// and writes the JSON result on stdout.
pub fn dispatch_devin_hook(hook_name: &str) -> i32 {
    let raw: Value = match serde_json::from_reader(std::io::stdin().lock()) {
        Ok(value) => value,
        Err(err) => {
            log::log_warn(
                "hooks",
                "devin.parse_error",
                &format!("hook={hook_name} err={err}"),
            );
            return 0;
        }
    };
    let db = match HcomDb::open() {
        Ok(db) => db,
        Err(err) => {
            log::log_warn(
                "hooks",
                "devin.db_error",
                &format!("hook={hook_name} err={err}"),
            );
            return 0;
        }
    };
    let ctx = HcomContext::from_os();
    if !common::hook_gate_check(&ctx, &db) {
        return 0;
    }
    let payload = HookPayload::from_devin(hook_type_for_command(hook_name), raw);
    let (output, delivery_ack) =
        common::dispatch_with_panic_guard("devin", hook_name, (json!({}), None), || {
            match hook_name {
                "devin-sessionstart" => (handle_sessionstart(&db, &ctx, &payload), None),
                "devin-userpromptsubmit" => (handle_userpromptsubmit(&db, &ctx, &payload), None),
                "devin-pretooluse" => (handle_pretooluse(&db, &ctx, &payload), None),
                "devin-permissionrequest" => (handle_permissionrequest(&db, &ctx, &payload), None),
                "devin-posttooluse" => handle_posttooluse(&db, &ctx, &payload),
                "devin-stop" => handle_stop(&db, &ctx, &payload),
                "devin-postcompaction" => (handle_postcompaction(&db, &ctx, &payload), None),
                "devin-sessionend" => (handle_sessionend(&db, &ctx, &payload), None),
                _ => (json!({}), None),
            }
        });
    let mut stdout = std::io::stdout().lock();
    if serde_json::to_writer(&mut stdout, &output).is_ok()
        && stdout.flush().is_ok()
        && let Some(ack) = delivery_ack.as_ref()
    {
        common::commit_delivery_ack(&db, ack);
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hooks::test_helpers::EnvGuard;
    use serial_test::serial;

    fn devin_test_env() -> (tempfile::TempDir, PathBuf, EnvGuard) {
        let guard = EnvGuard::new();
        let dir = tempfile::tempdir().unwrap();
        let workspace = dir.path().join("workspace");
        let home = dir.path().join("home");
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&home).unwrap();
        unsafe {
            std::env::set_var("HOME", &home);
            std::env::set_var("HCOM_DIR", workspace.join(".hcom"));
            std::env::remove_var("DEVIN_CONFIG_DIR");
        }
        (dir, workspace, guard)
    }

    #[test]
    #[serial]
    fn setup_is_idempotent_and_preserves_other_hooks() {
        let (_dir, workspace, _guard) = devin_test_env();
        // HCOM_DIR set → project-local path.
        let hooks_path = workspace.join(".devin/config.json");
        // Pre-existing user hook + an unrelated key.
        let mut existing = serde_json::Map::new();
        existing.insert(
            "hooks".to_string(),
            json!({
                "PreToolUse": [
                    { "type": "command", "command": "./my-linter.sh", "timeout": 5 }
                ]
            }),
        );
        existing.insert("agent".to_string(), json!({ "model": "opus" }));
        std::fs::create_dir_all(hooks_path.parent().unwrap()).unwrap();
        std::fs::write(
            &hooks_path,
            serde_json::to_string_pretty(&Value::Object(existing)).unwrap(),
        )
        .unwrap();

        try_setup_devin_hooks(false).unwrap();
        assert!(verify_devin_hooks_installed(false));

        // Idempotent: second install doesn't duplicate hcom entries.
        try_setup_devin_hooks(false).unwrap();
        let content = std::fs::read_to_string(&hooks_path).unwrap();
        let root: Value = serde_json::from_str(&content).unwrap();
        let pre = root["hooks"]["PreToolUse"].as_array().unwrap();
        let hcom_count = pre
            .iter()
            .filter(|e| entry_is_hcom(e))
            .count();
        assert_eq!(hcom_count, 1, "hcom PreToolUse hook should appear exactly once");
        // User hook preserved.
        assert!(
            pre.iter().any(|e| e.get("command").and_then(Value::as_str) == Some("./my-linter.sh")),
            "user hook must be preserved"
        );
        // Unrelated key preserved.
        assert_eq!(root["agent"]["model"], json!("opus"));
    }

    #[test]
    #[serial]
    fn setup_includes_permissions_only_when_requested() {
        let (_dir, _workspace, _guard) = devin_test_env();
        try_setup_devin_hooks(true).unwrap();
        assert!(verify_devin_hooks_installed(true));
        let path = get_devin_settings_path();
        let root: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert!(
            root["hooks"]
                .get("PermissionRequest")
                .and_then(Value::as_array)
                .is_some_and(|e| !e.is_empty()),
            "PermissionRequest hook must be installed with permissions"
        );
    }

    #[test]
    #[serial]
    fn setup_omits_permissions_only_when_not_requested() {
        let (_dir, _workspace, _guard) = devin_test_env();
        try_setup_devin_hooks(false).unwrap();
        let path = get_devin_settings_path();
        let root: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        assert!(
            root["hooks"].get("PermissionRequest").is_none()
                || root["hooks"]["PermissionRequest"].as_array().unwrap().is_empty(),
            "PermissionRequest hook must be omitted without permissions"
        );
    }

    #[test]
    #[serial]
    fn remove_strips_hcom_hooks_and_env() {
        let (_dir, _workspace, _guard) = devin_test_env();
        try_setup_devin_hooks(false).unwrap();
        assert!(verify_devin_hooks_installed(false));
        assert!(remove_devin_hooks());
        let path = get_devin_settings_path();
        let root: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        if let Some(hooks) = root.get("hooks").and_then(Value::as_object) {
            for entries in hooks.values() {
                if let Some(arr) = entries.as_array() {
                    for e in arr {
                        assert!(
                            !entry_is_hcom(e),
                            "hcom hook command left behind after remove"
                        );
                    }
                }
            }
        }
        assert!(
            root.get("env").is_none() || root["env"].get("HCOM").is_none(),
            "HCOM env export must be removed with the hooks"
        );
    }

    #[test]
    #[serial]
    fn user_config_path_when_no_hcom_dir() {
        let guard = EnvGuard::new();
        let dir = tempfile::tempdir().unwrap();
        unsafe {
            std::env::set_var("HOME", dir.path());
            std::env::remove_var("HCOM_DIR");
            std::env::remove_var("DEVIN_CONFIG_DIR");
            std::env::remove_var("XDG_CONFIG_HOME");
        }
        let path = get_devin_settings_path();
        assert!(
            path.ends_with(".config/devin/config.json") || path.ends_with(".devin/config.json"),
            "expected user config path, got {}",
            path.display()
        );
        drop(guard);
    }

    #[test]
    fn devin_hook_commands_table_covers_core_events() {
        let events: Vec<&str> = DEVIN_HOOK_COMMANDS.iter().map(|(e, _, _, _)| *e).collect();
        for required in [
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "Stop",
            "SessionEnd",
        ] {
            assert!(events.contains(&required), "missing required event {required}");
        }
    }

    #[test]
    fn hook_type_for_command_round_trips() {
        for (event, command, _, _) in DEVIN_HOOK_COMMANDS {
            assert_eq!(hook_type_for_command(command), *event);
        }
    }
}
