"""Testes do forwarder: dedup, rate-limit, blocked, formatação.

Monkeypatcha HcomClient.events (via FakeHcom) e HermesSender.send (via FakeSender)
p/ não depender de CLIs reais. 1 teste runnable por lógica não-trivial.
"""

from __future__ import annotations

from hermes_hcom_bridge.forwarder import Forwarder, format_event


class FakeHcom:
    """Stub de HcomClient.events: retorna listas fixas por tipo de chamada."""

    def __init__(self, messages=None, blocked=None):
        self.messages = list(messages or [])
        self.blocked = list(blocked or [])
        self.calls: list[dict] = []

    def events(self, **kw):
        self.calls.append(kw)
        if kw.get("status") == "blocked":
            return list(self.blocked)
        if kw.get("type") == "message":
            return list(self.messages)
        return []


class FakeSender:
    """Captura (to, text) sem falar com o CLI hermes."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, to, text, **kw):
        self.sent.append((to, text))
        return "ok"


def _msg(eid, ts, instance, text, intent="inform"):
    return {"id": eid, "timestamp": ts, "type": "message", "instance": instance,
            "from": instance, "text": text, "intent": intent}


def _blocked(eid, ts, instance, ctx=""):
    return {"id": eid, "timestamp": ts, "type": "status", "instance": instance,
            "status": "blocked", "status_context": ctx}


# ---- dedup ------------------------------------------------------------------

def test_dedup_does_not_resend_seen_event():
    """Evento já visto (mesmo id) não é reenviado no poll seguinte."""
    msg = _msg("1", "2026-06-30T10:00:00Z", "vino", "oi")
    hcom = FakeHcom(messages=[msg], blocked=[])
    sender = FakeSender()
    fwd = Forwarder(hcom, sender, to="telegram:1", poll_interval=0.01, start_ts="")
    fwd.poll_once()
    fwd.poll_once()  # FakeHcom repete os mesmos eventos -> dedup deve barrar
    assert len(sender.sent) == 1
    assert sender.sent[0][1] == "[vino] oi"


# ---- rate-limit -------------------------------------------------------------

def test_rate_limit_blocks_excess():
    """Acima do rate-limit, apenas N mensagens são enviadas; resto descartado."""
    msgs = [_msg(str(i), f"2026-06-30T10:00:{i:02d}Z", "vino", f"msg{i}") for i in range(25)]
    hcom = FakeHcom(messages=msgs, blocked=[])
    sender = FakeSender()
    fwd = Forwarder(hcom, sender, to="telegram:1", poll_interval=0.01, rate_limit=10, start_ts="")
    fwd.poll_once()
    assert len(sender.sent) == 10


# ---- blocked events ---------------------------------------------------------

def test_blocked_events_are_forwarded():
    """Eventos de status=blocked são encaminhados ao destino."""
    blocked = [_blocked("b1", "2026-06-30T10:00:05Z", "nova", "esperando approval")]
    hcom = FakeHcom(messages=[], blocked=blocked)
    sender = FakeSender()
    fwd = Forwarder(hcom, sender, to="telegram:42", poll_interval=0.01, start_ts="")
    n = fwd.poll_once()
    assert n == 1
    assert len(sender.sent) == 1
    to, text = sender.sent[0]
    assert to == "telegram:42"
    assert "nova" in text
    assert "BLOCKED" in text
    assert "esperando approval" in text


# ---- formatação -------------------------------------------------------------

def test_format_status_includes_agent_and_status():
    """Texto formatado de status inclui nome do agente + status + contexto."""
    ev = _blocked("b1", "2026-06-30T10:00:00Z", "vino", "needs input")
    text = format_event(ev)
    assert "vino" in text
    assert "BLOCKED" in text
    assert "needs input" in text


def test_format_message_includes_agent_and_text():
    """Texto formatado de message inclui nome do agente + corpo da mensagem."""
    ev = _msg("m1", "2026-06-30T10:00:00Z", "kimi", "testes passaram", intent="inform")
    text = format_event(ev)
    assert "kimi" in text
    assert "testes passaram" in text
    # intent inform não aparece (é o default, omitido)
    assert "inform" not in text


def test_format_message_with_request_intent_shows_intent():
    """intent=request é relevante -> aparece no texto formatado."""
    ev = _msg("m1", "2026-06-30T10:00:00Z", "kimi", "rode os testes", intent="request")
    text = format_event(ev)
    assert "kimi" in text
    assert "request" in text
    assert "rode os testes" in text


# ---- last_ts avança ---------------------------------------------------------

def test_last_ts_advances_after_poll():
    """Após um poll, _last_ts avança p/ o maior timestamp visto (filtra próximos)."""
    msgs = [
        _msg("1", "2026-06-30T10:00:00Z", "a", "x"),
        _msg("2", "2026-06-30T10:00:05Z", "b", "y"),
    ]
    hcom = FakeHcom(messages=msgs, blocked=[])
    sender = FakeSender()
    fwd = Forwarder(hcom, sender, to="telegram:1", poll_interval=0.01, start_ts="")
    fwd.poll_once()
    assert fwd._last_ts == "2026-06-30T10:00:05Z"
    # segundo poll passa after=10:00:05Z (filtra eventos antigos)
    fwd.poll_once()
    msg_calls = [c for c in hcom.calls if c.get("type") == "message"]
    assert msg_calls[0]["after"] is None       # primeiro poll sem filtro
    assert msg_calls[1]["after"] == "2026-06-30T10:00:05Z"


# ---- estrutura real do hcom (data aninhado + ts) ----------------------------

def _real_msg(eid, ts, instance, text, intent="inform"):
    """Shape real do hcom v0.7.22: campos aninhados em `data`, ts no topo."""
    return {"id": eid, "instance": instance, "type": "message", "ts": ts,
            "data": {"from": instance, "intent": intent, "text": text}}


def _real_blocked(eid, ts, instance, ctx="needs approval"):
    return {"id": eid, "instance": instance, "type": "status", "ts": ts,
            "data": {"status": "blocked", "context": ctx, "detail": ctx}}


def test_format_real_message_shape():
    ev = _real_msg(6038, "2026-06-30T19:56:52", "bimu",
                   "kanban: pronto, testes 8 passed", intent="inform")
    text = format_event(ev)
    assert "bimu" in text
    assert "kanban: pronto" in text
    assert "inform" not in text  # default, omitido


def test_format_real_status_shape():
    ev = _real_blocked(6101, "2026-06-30T20:01:24", "nova", "esperando approval")
    text = format_event(ev)
    assert "nova" in text
    assert "BLOCKED" in text
    assert "esperando approval" in text


def test_forward_real_shape_event():
    """Evento no shape real do hcom é encaminhado e _last_ts usa `ts`."""
    msg = _real_msg(7001, "2026-06-30T19:56:52", "vino", "rode os testes", "request")
    hcom = FakeHcom(messages=[msg], blocked=[])
    sender = FakeSender()
    fwd = Forwarder(hcom, sender, to="telegram:1", poll_interval=0.01, start_ts="")
    n = fwd.poll_once()
    assert n == 1
    to, text = sender.sent[0]
    assert "vino" in text and "rode os testes" in text and "request" in text
    assert fwd._last_ts == "2026-06-30T19:56:52"


def test_history_gate_skips_events_before_start_ts():
    """start_ts: eventos com ts <= start_ts NÃO são encaminhados (no replay)."""
    old = _msg("1", "2026-06-30T10:00:00Z", "vino", "antigo")
    new = _msg("2", "2026-06-30T20:00:00Z", "vino", "novo")
    hcom = FakeHcom(messages=[old, new], blocked=[])
    sender = FakeSender()
    # start_ts entre os dois: só o "novo" (ts > start_ts) é encaminhado
    fwd = Forwarder(hcom, sender, to="telegram:1", poll_interval=0.01,
                    start_ts="2026-06-30T15:00:00Z")
    n = fwd.poll_once()
    assert n == 1
    assert len(sender.sent) == 1
    assert "novo" in sender.sent[0][1]
    assert "antigo" not in [t for _, t in sender.sent]


def test_default_start_ts_is_now():
    """Sem start_ts explícito, _last_ts começa em ~agora (não None)."""
    hcom = FakeHcom(messages=[], blocked=[])
    sender = FakeSender()
    fwd = Forwarder(hcom, sender, to="telegram:1", poll_interval=0.01)
    assert fwd._last_ts is not None and fwd._last_ts != ""
    assert fwd._start_ts == fwd._last_ts


def test_forward_intents_filter():
    """forward_intents={'request'} só encaminha requests; informs descartados."""
    msgs = [
        _msg("1", "2026-06-30T10:00:00Z", "vino", "oi", intent="inform"),
        _msg("2", "2026-06-30T10:00:01Z", "orq", "decide X", intent="request"),
        _msg("3", "2026-06-30T10:00:02Z", "vino", "ok", intent="ack"),
    ]
    hcom = FakeHcom(messages=msgs, blocked=[])
    sender = FakeSender()
    fwd = Forwarder(hcom, sender, to="telegram:1", poll_interval=0.01,
                    start_ts="", forward_intents={"request"})
    n = fwd.poll_once()
    assert n == 1
    assert len(sender.sent) == 1
    assert "decide X" in sender.sent[0][1]
