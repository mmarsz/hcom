#!/usr/bin/env bash
# Sobe forwarder + kanban daemons (idempotente). Additivo — não toca gateway/webui.
set -euo pipefail
cd ~/contaxis/hermes-hcom-bridge
RUN=~/contaxis/hermes-hcom-bridge/run
PY=/home/mmarsz/.hermes/hermes-agent/venv/bin/python
export HERMES_HOME=/home/mmarsz/.hermes
export HCOM_PATH=/home/mmarsz/.local/bin/hcom
export HCOM_TARGET_SUFFIX=:FELE
export HERMES_PATH=/home/mmarsz/.hermes/hermes-agent/venv/bin/hermes
export HERMES_FORWARD_TO=telegram:7054724026
export HERMES_FORWARD_INTENTS=request
export HCOM_BRIDGE_NAME=bridge
export KANBAN_HOST=100.87.96.23
export KANBAN_PORT=8643
export HCOM_TIMEOUT=30

start_one() {
  local name=$1 module=$2
  local pidf=$RUN/$name.pid logf=$RUN/$name.log
  if [ -f "$pidf" ] && kill -0 "$(cat $pidf)" 2>/dev/null; then
    echo "$name already running (pid $(cat $pidf))"; return 0
  fi
  nohup "$PY" -m "$module" >"$logf" 2>&1 &
  echo $! > "$pidf"
  sleep 1
  if kill -0 "$(cat $pidf)" 2>/dev/null; then
    echo "$name started (pid $(cat $pidf)) log=$logf"
  else
    echo "$name FAILED — see $logf"; tail -5 "$logf"; return 1
  fi
}

start_one forwarder hermes_hcom_bridge.forwarder
start_one kanban    hermes_hcom_bridge.kanban.app
