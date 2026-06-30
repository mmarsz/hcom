//! Devin CLI transcript parser.
//!
//! Devin CLI stores transcripts as JSON files at
//! `~/.local/share/devin/cli/transcripts/<session-name>.json`. The internal
//! schema is not publicly documented and may change between CLI versions.
//! This parser is intentionally conservative: it reads the file as a JSON
//! value and extracts user/assistant exchanges from common top-level shapes,
//! returning an empty list when the structure is unrecognized rather than
//! crashing. As the format stabilizes, this parser should be expanded to
//! extract tool-use details and metadata.

use std::path::Path;

use serde_json::Value;

use super::shared::{Exchange, ToolUse, finalize_action_text, read_file_lossy};

fn text_from_value(v: &Value) -> String {
    v.as_str()
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| {
            v.as_array()
                .map(|arr| {
                    arr.iter()
                        .filter_map(|item| {
                            item.as_str()
                                .map(|s| s.to_string())
                                .or_else(|| item.get("text").and_then(|t| t.as_str()).map(|s| s.to_string()))
                        })
                        .collect::<Vec<_>>()
                        .join("\n")
                })
                .unwrap_or_default()
        })
}

/// Parse a Devin CLI transcript JSON file.
///
/// Returns up to `last` exchanges (0 = all). Devin transcripts are opaque
/// JSON; this parser handles three common shapes:
///   1. Top-level array of message objects
///   2. Object with `messages`/`exchanges`/`turns` array
///   3. Single message object
pub(crate) fn parse_devin_json(
    path: &Path,
    last: usize,
    _detailed: bool,
) -> Result<Vec<Exchange>, String> {
    let content = read_file_lossy(path)?;
    let root: Value =
        serde_json::from_str(&content).map_err(|e| format!("Invalid JSON in Devin transcript: {e}"))?;

    let messages: Vec<&Value> = if let Some(arr) = root.as_array() {
        arr.iter().collect()
    } else if let Some(arr) = root
        .get("messages")
        .or_else(|| root.get("exchanges"))
        .or_else(|| root.get("turns"))
        .and_then(|v| v.as_array())
    {
        arr.iter().collect()
    } else {
        vec![&root]
    };

    let mut exchanges: Vec<Exchange> = Vec::new();
    let mut current_user = String::new();
    let mut current_action = String::new();
    let mut current_tools: Vec<ToolUse> = Vec::new();
    let mut timestamp = String::new();
    let mut position = 0usize;

    let flush = |exchanges: &mut Vec<Exchange>,
                 position: &mut usize,
                 user: &mut String,
                 action: &mut String,
                 tools: &mut Vec<ToolUse>,
                 ts: &mut String| {
        if !user.is_empty() || !action.is_empty() {
            *position += 1;
            let final_action = finalize_action_text(action, tools, &[], false);
            exchanges.push(Exchange {
                position: *position,
                user: std::mem::take(user),
                action: final_action,
                files: Vec::new(),
                timestamp: std::mem::take(ts),
                tools: std::mem::take(tools),
                edits: Vec::new(),
                errors: Vec::new(),
                ended_on_error: false,
            });
            action.clear();
        }
    };

    for msg in messages {
        let role = msg
            .get("role")
            .or_else(|| msg.get("type"))
            .or_else(|| msg.get("sender"))
            .and_then(|r| r.as_str())
            .unwrap_or("");
        let text = msg
            .get("content")
            .or_else(|| msg.get("text"))
            .or_else(|| msg.get("message"))
            .map(text_from_value)
            .unwrap_or_default();
        if text.is_empty() {
            continue;
        }
        let ts = msg
            .get("timestamp")
            .or_else(|| msg.get("ts"))
            .or_else(|| msg.get("created_at"))
            .and_then(|t| t.as_str())
            .unwrap_or("")
            .to_string();
        match role.to_ascii_lowercase().as_str() {
            "user" | "human" | "prompt" => {
                flush(
                    &mut exchanges,
                    &mut position,
                    &mut current_user,
                    &mut current_action,
                    &mut current_tools,
                    &mut timestamp,
                );
                current_user = text;
                timestamp = ts;
            }
            "assistant" | "agent" | "response" | "model" => {
                if !current_action.is_empty() {
                    current_action.push_str("\n\n");
                }
                current_action.push_str(&text);
            }
            _ => {}
        }
    }
    flush(
        &mut exchanges,
        &mut position,
        &mut current_user,
        &mut current_action,
        &mut current_tools,
        &mut timestamp,
    );

    if last > 0 && exchanges.len() > last {
        let start = exchanges.len() - last;
        exchanges = exchanges.split_off(start);
    }

    Ok(exchanges)
}
