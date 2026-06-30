"""hermes_sender — único ponto que fala com o CLI `hermes` (subprocess).

Wrapper mínimo de `hermes send --to <to> "<text>"`. Args sempre como lista
(never shell=True) p/ evitar injeção. Timeout em toda chamada.

Contrato estável: o forwarder depende deste módulo. Mudar aqui = atualizar callers.
"""

from __future__ import annotations

import os
import shlex
import subprocess


class HermesError(RuntimeError):
    """Erro de comunicação com o hermes (timeout, exit!=0, binário ausente)."""


class HermesSender:
    """Wrapper subprocess do CLI `hermes send`.

    Args:
        hermes_path: caminho do binário hermes (default: "hermes" do PATH ou HERMES_PATH).
        default_timeout: timeout padrão em segundos p/ chamadas sem timeout explícito.
    """

    def __init__(self, hermes_path: str | None = None, default_timeout: float = 15.0) -> None:
        self.hermes_path = hermes_path or os.environ.get("HERMES_PATH", "hermes")
        self.default_timeout = default_timeout

    def send(self, to: str, text: str, *, timeout: float | None = None) -> str:
        """`hermes send --to <to> "<text>"` -> stdout (str).

        Args como lista, sem shell. Texto nunca é interpolado em shell — vai como
        um único argv, independente do conteúdo (ponto-e-vírgula, crases, $(), etc.
        são tratados como texto literal pelo kernel).
        """
        cmd = [self.hermes_path, "send", "--to", to, text]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout if timeout is not None else self.default_timeout,
            )
        except FileNotFoundError as e:
            raise HermesError(f"hermes não encontrado em '{self.hermes_path}': {e}") from e
        except subprocess.TimeoutExpired as e:
            raise HermesError(
                f"hermes timeout após {e.timeout}s: {' '.join(map(shlex.quote, cmd))}"
            ) from e
        if proc.returncode != 0:
            raise HermesError(
                f"hermes exit={proc.returncode}: {' '.join(map(shlex.quote, cmd))}\n"
                f"stderr: {proc.stderr.strip()}"
            )
        return proc.stdout.strip()
