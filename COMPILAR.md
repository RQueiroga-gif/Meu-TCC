# Como instalar, compilar e executar

Guia passo a passo do Assistente de DFD. Python não gera executável: "compilar"
aqui significa **instalar as dependências, preparar a base e subir os dois
serviços** (API e interface). Há dois caminhos:

- **A. Local, sem Docker** — recomendado para desenvolver e para a banca.
- **B. Docker Compose** — sobe MySQL, API e interface com um comando.

---

## Pré-requisitos

| Item | Versão | Como conferir |
|---|---|---|
| Python | 3.11 ou superior (testado com 3.13) | `python --version` |
| pip | acompanha o Python | `python -m pip --version` |
| Git | qualquer | `git --version` |
| Docker Desktop | só para o caminho B | `docker compose version` |

> **Windows:** marque *"Add python.exe to PATH"* ao instalar o Python. Se o
> PowerShell recusar ativar o ambiente virtual, rode uma única vez:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

Todos os comandos abaixo partem da pasta do projeto (`dfd-assistido/`, a que
contém `backend/`, `frontend/` e este arquivo).

---

## A. Execução local (sem Docker)

### 1. Configuração (`.env`)

```powershell
# Windows (PowerShell)
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```
```bash
# Linux / macOS
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Abra o `.env` e cole a chave gerada em `SECRET_KEY=`. Os demais valores padrão
já funcionam: SQLite, provedor `echo` (sem rede, sem custo) e gerador de
embeddings de fallback. Sem trocar a chave, a API sobe, mas avisa no log que os
tokens podem ser forjados.

### 2. Backend (API)

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1            # Linux/macOS: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Prepare o banco, os usuários e a base normativa de amostra:

```powershell
python -m scripts.seed
```

Saída esperada (resumida), com o texto oficial de `ingestao/oficial/`:

```
INFO     usuário criado: demandante@orgao.gov.br (demandante)
INFO     usuário criado: gestor@orgao.gov.br (gestor)
INFO     L14133: 1249 dispositivos (atualizada)
INFO     D10947: 75 dispositivos (atualizada)
INFO     L13709: 410 dispositivos (atualizada)
INFO     Seed concluído.
```

> **Atualizando da versão 0.2.0?** Rode `pip install -r requirements.txt` (há
> dependências novas: `html5lib`, `snowballstemmer`) e depois
> `python -m scripts.seed --forcar`, para ressegmentar as normas com o
> segmentador corrigido e carregar o texto oficial integral.

> O aviso *"Modelo de embeddings indisponível … usando gerador determinístico"*
> é normal sem o `sentence-transformers` (veja a seção C).

Confira a instalação com os testes automatizados (88 testes, ~15 s):

```powershell
python -m pytest
```

Suba a API:

```powershell
uvicorn app.main:app --reload --port 8001
```

- Documentação interativa: <http://localhost:8001/docs>
- Diagnóstico: <http://localhost:8001/saude>

Deixe este terminal aberto.

### 3. Frontend (interface)

Em **outro terminal**, a partir da pasta do projeto:

```powershell
cd frontend
python -m venv .venv
.venv\Scripts\Activate.ps1            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Abra <http://localhost:8501> e entre com:

| Perfil | E-mail | Senha |
|---|---|---|
| Demandante | `demandante@orgao.gov.br` | `demandante123` |
| Gestor da base | `gestor@orgao.gov.br` | `gestor123` |

Se a API estiver em outro endereço, defina `API_URL` antes do `streamlit run`
(PowerShell: `$env:API_URL="http://host:8001"`).

### 4. Roteiro de verificação (5 minutos)

1. **Demandante → Nova demanda.** Descreva, por exemplo: *"Aquisição de 40
   notebooks para os laboratórios das escolas, prioridade alta, estimativa de
   R$ 180.000,00, conclusão em 30/06/2027, conforme o plano de contratações
   anual."* → **Gerar minuta preliminar**.
2. **Revisão:** confira as fontes à direita e a aba **Conformidade**; edite um
   campo e clique **Salvar revisão** (vira a versão 2).
3. **Validação:** confirme os 8 campos, marque a declaração, **Aprovar DFD**.
4. **Exportar DFD em .docx → Preparar download → Baixar .docx.** O arquivo traz
   os campos do art. 8º, a validação e os anexos I a III.
5. **Gestor → Indicadores** e **Base normativa** (cadeia de auditoria íntegra,
   vigência das normas, consulta à base).

---

## B. Docker Compose

```powershell
Copy-Item .env.example .env          # ajuste SECRET_KEY e, se quiser, o provedor
docker compose up --build
```

- Interface: <http://localhost:8501> · API: <http://localhost:8001/docs>
- O backend roda o seed sozinho na subida (idempotente) e usa MySQL 8.4.
- Banco, índice vetorial e arquivos exportados ficam em volumes Docker.
- Recomeçar do zero: `docker compose down -v`

---

## C. Modelos reais (necessário para as métricas do TCC)

O gerador determinístico e o provedor `echo` só validam o fluxo. Para os
experimentos:

### Embeddings BGE-M3 (local)

