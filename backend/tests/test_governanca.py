"""Testes de governanca: RN01 (trava de exportacao) e RN05 (auditoria).

Correspondem aos testes T1 a T6 do planejamento do capitulo de implementacao e
sustentam os criterios "Supervisao humana" e "Governanca de dados" do Quadro 2.

O ponto verificado aqui e que as invariantes vivem na camada de dados: as
tentativas de violacao sao feitas por SQL direto, contornando a API, e ainda
assim sao rejeitadas.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core import auditoria
from app.models import (
    Demanda,
    DocumentoDFD,
    Exportacao,
    Minuta,
    Usuario,
    Validacao,
)


@pytest.fixture
def dfd_com_minuta(sessao):  # noqa: ANN001
    """Um DFD com uma versao de minuta, sem validacao."""
    usuario = sessao.query(Usuario).filter_by(email="demandante@orgao.gov.br").one()
    demanda = Demanda(
        id_usuario=usuario.id_usuario,
        unidade_requisitante="Protocolo",
        titulo="Aquisição de equipamentos de informática",
        descricao="Aquisição de 40 notebooks para o setor de protocolo.",
        situacao="em_elaboracao",
    )
    sessao.add(demanda)
    sessao.flush()

    dfd = DocumentoDFD(id_demanda=demanda.id_demanda, exercicio=2026)
    sessao.add(dfd)
    sessao.flush()

    minuta = Minuta(
        id_dfd=dfd.id_dfd,
        versao=1,
        conteudo={"justificativa_necessidade": {"texto": "t", "fundamentos": ["T1"]}},
        origem="geracao",
        id_usuario=usuario.id_usuario,
        modelo_llm="echo",
        versao_prompt="p-1.0",
    )
    sessao.add(minuta)
    sessao.flush()
    return dfd, minuta, usuario


# ------------------------------------------------------------------ RN01
def test_t1_exportar_sem_validacao_e_rejeitado(sessao, dfd_com_minuta):  # noqa: ANN001
    """T1 — a trava impede exportacao sem decisao humana registrada."""
    dfd, minuta, usuario = dfd_com_minuta
    sessao.add(
        Exportacao(
            id_dfd=dfd.id_dfd,
            id_minuta=minuta.id_minuta,
            id_usuario=usuario.id_usuario,
            hash_arquivo="0" * 64,
        )
    )
    with pytest.raises((IntegrityError, OperationalError)) as erro:
        sessao.flush()
    assert "RN01" in str(erro.value)
    sessao.rollback()


def test_t2_exportar_versao_aprovada_e_aceito(sessao, dfd_com_minuta):  # noqa: ANN001
    """T2 — com a validacao registrada, a exportacao passa e a situacao muda."""
    dfd, minuta, usuario = dfd_com_minuta
    sessao.add(
        Validacao(
            id_dfd=dfd.id_dfd,
            id_minuta=minuta.id_minuta,
            id_usuario=usuario.id_usuario,
            decisao="aprovado",
        )
    )
    sessao.flush()

    sessao.add(
        Exportacao(
            id_dfd=dfd.id_dfd,
            id_minuta=minuta.id_minuta,
            id_usuario=usuario.id_usuario,
            hash_arquivo="1" * 64,
        )
    )
    sessao.flush()
    sessao.expire(dfd)
    assert dfd.situacao == "exportado"


def test_t3_exportar_versao_antiga_ou_nao_aprovada_e_rejeitado(sessao, dfd_com_minuta):  # noqa: ANN001
    """T3 — aprovar a v1 e depois gerar a v2 invalida a exportacao de ambas."""
    dfd, minuta_v1, usuario = dfd_com_minuta
    sessao.add(
        Validacao(
            id_dfd=dfd.id_dfd,
            id_minuta=minuta_v1.id_minuta,
            id_usuario=usuario.id_usuario,
            decisao="aprovado",
        )
    )
    minuta_v2 = Minuta(
        id_dfd=dfd.id_dfd,
        versao=2,
        conteudo={"justificativa_necessidade": {"texto": "revisado", "fundamentos": ["T1"]}},
        origem="edicao",
        id_usuario=usuario.id_usuario,
    )
    sessao.add(minuta_v2)
    sessao.flush()

    # v1 (aprovada, porem nao e a mais recente)
    sessao.add(
        Exportacao(
            id_dfd=dfd.id_dfd, id_minuta=minuta_v1.id_minuta, id_usuario=usuario.id_usuario
        )
    )
    with pytest.raises((IntegrityError, OperationalError)) as erro:
        sessao.flush()
    assert "versao mais recente" in str(erro.value)
    sessao.rollback()


def test_t4_versao_nao_pode_ser_sobrescrita(sessao, dfd_com_minuta):  # noqa: ANN001
    """T4 — a restricao de unicidade preserva o historico."""
    dfd, _, usuario = dfd_com_minuta
    sessao.add(
        Minuta(
            id_dfd=dfd.id_dfd,
            versao=1,                      # ja existe
            conteudo={},
            origem="edicao",
            id_usuario=usuario.id_usuario,
        )
    )
    with pytest.raises(IntegrityError):
        sessao.flush()
    sessao.rollback()


# ------------------------------------------------------------------ RN05
def test_t5_auditoria_e_somente_inclusao(sessao):  # noqa: ANN001
    """T5 — UPDATE e DELETE na auditoria sao rejeitados pelo banco."""
    usuario = sessao.query(Usuario).filter_by(email="demandante@orgao.gov.br").one()
    registro = auditoria.registrar(
        sessao, id_usuario=usuario.id_usuario, acao="TESTE", detalhe={"x": 1}
    )
    sessao.commit()

    with pytest.raises((IntegrityError, OperationalError)) as erro:
        sessao.execute(
            text("UPDATE registro_auditoria SET acao = 'ADULTERADO' WHERE id_registro = :i"),
            {"i": registro.id_registro},
        )
    assert "somente-inclusao" in str(erro.value)
    sessao.rollback()

    with pytest.raises((IntegrityError, OperationalError)):
        sessao.execute(
            text("DELETE FROM registro_auditoria WHERE id_registro = :i"),
            {"i": registro.id_registro},
        )
    sessao.rollback()


def test_cadeia_de_hashes_encadeia_e_verifica(sessao):  # noqa: ANN001
    usuario = sessao.query(Usuario).filter_by(email="gestor@orgao.gov.br").one()
    primeiro = auditoria.registrar(sessao, id_usuario=usuario.id_usuario, acao="A")
    segundo = auditoria.registrar(sessao, id_usuario=usuario.id_usuario, acao="B")
    sessao.commit()

    assert segundo.hash_anterior == primeiro.hash_registro
    assert auditoria.verificar_cadeia(sessao)["integra"] is True


def test_t6_adulteracao_com_gatilho_desativado_e_detectada(sessao):  # noqa: ANN001
    """T6 — acesso privilegiado consegue alterar, mas a cadeia denuncia."""
    usuario = sessao.query(Usuario).filter_by(email="gestor@orgao.gov.br").one()
    alvo = auditoria.registrar(sessao, id_usuario=usuario.id_usuario, acao="ORIGINAL")
    auditoria.registrar(sessao, id_usuario=usuario.id_usuario, acao="POSTERIOR")
    sessao.commit()

    # Simula acesso privilegiado: remove o gatilho e altera o registro.
    sessao.execute(text("DROP TRIGGER IF EXISTS trg_auditoria_sem_update"))
    sessao.execute(
        text("UPDATE registro_auditoria SET acao = 'ADULTERADO' WHERE id_registro = :i"),
        {"i": alvo.id_registro},
    )
    sessao.commit()

    resultado = auditoria.verificar_cadeia(sessao)
    assert resultado["integra"] is False
    assert resultado["primeiro_divergente"] == alvo.id_registro
