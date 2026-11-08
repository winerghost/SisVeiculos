"""Pico de memória da recodificação no limite de pixels (DoS por OOM com o mem_limit do container).

Mede o RSS de pico (ru_maxrss) num processo FILHO limpo, só com o módulo de imagem importado,
para o PNG RGBA e o WebP lossless de tamanho máximo. Só roda onde há `resource` (Linux/CI).
Números de referência (Pillow 13.0.0, Alpine, 4032x3024): PNG RGBA ~60 MB, WebP ~190 MB; antes
da redução em faixas: PNG ~150 MB (4000x4000: ~200 MB) e duas simultâneas ~400 MB.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from app.services import imagem_perfil
from tests.imagens import salvar

pytest.importorskip("resource")

RAIZ = Path(__file__).resolve().parent.parent
LARGURA, ALTURA = 4032, 3024
LIMITE_PNG_MB = 120  # pico por recodificação (acima da base do processo)
LIMITE_WEBP_MB = 230  # libwebp/Pillow fazem cópias internas; uma por vez (SIMULTANEAS=1)
MEM_LIMIT_MB = 512
BASE_PROCESSO_MB = 200  # folga para Flask + supabase + buffers do processo real

SCRIPT = """
import resource, sys, threading
dados = open(sys.argv[1], "rb").read()
mime, n = sys.argv[2], int(sys.argv[3])
from app.services import imagem_perfil as ip
def rss(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
base = rss()
def run():
    try: ip.recodificar(dados, mime)
    except Exception: pass
ts = [threading.Thread(target=run) for _ in range(n)]
[t.start() for t in ts]; [t.join() for t in ts]
print(rss() - base)
"""


def _medir(tmp_path: Path, dados: bytes, mime: str, simultaneas: int) -> float:
    arq = tmp_path / "imagem.bin"
    arq.write_bytes(dados)
    r = subprocess.run(  # noqa: S603 - interpretador atual, script fixo
        [sys.executable, "-c", SCRIPT, str(arq), mime, str(simultaneas)],
        cwd=RAIZ,
        env={"PYTHONPATH": str(RAIZ)},
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return float(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def png_maximo() -> bytes:
    return salvar(Image.new("RGBA", (LARGURA, ALTURA), (10, 200, 30, 128)), "PNG")


@pytest.fixture(scope="module")
def webp_maximo() -> bytes:
    return salvar(Image.new("RGB", (LARGURA, ALTURA), (10, 200, 30)), "WEBP", lossless=True)


def test_limite_de_pixels_e_o_do_teste():
    assert imagem_perfil.MAX_PIXELS == LARGURA * ALTURA


def test_pico_do_png_rgba_no_limite_de_pixels(tmp_path, png_maximo):  # type: ignore[no-untyped-def]
    pico = _medir(tmp_path, png_maximo, "image/png", 1)
    assert pico < LIMITE_PNG_MB, f"pico {pico:.0f} MB"


def test_pico_do_webp_lossless_no_limite_de_pixels(tmp_path, webp_maximo):  # type: ignore[no-untyped-def]
    pico = _medir(tmp_path, webp_maximo, "image/webp", 1)
    assert pico < LIMITE_WEBP_MB, f"pico {pico:.0f} MB"


def test_varias_simultaneas_cabem_no_mem_limit(tmp_path, png_maximo, webp_maximo):  # type: ignore[no-untyped-def]
    """Com a vaga única, a concorrência não soma os picos: o pior caso continua cabendo."""
    assert imagem_perfil.SIMULTANEAS == 1
    for dados, mime, limite in (
        (png_maximo, "image/png", LIMITE_PNG_MB),
        (webp_maximo, "image/webp", LIMITE_WEBP_MB),
    ):
        pico = _medir(tmp_path, dados, mime, 4)
        assert pico < limite * 1.2, f"pico {pico:.0f} MB"
        assert BASE_PROCESSO_MB + pico < MEM_LIMIT_MB * 0.8