```powershell
cd backend
pip install sentence-transformers     # traz o PyTorch; ~2 GB de download
```

No `.env`: `EMBEDDING_MODELO=BAAI/bge-m3`. Depois, **reconstrua o índice** (os
vetores antigos pertencem a outro espaço e o sistema se recusa a misturá-los):

```powershell
python -m scripts.seed --reindexar
```

(ou, na interface, **Gestor → Base normativa → Reindexar a base**). A primeira
execução baixa o modelo e demora alguns minutos.

### Modelo de linguagem

| Provedor | `.env` | Observação |
|---|---|---|
| Gemini (nuvem) | `LLM_PROVEDOR=gemini`, `GEMINI_API_KEY=…`, `GEMINI_MODELO=gemini-3.6-flash` | Chave em aistudio.google.com. No nível gratuito o Google pode usar o conteúdo enviado: só demandas fictícias. Espera automática em 429/503 |
| Groq (nuvem, gerador do E9) | `LLM_PROVEDOR=groq`, `GROQ_API_KEY=…`, `GROQ_MODELO=openai/gpt-oss-120b` | Chave em console.groq.com. Nível gratuito com limite por minuto e por dia; respeita o `retry-after`. Só demandas fictícias até a política de dados constar no TCC |
| Revisão de texto | `LLM_PROVEDOR_CORRECAO=gemini`, `LLM_MODELO_CORRECAO=…` (opcional) | Segundo modelo do botão "Revisar texto"; vazio = o mesmo do `LLM_PROVEDOR` |
| Echo | `LLM_PROVEDOR=echo` | Simulador determinístico, sem rede: só para testes |

Reinicie a API após mudar o `.env`.

### Corpus normativo completo

```powershell
cd backend
python ..\ingestao\baixar.py              # L14133, D10947 e LGPD do Planalto
python ..\ingestao\baixar.py --sigla L14133 --forcar
```

---

### spaCy: nomes de pessoas (NER) e embeddings leves

Opcional, mas recomendado (≈ 45 MB): ativa o reconhecimento de nomes na
minimização de dados pessoais (RN06) e o gerador `spacy:pt_core_news_md`.

```powershell
pip install spacy
pip install https://github.com/explosion/spacy-models/releases/download/pt_core_news_md-3.8.0/pt_core_news_md-3.8.0-py3-none-any.whl
```

### Avaliação do capítulo 6

```powershell
cd backend
python -m avaliacao.executar                    # E1 a E8 (~35 min com o BGE-M3)
python -m avaliacao.executar --somente-e9 --llm groq               # 32 demandas, 4 condições e 6 adversariais
python -m avaliacao.executar --somente-e9 --llm gemini --amostra 6 # réplica: 6 demandas, 4 condições
```

Os dois provedores são usados no nível gratuito. O E9 salva cada geração assim
que termina; quando a cota do dia acaba (ou a rede cai), repita o mesmo comando
no dia seguinte e ele continua de onde parou. O E9 da Groq leva vários dias.
Gerações feitas com a versão 0.4.4 precisam da medida corrigida da 0.4.5 antes das
tabelas: veja `python -m avaliacao.remedir_e9` no README (seção D).

Resultados em `backend/avaliacao/resultados/` (copie `figuras/*.pdf` e
`latex/*.tex` para o projeto LaTeX, pastas `figuras/resultados/` e `tabelas/`).

---

## D. Comandos de manutenção

| Objetivo | Comando (na pasta `backend`, com o `.venv` ativo) |
|---|---|
| Rodar os testes | `python -m pytest` |
| Recriar usuários e amostras | `python -m scripts.seed` |
| Ressegmentar as normas | `python -m scripts.seed --forcar` |
| Reconstruir o índice vetorial | `python -m scripts.seed --reindexar` |
| Zerar a base local | apague `backend/db/dfd.sqlite3*` e `backend/db/indice.npz`, depois rode o seed |

---

## E. Problemas comuns

| Sintoma | Causa e solução |
|---|---|
| *"Não foi possível falar com a API em http://localhost:8001"* | A API não está rodando ou está em outra porta. Suba o `uvicorn` (passo A.2) ou defina `API_URL`. |
| Toda geração responde *"Não foram encontrados fundamentos normativos suficientes"* | Índice vazio ou limiar alto demais. Veja **Gestor → Base normativa**: se "Trechos no índice vetorial" for 0 ou diferente do banco, rode `python -m scripts.seed --reindexar`. |
| *"A base normativa precisa ser reindexada"* | O gerador de embeddings mudou desde a indexação. Rode `python -m scripts.seed --reindexar`. |
| *"O serviço de geração está temporariamente indisponível"* | Falha do provedor de LLM (chave ausente ou errada, cota esgotada, rede). O motivo fica em **Registros de auditoria** (ação `FALHA_GERACAO`) e no log da API. |
| *"Muitas tentativas sem sucesso"* no login | Bloqueio de 15 min após 5 senhas erradas. Reinicie a API para liberar em desenvolvimento. |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `database is locked` | Dois processos escrevendo no mesmo SQLite (ex.: seed com a API aberta sob carga). Pare a API, rode o seed, suba de novo — ou use MySQL. |
