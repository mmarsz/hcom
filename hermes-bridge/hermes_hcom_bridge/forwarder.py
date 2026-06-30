"""forwarder — assina hcom events e encaminha proativas ao Telegram via `hermes send`.

Loop de polling que chama:
  - hcom_client.events(type="message", after=<ultimo_ts ISO-8601>)
  - hcom_client.events(status="blocked")
Faz dedup por event id (LRU set com cap), formata texto, chama HermesSender.send(to, texto).
Anti-flood: dedup + rate-limit (max N msgs/min, default 20).

Config via env:
  HERMES_FORWARD_TO                 destino `hermes send --to` (default: telegram:<HERMES_FORWARD_TELEGRAM_CHAT_ID>)
  HERMES_FORWARD_TELEGRAM_CHAT_ID   chat id Telegram admin (fallback p/ montar HERMES_FORWARD_TO)
  HERMES_FORWARD_POLL_INTERVAL      segundos entre polls (default: 2)
  HERMES_FORWARD_RATE_LIMIT         máx msgs/min (default: 20)
  HERMES_FORWARD_DEDUP_CAP          tamanho do LRU de ids já vistos (default: 1000)
  HERMES_PATH                       binário hermes (default: hermes do PATH)
  HCOM_PATH                         binário hcom (default: hcom do PATH)

Entry-point: `python -m hermes_hcom_bridge.forwarder` ou `main(argv)`.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone

from .hcom_client import HcomClient
from .hermes_sender import HermesError, HermesSender

log = logging.getLogger("forwarder")

DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_RATE_LIMIT = 20  # msgs por minuto
DEFAULT_DEDUP_CAP = 1000


def _event_field(event: dict, *keys: str):
    """Lê um campo de evento hcom procurando em `data` (aninhado) e depois top-level.

    Eventos reais do hcom (v0.7.22): {id, instance, type, ts, data:{...}}.
    Testes/usos legacy podem usar shape flat. Aceitamos ambos.
    """
    data = event.get("data")
    if isinstance(data, dict):
        for k in keys:
            if k in data and data[k] not in (None, ""):
                return data[k]
    for k in keys:
        if k in event and event[k] not in (None, ""):
            return event[k]
    return None


def _event_ts(event: dict) -> str:
    """Timestamp do evento: `ts` (hcom real) ou `timestamp` (legacy)."""
    return event.get("ts") or event.get("timestamp") or ""


def _now_iso() -> str:
    """ISO-8601 UTC agora (segundos), no formato do hcom (ts dos eventos)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def format_event(event: dict) -> str:
    """Formata um evento hcom em texto humano p/ Telegram.

    - message:  `[<agent>] (<intent>) <text>`  (intent só se != inform)
    - status:   `[<agent>] <STATUS>: <context>`  (context = status_context/context)
    - fallback: `[<agent>] <type>`

    Lê campos de `event.data` (shape real do hcom) com fallback top-level.
    """
    etype = event.get("type")
    instance = event.get("instance") or _event_field(event, "from") or "?"
    if etype == "message":
        text = _event_field(event, "text") or ""
        intent = _event_field(event, "intent")
        prefix = f"[{instance}]"
        if intent and intent != "inform":
            prefix += f" ({intent})"
        return f"{prefix} {text}".strip()
    if etype == "status":
        status = str(_event_field(event, "status") or "?").upper()
        ctx = _event_field(event, "status_context", "context", "detail")
        suffix = f": {ctx}" if ctx else ""
        return f"[{instance}] {status}{suffix}"
    return f"[{instance}] {etype or 'event'}"


