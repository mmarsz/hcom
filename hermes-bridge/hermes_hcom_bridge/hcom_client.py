"""hcom_client — único ponto que fala com o CLI `hcom` (subprocess).

Tudo no repo que precisar do hcom passa por aqui. Args sempre como lista
(never shell=True) p/ evitar injeção. Timeout em toda chamada. Parse robusto
de JSON (hcom pode imprimir warnings no stderr; stdout = JSON).

Contrato estável: o resto do repo depende destes métodos. Mudar aqui = atualizar
callers.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Iterable


class HcomError(RuntimeError):
    """Erro de comunicação com o hcom (timeout, exit!=0, JSON inválido)."""


@dataclass(frozen=True)
class HcomResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class HcomClient:
    """Wrapper subprocess do CLI `hcom`.

    Args:
        hcom_path: caminho do binário hcom (default: "hcom" do PATH).
        default_timeout: timeout padrão em segundos p/ chamadas sem timeout explícito.
        name: identidade hcom usada em `send` (passa --name). spawn/kill/list/events
            não precisam de identidade.
    """

    def __init__(
        self,
        hcom_path: str | None = None,
        default_timeout: float = 30.0,
        name: str | None = None,
        target_suffix: str | None = None,
    ) -> None:
        self.hcom_path = hcom_path or os.environ.get("HCOM_PATH", "hcom")
        self.default_timeout = default_timeout
        self.name = name
        # Sufixo de destino p/ relays cross-device (ex.: ":FELE"). Quando setado
        # (env HCOM_TARGET_SUFFIX), é anexado a targets que NÃO contêm ':' (nomes
        # base) — targets já qualificados ("orq:FELE") e "tag:T" ficam inalterados.
        # Necessário porque o hcom rejeita nomes base parciais de agentes remotos.
        self.target_suffix = target_suffix if target_suffix is not None else (
            os.environ.get("HCOM_TARGET_SUFFIX", ""))

    @staticmethod
    def _apply_suffix(target: str, suffix: str) -> str:
        """Anexa `suffix` a um target base (sem ':'), preservando '@' e qualificados.

        "orq" -> "orq:FELE"; "@orq" -> "@orq:FELE"; "orq:FELE" -> "orq:FELE";
        "tag:T" -> "tag:T" (tem ':'). suffix vazio = no-op.
        """
        if not suffix:
            return target
        at = target.startswith("@")
        base = target[1:] if at else target
        if ":" in base:
            return target  # já qualificado ou tag:T
        return f"@{base}{suffix}" if at else f"{base}{suffix}"

    # ---- baixo nível ---------------------------------------------------------

    def _run(
        self,
        args: list[str],
        *,
        stdin: str | None = None,
        timeout: float | None = None,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> HcomResult:
        """Roda `hcom <args>` como subprocess (lista, sem shell)."""
        cmd = [self.hcom_path, *args]
        run_env = None
        if env:
            run_env = {**os.environ, **env}
        try:
            proc = subprocess.run(
                cmd,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout if timeout is not None else self.default_timeout,
                env=run_env,
            )
        except FileNotFoundError as e:
            raise HcomError(f"hcom não encontrado em '{self.hcom_path}': {e}") from e
        except subprocess.TimeoutExpired as e:
            raise HcomError(
                f"hcom timeout após {e.timeout}s: {' '.join(map(shlex.quote, cmd))}"
            ) from e
        res = HcomResult(proc.returncode, proc.stdout, proc.stderr)
        if check and not res.ok:
            raise HcomError(
                f"hcom exit={res.exit_code}: {' '.join(map(shlex.quote, cmd))}\n"
                f"stderr: {res.stderr.strip()}"
            )
        return res

    @staticmethod
    def _parse_json(stdout: str) -> Any:
        """hcom imprime JSON no stdout. Tenta parse direto; se vier misturado com
        texto, extrai a primeira/última linha JSON válida."""
        stdout = stdout.strip()
        if not stdout:
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            pass
        # fallback: procura a primeira linha que parseia como JSON (array/objeto)
        for line in stdout.splitlines():
            line = line.strip()
            if line and line[0] in "[{":
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        raise HcomError(f"resposta do hcom não é JSON: {stdout[:200]!r}")

    @staticmethod
    def _parse_json_lines(stdout: str) -> list:
        """Parse do output do `hcom events` — NDJSON (um JSON por linha).

        `hcom events` imprime um evento por linha (NDJSON), NÃO um array JSON.
        Aceita também um array JSON único (fallback) e linhas de aviso misturadas.
        """
        stdout = stdout.strip()
        if not stdout:
            return []
        # array único?
        if stdout[0] == "[":
            try:
                v = json.loads(stdout)
                return list(v) if isinstance(v, list) else [v]
            except json.JSONDecodeError:
                pass
        # NDJSON: uma linha por evento
        out: list = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line or line[0] not in "[{":
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    # ---- list / agents -------------------------------------------------------

    def list_agents(self, *, include_stopped: bool = False) -> list[dict]:
        """`hcom list --json` -> lista de dicts (agentes vivos)."""
        args = ["list", "--json"]
        if include_stopped:
            # hcom list --stopped retorna eventos de parada; mantemos simples aqui
            args = ["list", "--stopped", "--json"]
        res = self._run(args, timeout=self.default_timeout)
        data = self._parse_json(res.stdout)
        if data is None:
            return []
        if isinstance(data, dict):
            data = [data]
        return list(data)

    def agent(self, name: str) -> dict:
        """`hcom list <name> --json` -> detalhe de um agente."""
        res = self._run(["list", name, "--json"], timeout=self.default_timeout)
        data = self._parse_json(res.stdout)
        return data if isinstance(data, dict) else {}

    # ---- events --------------------------------------------------------------

    def events(
        self,
        *,
        agent: str | None = None,
        type: str | None = None,
        status: str | None = None,
        intent: str | None = None,
        from_: str | None = None,
        mention: str | None = None,
        after: str | None = None,
        before: str | None = None,
        last: int | None = None,
        wait: float | None = None,
    ) -> list[dict]:
        """`hcom events [filtros]` -> lista de eventos (JSON)."""
        args = ["events"]
        if agent:
            args += ["--agent", agent]
        if type:
            args += ["--type", type]
        if status:
            args += ["--status", status]
        if intent:
            args += ["--intent", intent]
        if from_:
            args += ["--from", from_]
        if mention:
            args += ["--mention", mention]
        if after:
            args += ["--after", after]
        if before:
            args += ["--before", before]
        if last is not None:
            args += ["--last", str(last)]
        if wait is not None:
            args += ["--wait", str(wait)]
        timeout = (wait + 5) if wait else self.default_timeout
        res = self._run(args, timeout=timeout)
        # `hcom events` é NDJSON (um evento por linha) — parser dedicado.
        return self._parse_json_lines(res.stdout)

    # ---- send ----------------------------------------------------------------

    def send(
        self,
        targets: Iterable[str] | None,
        text: str,
        *,
        intent: str | None = None,
        reply_to: str | None = None,
        thread: str | None = None,
        name: str | None = None,
        go: bool = True,
    ) -> HcomResult:
        """`hcom send [@targets] [--intent ...] -- text`.

        targets=None ou vazio = broadcast (`send -- text`). `--go` é necessário
        p/ broadcast (gate anti-loop do hcom); default True.
        """
        args = ["send"]
        for t in (targets or []):
            t = self._apply_suffix(t, self.target_suffix)
            args.append(t if t.startswith("@") else f"@{t}")
        if intent:
            args += ["--intent", intent]
        if reply_to:
            args += ["--reply-to", str(reply_to)]
        if thread:
            args += ["--thread", thread]
        args += ["--name", name or self.name or "bridge"]
        if go:
            args.append("--go")
        args += ["--", text]
        return self._run(args, timeout=self.default_timeout)

    # ---- spawn / kill --------------------------------------------------------

    def spawn(
        self,
        tool: str,
        *,
        count: int = 1,
        tag: str | None = None,
        directory: str | None = None,
        headless: bool = False,
        model: str | None = None,
        hcom_prompt: str | None = None,
        extra_args: Iterable[str] | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = 60.0,
    ) -> HcomResult:
        """`hcom [N] <tool> [flags] [tool-args]`.

        YOLO: caller deve passar env={"DEVIN_PERMISSION_MODE": "dangerous"} p/ devin
        headless (auto-aprova tudo). Não fazemos isso aqui p/ não acoplar.
        """
        args = [str(count), tool]
        if tag:
            args += ["--tag", tag]
        if directory:
            args += ["--dir", directory]
        if headless:
            args.append("--headless")
        if model:
            args += ["--model", model]
        if hcom_prompt:
            args += ["--hcom-prompt", hcom_prompt]
        if extra_args:
            args += list(extra_args)
        return self._run(args, timeout=timeout, env=env)

    def kill(self, target: str) -> HcomResult:
        """`hcom kill <name|tag:T|all>`."""
        return self._run(["kill", target], timeout=self.default_timeout)
