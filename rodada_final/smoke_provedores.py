"""Teste minimo dos provedores reais: uma chamada curta a cada modelo configurado.
Nao imprime chaves. Executado a partir da pasta backend."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import config  # noqa: E402
from app.llm.factory import obter_provedor  # noqa: E402

print("Configuracao: GROQ_MODELO=%s | GEMINI_MODELO=%s | revisao=%s/%s" % (
    config.GROQ_MODELO, config.GEMINI_MODELO, config.LLM_PROVEDOR_CORRECAO, config.LLM_MODELO_CORRECAO))
falhas = 0
for nome, modelo in [("groq", None), ("gemini", None), ("gemini", config.LLM_MODELO_CORRECAO or None)]:
    rotulo = "%s [%s]" % (nome, modelo or "padrao")
    try:
        r = obter_provedor(nome, modelo=modelo).completar("Responda apenas: OK", "teste", formato_json=False)
        extras = {k: v for k, v in (r.parametros or {}).items() if "prompt" not in k}
        print("%s OK | modelo informado pelo provedor=%r | resposta=%r | %s" % (rotulo, r.modelo, r.texto.strip()[:40], extras))
    except Exception as erro:  # noqa: BLE001
        falhas += 1
        print("%s FALHOU | %s: %s" % (rotulo, type(erro).__name__, str(erro)[:400]))
raise SystemExit(1 if falhas else 0)