class Forwarder:
    """Loop de polling hcom -> hermes/Telegram.

    Args:
        hcom: HcomClient injetável.
        sender: HermesSender injetável.
        to: destino `hermes send --to <to>`.
        poll_interval: segundos entre polls.
        rate_limit: máx msgs por minuto (janela deslizante).
        dedup_cap: tamanho do LRU de ids já vistos.
    """

    def __init__(
        self,
        hcom: HcomClient,
        sender: HermesSender,
        *,
        to: str,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        rate_limit: int = DEFAULT_RATE_LIMIT,
        dedup_cap: int = DEFAULT_DEDUP_CAP,
        start_ts: str | None = None,
        forward_intents: set[str] | None = None,
    ) -> None:
        self.hcom = hcom
        self.sender = sender
        self.to = to
        self.poll_interval = poll_interval
        self.rate_limit = rate_limit
        self.dedup_cap = dedup_cap
        # set de intents p/ filtrar mensagens encaminhadas (None/vazio = todas).
        self.forward_intents = forward_intents or set()
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._send_times: deque[float] = deque()
        # start_ts: só encaminha eventos com ts > start_ts (default = agora UTC).
        # Evita replay do histórico inteiro pro Telegram ao subir o daemon.
        # _last_ts começa em start_ts p/ o filtro `after` do events().
        # start_ts="" desativa o gate (útil em testes / replay completo).
        self._start_ts = start_ts if start_ts is not None else _now_iso()
        # _last_ts: None quando gate desativado (start_ts="") → primeiro poll
        # sem filtro `after` (compat com testes); caso contrário = start_ts.
        self._last_ts: str | None = self._start_ts or None
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    # ---- dedup (LRU set) -----------------------------------------------------

    def _is_seen(self, event_id: str) -> bool:
        if event_id in self._seen:
            self._seen.move_to_end(event_id)  # LRU touch
            return True
        self._seen[event_id] = None
        if len(self._seen) > self.dedup_cap:
            self._seen.popitem(last=False)
        return False

    @staticmethod
    def _event_id(event: dict) -> str:
        eid = event.get("id")
        if eid:
            return str(eid)
        # sem id: fallback composto (ts|type|instance)
        return f"{_event_ts(event)}|{event.get('type')}|{event.get('instance')}"

    # ---- rate-limit (janela deslizante 60s) ----------------------------------

    def _rate_allow(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        cutoff = now - 60.0
        while self._send_times and self._send_times[0] < cutoff:
            self._send_times.popleft()
        if len(self._send_times) >= self.rate_limit:
            return False
        self._send_times.append(now)
        return True

    # ---- emit (dedup + rate-limit + format + send) ---------------------------

    def _emit(self, event: dict) -> bool:
        """Dedup + rate-limit + format + send. Retorna True se enviado."""
        # gate de histórico: ignora eventos anteriores ao start_ts (no-startup).
        ts = _event_ts(event)
        if ts and self._start_ts and ts <= self._start_ts:
            return False
        eid = self._event_id(event)
        if self._is_seen(eid):
            return False
        if not self._rate_allow():
            log.warning("rate-limit: descartando evento %s (cap %d/min)", eid, self.rate_limit)
            return False
        text = format_event(event)
        try:
            self.sender.send(self.to, text)
        except HermesError as e:
            log.error("hermes send falhou p/ evento %s: %s", eid, e)
            return False
        log.info("encaminhado %s -> %s", eid, self.to)
        return True

    # ---- poll ----------------------------------------------------------------

    def poll_once(self) -> int:
        """Busca eventos (messages + blocked) e encaminha. Retorna nº enviados."""
        msg_events = self.hcom.events(type="message", after=self._last_ts)
        # filtro de intent (HERMES_FORWARD_INTENTS csv, ex. "request"): se setado,
        # só encaminha mensagens cujo intent está no set. Reduz ruído (swarm ativo
        # gera muito inform/ack). blocked sempre encaminhado. Default vazio = todos.
        if self.forward_intents:
            msg_events = [
                e for e in msg_events
                if (_event_field(e, "intent") or "inform") in self.forward_intents
            ]
        blocked_events = self.hcom.events(status="blocked")
        all_events = msg_events + blocked_events
        # ordena por ts p/ atualizar _last_ts corretamente (lexicográfico em
        # ISO-8601 é consistente)
        all_events.sort(key=lambda e: _event_ts(e))
        sent = 0
        for ev in all_events:
            if self._emit(ev):
                sent += 1
            ts = _event_ts(ev)
            if ts and (self._last_ts is None or ts > self._last_ts):
                self._last_ts = ts
        return sent

    def run(self) -> None:
        log.info(
            "forwarder iniciado: to=%s poll=%.1fs rate=%d/min dedup_cap=%d",
            self.to, self.poll_interval, self.rate_limit, self.dedup_cap,
        )
        while not self._stop:
            try:
                n = self.poll_once()
                if n:
                    log.info("encaminhados %d eventos", n)
            except Exception as e:  # noqa: BLE001 — loop não pode morrer
                log.error("erro no poll: %s", e)
            # sleep interrompível (checa _stop a cada 0.2s)
            slept = 0.0
            step = min(0.2, self.poll_interval)
            while not self._stop and slept < self.poll_interval:
                time.sleep(step)
                slept += step
        log.info("forwarder encerrado")


def _env_to() -> str:
    to = os.environ.get("HERMES_FORWARD_TO")
    if to:
        return to
    chat_id = os.environ.get("HERMES_FORWARD_TELEGRAM_CHAT_ID")
    if chat_id:
        return f"telegram:{chat_id}"
    raise SystemExit("defina HERMES_FORWARD_TO ou HERMES_FORWARD_TELEGRAM_CHAT_ID")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="forwarder",
        description="Encaminha eventos hcom pro Telegram via `hermes send`.",
    )
    p.add_argument("--to", default=None, help="destino `hermes send --to` (default: env HERMES_FORWARD_TO)")
    p.add_argument("--poll-interval", type=float, default=None, help="segundos entre polls (default: 2)")
    p.add_argument("--rate-limit", type=int, default=None, help="máx msgs/min (default: 20)")
    p.add_argument("--dedup-cap", type=int, default=None, help="tamanho do LRU de ids (default: 1000)")
    p.add_argument("--name", default="bridge", help="identidade hcom (passa --name)")
    p.add_argument("-v", "--verbose", action="store_true", help="log debug")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    to = args.to or _env_to()
    poll = args.poll_interval if args.poll_interval is not None else float(
        os.environ.get("HERMES_FORWARD_POLL_INTERVAL", DEFAULT_POLL_INTERVAL))
    rate = args.rate_limit if args.rate_limit is not None else int(
        os.environ.get("HERMES_FORWARD_RATE_LIMIT", DEFAULT_RATE_LIMIT))
    cap = args.dedup_cap if args.dedup_cap is not None else int(
        os.environ.get("HERMES_FORWARD_DEDUP_CAP", DEFAULT_DEDUP_CAP))
    # HERMES_FORWARD_INTENTS csv (ex. "request"): filtra mensagens por intent.
    # Default vazio = encaminha todas (comportamento original do SPEC v1).
    intents_raw = os.environ.get("HERMES_FORWARD_INTENTS", "").strip()
    intents = {s.strip() for s in intents_raw.split(",") if s.strip()}

    hcom = HcomClient(name=args.name)
    sender = HermesSender()
    fwd = Forwarder(hcom, sender, to=to, poll_interval=poll, rate_limit=rate,
                    dedup_cap=cap, forward_intents=intents)

    def _sig(signum, _frame):
        log.info("sinal %s recebido, parando...", signum)
        fwd.stop()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    fwd.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
