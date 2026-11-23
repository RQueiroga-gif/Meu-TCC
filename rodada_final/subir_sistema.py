"""Sobe a API e a interface para a inspeção heurística manual (roteiro_avaliacao_heuristica.md).

Usa o banco e o índice criados por ao_vivo.py (rodada_final/ao_vivo/dados) e os
provedores do .env (Groq gera, Gemini revisa). Abre o navegador e mantém tudo no ar
até você pressionar Enter nesta janela.
"""
from __future__ import annotations

import argparse
import sys
import webbrowser

import ao_vivo as av


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--py-frontend", default=sys.executable)
    ap.add_argument("--porta-api", type=int, default=8011)
    ap.add_argument("--porta-ui", type=int, default=8511)
    args = ap.parse_args()
    if not (av.DADOS / "indice.npz").exists():
        sys.exit("Rode antes o B_sistema_ao_vivo.bat: ele cria o banco e o índice usados aqui.")
    av.LOGS.mkdir(parents=True, exist_ok=True)
    opcoes = argparse.Namespace(simulador=False, provedor="groq", revisor="gemini")
    env = av.ambiente(opcoes)
    url_api = f"http://127.0.0.1:{args.porta_api}"
    url_ui = f"http://127.0.0.1:{args.porta_ui}"
    api = av.iniciar([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                      "--port", str(args.porta_api)], av.BACKEND, env, "inspecao_api.log")
    ui = av.iniciar([args.py_frontend, "-m", "streamlit", "run", "app.py", "--server.port",
                     str(args.porta_ui), "--server.headless", "true", "--server.address", "127.0.0.1",
                     "--browser.gatherUsageStats", "false"], av.FRONTEND,
                    {**env, "API_URL": url_api}, "inspecao_interface.log")
    try:
        if av.esperar_url(f"{url_api}/saude", 300) and av.esperar_url(url_ui, 120):
            print(f"Sistema no ar: {url_ui}")
            print("Demandante: demandante@orgao.gov.br / demandante123")
            print("Gestor:     gestor@orgao.gov.br / gestor123")
            webbrowser.open(url_ui)
            input("Pressione Enter aqui para encerrar o sistema... ")
        else:
            print("A API ou a interface não respondeu; veja rodada_final/ao_vivo/logs.")
    finally:
        av.parar(api)
        av.parar(ui)


if __name__ == "__main__":
    main()
