"""Circuit breaker por provedor (em memória, por processo).

Fechado: deixa passar. Após `falhas` falhas consecutivas abre por `abertura_s` segundos. Passado
o prazo, deixa passar UMA tentativa (meio-aberto): sucesso fecha, falha reabre. Com mais de um
worker do gunicorn cada processo tem o seu; basta como proteção contra provedor fora do ar (a
cota, que precisa ser exata, fica no banco).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class Circuito:
    def __init__(
        self,
        falhas: int,
        abertura_s: float,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limite = max(1, falhas)
        self._abertura = abertura_s
        self._relogio = relogio
        self._lock = threading.Lock()
        self._falhas = 0
        self._aberto_ate: float | None = None
        self._tentativa_em_curso = False

    def permite(self) -> bool:
        with self._lock:
            if self._aberto_ate is None:
                return True
            if self._relogio() < self._aberto_ate:
                return False
            if self._tentativa_em_curso:  # meio-aberto: só uma tentativa por vez
                return False
            self._tentativa_em_curso = True
            return True

    def sucesso(self) -> None:
        with self._lock:
            self._falhas = 0
            self._aberto_ate = None
            self._tentativa_em_curso = False

    def falha(self) -> None:
        with self._lock:
            self._tentativa_em_curso = False
            self._falhas += 1
            if self._falhas >= self._limite:
                self._aberto_ate = self._relogio() + self._abertura

    @property
    def aberto(self) -> bool:
        with self._lock:
            return self._aberto_ate is not None and self._relogio() < self._aberto_ate
