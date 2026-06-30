"""Testes do hcom_client usando um binário `hcom` fake (script Python).

Garante: args como lista (sem shell), timeout, parse de JSON, broadcast com --go,
shell-out de spawn/kill/send. Não depende de hcom real.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from hermes_hcom_bridge.hcom_client import HcomClient, HcomError


def _make_fake_hcom(tmp_path: Path, script: str) -> Path:
    """Cria um executável `hcom` que roda `script` (Python). Retorna path."""
    fake = tmp_path / "hcom"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json, os\n"
        f"{script}\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake


def _client(fake: Path) -> HcomClient:
    return HcomClient(hcom_path=str(fake), default_timeout=10.0, name="hb_build")


def test_list_agents_parses_json(tmp_path: Path) -> None:
    fake = _make_fake_hcom(
        tmp_path,
        textwrap.dedent("""
            if sys.argv[1:3] == ["list", "--json"]:
                print(json.dumps([{"name": "vino", "status": "listening"},
                                  {"name": "nova", "status": "blocked"}]))
            else:
                sys.exit(99)
        """),
    )
    agents = _client(fake).list_agents()
    assert [a["name"] for a in agents] == ["vino", "nova"]
    assert agents[0]["status"] == "listening"


def test_list_agents_empty_ok(tmp_path: Path) -> None:
    fake = _make_fake_hcom(tmp_path, "print('')\n")
    assert _client(fake).list_agents() == []


def test_send_passes_args_as_list_no_shell(tmp_path: Path) -> None:
    # captura argv e grava num arquivo p/ inspecionar
    argv_file = tmp_path / "argv.json"
    fake = _make_fake_hcom(
        tmp_path,
        textwrap.dedent(f"""
            with open({str(argv_file)!r}, "w") as f:
                json.dump(sys.argv, f)
            print("ok")
        """),
    )
    _client(fake).send(["vino"], "ls; rm -rf /", intent="request")
    argv = json.loads(argv_file.read_text())
    # texto malicioso vai como arg único, NÃO é interpretado pelo shell
    assert "ls; rm -rf /" in argv
    assert "--intent" in argv and "request" in argv
    assert "--name" in argv and "hb_build" in argv
    assert "--go" in argv  # broadcast gate
    assert "--" in argv  # separador


def test_send_broadcast_uses_go(tmp_path: Path) -> None:
    argv_file = tmp_path / "argv.json"
    fake = _make_fake_hcom(
        tmp_path,
        textwrap.dedent(f"""
            with open({str(argv_file)!r}, "w") as f:
                json.dump(sys.argv, f)
            print("ok")
        """),
    )
    _client(fake).send(None, "hi all")
    argv = json.loads(argv_file.read_text())
    # sem targets, só broadcast
    assert not any(a.startswith("@") for a in argv)
    assert "--go" in argv and "--" in argv


def test_kill_passes_target(tmp_path: Path) -> None:
    argv_file = tmp_path / "argv.json"
    fake = _make_fake_hcom(
        tmp_path,
        textwrap.dedent(f"""
            with open({str(argv_file)!r}, "w") as f:
                json.dump(sys.argv, f)
            print("killed")
        """),
    )
    _client(fake).kill("tag:hbridge")
    argv = json.loads(argv_file.read_text())
    assert argv[1:] == ["kill", "tag:hbridge"]


def test_spawn_passes_flags_and_env(tmp_path: Path) -> None:
    argv_file = tmp_path / "argv.json"
    env_file = tmp_path / "env.json"
    fake = _make_fake_hcom(
        tmp_path,
        textwrap.dedent(f"""
            with open({str(argv_file)!r}, "w") as f:
                json.dump(sys.argv, f)
            with open({str(env_file)!r}, "w") as f:
                json.dump({{k: os.environ.get(k) for k in ("DEVIN_PERMISSION_MODE",)}}, f)
            print("spawned")
        """),
    )
    _client(fake).spawn(
        "devin",
        count=2,
        tag="hbridge",
        directory=str(tmp_path),
        headless=True,
        hcom_prompt="rode os testes",
        env={"DEVIN_PERMISSION_MODE": "dangerous"},
    )
    argv = json.loads(argv_file.read_text())
    assert "2" in argv and "devin" in argv
    assert "--tag" in argv and "hbridge" in argv
    assert "--headless" in argv
    assert "--hcom-prompt" in argv and "rode os testes" in argv
    env = json.loads(env_file.read_text())
    assert env["DEVIN_PERMISSION_MODE"] == "dangerous"


def test_timeout_raises(tmp_path: Path) -> None:
    fake = _make_fake_hcom(tmp_path, "import time; time.sleep(30)\n")
    with pytest.raises(HcomError, match="timeout"):
        _client(fake)._run(["list"], timeout=0.5)


def test_nonzero_exit_raises(tmp_path: Path) -> None:
    fake = _make_fake_hcom(tmp_path, "sys.stderr.write('boom\\n'); sys.exit(2)\n")
    with pytest.raises(HcomError, match="exit=2"):
        _client(fake).list_agents()


def test_missing_binary_raises(tmp_path: Path) -> None:
    c = HcomClient(hcom_path=str(tmp_path / "nope"), default_timeout=5.0)
    with pytest.raises(HcomError, match="não encontrado"):
        c.list_agents()


def test_parse_json_handles_leading_text(tmp_path: Path) -> None:
    # hcom pode imprimir um aviso antes do JSON
    fake = _make_fake_hcom(
        tmp_path,
        textwrap.dedent("""
            if "list" in sys.argv:
                print("[tip] something")  # aviso
                print(json.dumps([{"name": "x", "status": "listening"}]))
            else:
                sys.exit(1)
        """),
    )
    agents = _client(fake).list_agents()
    assert agents == [{"name": "x", "status": "listening"}]


def test_apply_suffix_rules() -> None:
    """_apply_suffix: anexa a nomes base, preserva qualificados e tag:T."""
    f = HcomClient._apply_suffix
    assert f("orq", ":FELE") == "orq:FELE"
    assert f("@orq", ":FELE") == "@orq:FELE"
    assert f("orq:FELE", ":FELE") == "orq:FELE"      # já qualificado
    assert f("@orq:FELE", ":FELE") == "@orq:FELE"
    assert f("tag:T", ":FELE") == "tag:T"            # tag tem ':'
    assert f("@tag:T", ":FELE") == "@tag:T"
    assert f("orq", "") == "orq"                      # suffix vazio = no-op
    assert f("@orq", "") == "@orq"


def test_send_applies_target_suffix(tmp_path: Path) -> None:
    """send() com target_suffix anexa :FELE a nomes base no argv."""
    argv_file = tmp_path / "argv.json"
    fake = _make_fake_hcom(
        tmp_path,
        textwrap.dedent(f"""
            with open({str(argv_file)!r}, "w") as f:
                json.dump(sys.argv, f)
            print("ok")
        """),
    )
    c = HcomClient(hcom_path=str(fake), default_timeout=10.0,
                   name="hb_build", target_suffix=":FELE")
    c.send(["orq", "vino:FELE", "tag:T"], "hi", intent="inform")
    argv = json.loads(argv_file.read_text())
    assert "@orq:FELE" in argv      # base -> sufixado
    assert "@vino:FELE" in argv     # já qualificado, inalterado
    assert "@tag:T" in argv         # tag, inalterado


def test_send_suffix_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HCOM_TARGET_SUFFIX env é lido no __init__."""
    argv_file = tmp_path / "argv.json"
    fake = _make_fake_hcom(
        tmp_path,
        textwrap.dedent(f"""
            with open({str(argv_file)!r}, "w") as f:
                json.dump(sys.argv, f)
            print("ok")
        """),
    )
    monkeypatch.setenv("HCOM_TARGET_SUFFIX", ":FELE")
    c = HcomClient(hcom_path=str(fake), default_timeout=10.0, name="hb_build")
    c.send(["orq"], "hi")
    argv = json.loads(argv_file.read_text())
    assert "@orq:FELE" in argv


