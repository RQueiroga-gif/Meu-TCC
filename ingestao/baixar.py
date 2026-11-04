"""Baixa os atos normativos do texto oficial e os ingere na base (F1 + F2).

Executar na máquina do pesquisador, que tem acesso às fontes oficiais:

    python ingestao/baixar.py                # baixa e indexa tudo de fontes.yaml
    python ingestao/baixar.py --forcar       # reindexa mesmo sem alteração na fonte
    python ingestao/baixar.py --sigla L14133 # apenas uma norma

O HTML bruto é guardado em ingestao/brutos/ com a data e o hash SHA-256, o que
materializa a "base normativa versionada" e permite detectar alteração na fonte.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from datetime import date
from pathlib import Path

import requests
import yaml

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "backend"))

from app.database import aplicar_esquema, sessao_escopo  # noqa: E402
from app.services.ingestao import ingerir  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger("ingestao")

FONTES = Path(__file__).parent / "fontes.yaml"
BRUTOS = Path(__file__).parent / "brutos"

CABECALHOS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; TCC-DFD/1.0; pesquisa academica; "
        "Ciencia da Computacao)"
    )
}


def baixar(url: str, tempo_limite: int = 90) -> bytes:
    """Obtém o HTML oficial, preservando os bytes originais para o hash."""
    log.info("baixando %s", url)
    resposta = requests.get(url, timeout=tempo_limite, headers=CABECALHOS)
    resposta.raise_for_status()
    return resposta.content


def guardar(bruto: bytes, sigla: str) -> Path:
    """Arquiva o HTML bruto com data e hash (procedência da fonte)."""
    BRUTOS.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(bruto).hexdigest()[:12]
    destino = BRUTOS / f"{sigla}_{date.today():%Y%m%d}_{digest}.html"
    destino.write_bytes(bruto)
    return destino


def main() -> int:
    analisador = argparse.ArgumentParser(description="Ingestão da base normativa")
    analisador.add_argument("--sigla", help="processa apenas a norma indicada")
    analisador.add_argument(
        "--forcar", action="store_true", help="reindexa mesmo sem alteração na fonte"
    )
    argumentos = analisador.parse_args()

    if not FONTES.exists():
        log.error("arquivo de fontes não encontrado: %s", FONTES)
        return 1

    fontes = yaml.safe_load(FONTES.read_text(encoding="utf-8")) or []
    if argumentos.sigla:
        fontes = [f for f in fontes if f["sigla"] == argumentos.sigla]
        if not fontes:
            log.error("sigla não encontrada em fontes.yaml: %s", argumentos.sigla)
            return 1

    aplicar_esquema()
    falhas = 0

    for fonte in fontes:
        try:
            bruto = baixar(fonte["url"])
        except requests.RequestException as erro:
            log.error("%s: falha no download (%s)", fonte["sigla"], erro)
            falhas += 1
            continue

        arquivo = guardar(bruto, fonte["sigla"])
        log.info("%s: %d bytes arquivados em %s", fonte["sigla"], len(bruto), arquivo.name)

        try:
            with sessao_escopo() as sessao:
                resultado = ingerir(
                    sessao,
                    bruto=bruto,
                    sigla=fonte["sigla"],
                    tipo=fonte["tipo"],
                    numero=fonte["numero"],
                    ano=fonte["ano"],
                    ementa=fonte["ementa"],
                    url_fonte=fonte["url"],
                    forcar=argumentos.forcar,
                )
            log.info("%s", resultado)
        except Exception as erro:  # noqa: BLE001
            log.error("%s: falha na ingestão (%s)", fonte["sigla"], erro)
            falhas += 1

    if falhas:
        log.warning("concluído com %d falha(s)", falhas)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