def test_parse_json_lines_ndjson() -> None:
    """`hcom events` é NDJSON (um evento por linha) — parser lê todos."""
    ndjson = (
        '{"id":1,"type":"message","data":{"text":"a"}}\n'
        '{"id":2,"type":"message","data":{"text":"b"}}\n'
        '{"id":3,"type":"status","data":{"status":"active"}}\n'
    )
    out = HcomClient._parse_json_lines(ndjson)
    assert len(out) == 3
    assert [e["id"] for e in out] == [1, 2, 3]


def test_parse_json_lines_array_fallback() -> None:
    """Array JSON único também funciona (fallback)."""
    out = HcomClient._parse_json_lines('[{"id":1},{"id":2}]')
    assert len(out) == 2


def test_parse_json_lines_ignores_noise() -> None:
    """Linhas de aviso/tip misturadas são ignoradas."""
    noisy = '[tip] something\n{"id":1,"type":"message"}\n{"id":2}\n'
    out = HcomClient._parse_json_lines(noisy)
    assert len(out) == 2


def test_events_parses_ndjson_via_subprocess(tmp_path: Path) -> None:
    """events() end-to-end: fake hcom imprime NDJSON -> lista de 3 eventos."""
    fake = _make_fake_hcom(
        tmp_path,
        textwrap.dedent("""
            if "events" in sys.argv:
                print('{"id":1,"type":"message","instance":"a","ts":"2026-06-30T20:00:01"}')
                print('{"id":2,"type":"message","instance":"b","ts":"2026-06-30T20:00:02"}')
                print('{"id":3,"type":"status","instance":"a","ts":"2026-06-30T20:00:03"}')
            else:
                sys.exit(99)
        """),
    )
    evs = _client(fake).events(type="message")
    assert len(evs) == 3
    assert [e["id"] for e in evs] == [1, 2, 3]
