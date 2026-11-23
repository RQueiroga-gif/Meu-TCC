# Assistente de DFD

Protótipo de automação assistida do **Documento de Formalização da Demanda**, com
recuperação de fundamentos normativos, geração condicionada por modelo de linguagem
e **supervisão humana obrigatória**.

TCC em Ciência da Computação — Ronan Rocha Queiroga (UDC).

> O sistema redige e fundamenta; o agente público confere, corrige e decide; a
> arquitetura registra cada passo e impede que essa ordem seja invertida.

---

## Como executar

Guia completo, com Windows, Docker, modelos reais e solução de problemas:
**[COMPILAR.md](COMPILAR.md)**. Resumo:

Pré-requisito: Python 3.11 ou superior.

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp ../.env.example ../.env         # Windows: Copy-Item ..\.env.example ..\.env
python -m scripts.seed             # cria usuários e indexa a base de amostra
uvicorn app.main:app --reload --port 8001
```

Documentação interativa da API: <http://localhost:8001/docs>

### 2. Frontend

Em outro terminal:

```bash
cd frontend
pip install -r requirements.txt
streamlit run app.py
```

Interface: <http://localhost:8501>

### Credenciais de desenvolvimento

| Perfil | E-mail | Senha |
|---|---|---|
| Demandante | `demandante@orgao.gov.br` | `demandante123` |
| Gestor da base | `gestor@orgao.gov.br` | `gestor123` |

### Com Docker

```bash
docker compose up --build
```

---

## Organização

```
dfd-assistido/
├── backend/                 API, regras de negócio e persistência
│   ├── app/
│   │   ├── config.py        configuração por ambiente
│   │   ├── database.py      conexão e aplicação do esquema (SQLite ou MySQL)
│   │   ├── models.py        modelo de objetos (Quadro 10)
│   │   ├── schemas.py       contratos de entrada e saída
│   │   ├── security.py      autenticação e perfis
│   │   ├── core/            segmentador, anonimizador, índice, recuperação,
│   │   │                    normalização, verificação, análise e auditoria
│   │   ├── llm/             interface ProvedorLLM e implementações
│   │   ├── routers/         rotas HTTP (inclui /indicadores)
│   │   └── services/        orquestrador, ingestão e exportador .docx
│   ├── db/                  DDL de cada dialeto (com os gatilhos)
│   ├── scripts/seed.py      povoamento (--forcar, --reindexar)
│   ├── tests/               144 testes automatizados
│   └── avaliacao/           gabarito, experimentos E1–E9, gráficos e tabelas do cap. 5
├── frontend/                interface Streamlit (fala só com a API)
├── ingestao/                texto oficial (oficial/, com SHA-256), amostras e download
└── docs/                    saídas, prints e resultados
```

---

## Banco de dados: SQLite ou MySQL

A troca é feita por **uma variável**, `DB_DIALETO`. O modelo lógico e as regras de
negócio são idênticos; cada dialeto tem seu DDL com os gatilhos equivalentes.

| | SQLite (padrão) | MySQL 8 |
|---|---|---|
| Infraestrutura | Nenhuma: um arquivo | Servidor |
| Escritores simultâneos | Um processo | Vários |
| Quando usar | Enquanto só a API escreve | Com mais de um processo escrevendo (várias instâncias da API) |

```bash
# MySQL
DB_DIALETO=mysql
DB_HOST=localhost
DB_NOME=dfd
DB_USUARIO=dfd
DB_SENHA=...
```

O esquema é aplicado automaticamente na subida da aplicação, de forma idempotente.

---

## Regras garantidas na camada de dados

As invariantes do trabalho não dependem de disciplina da aplicação: são gatilhos
do banco. Um script, um fluxo mal configurado ou um acesso direto ao banco não
consegue violá-las.

| Regra | Garantia |
|---|---|
| **RN01** | Nenhuma exportação sem validação humana registrada, e apenas da versão exata aprovada |
| **RN02** | Toda minuta fica vinculada aos trechos que a fundamentam (`minuta_trecho`) |
| **RN03** | A recuperação filtra por vigência: norma revogada não fundamenta minuta |
| **RN05** | Auditoria somente-inclusão: `UPDATE` e `DELETE` são rejeitados |
| **RN06** | CPF, CNPJ, e-mail, telefone e CEP são removidos antes de gravar ou enviar — inclusive na revisão humana |
| **RNF07** | Cada registro de auditoria encadeia o hash do anterior (índice único impede bifurcação concorrente): adulteração é detectável |

Verificação da cadeia de auditoria: `GET /auditoria/integridade` (perfil gestor).

---

## Três camadas contra a alucinação

1. **Portão de suficiência** — sem trecho acima do limiar de similaridade, a geração
   não ocorre e o usuário é orientado a reformular a demanda (fluxo A1).
2. **Saída estruturada** — o modelo declara, para cada campo redigido, os
   identificadores dos trechos que o sustentam; para os campos extraídos, copia o
   trecho literal da demanda como evidência. Nunca estima valores.
3. **Verificação por código** — confere se cada fundamento existe no contexto, se
   nenhum artigo citado está fora dos trechos recuperados e se cada evidência
   aparece de fato na demanda. O resultado vira métrica (`taxa_ancoragem`).

Além das três camadas, a **análise de conformidade** (`core/analise.py`) avalia
cada campo do art. 8º (conforme, atenção ou pendente), calcula um índice de
prontidão e aparece na revisão, na validação e no Anexo II do documento.

---

## O documento exportado

O `.docx` (baixado pela interface ou por
`GET /dfd/{id}/exportacoes/{id_exportacao}/arquivo`) contém: identificação do
DFD; os oito campos do art. 8º com a fundamentação resolvida em citações e a
origem de cada valor (extraído da demanda ou declarado pelo agente); o bloco de
validação humana; **Anexo I** com o texto integral dos dispositivos recuperados;
**Anexo II** com o relatório de verificação e conformidade; **Anexo III** com o
histórico de versões. Cada exportação tem arquivo próprio e hash SHA-256
conferido no download.

## Indicadores

`GET /indicadores` (perfil gestor, tela *Indicadores*) consolida taxa de
sucesso da geração, gerações barradas pelo portão de suficiência, ancoragem
média, taxa de inconsistência, versões por DFD, aprovações × devoluções e tempo
mediano até a aprovação — os números do capítulo de resultados.

---

## Recuperação: densa, lexical ou híbrida

`RECUPERACAO_MODO=automatico` (padrão) usa a fusão híbrida BGE-M3 + BM25 quando o
BGE-M3 está instalado e o índice lexical (BM25) quando não está. A escolha vem da
avaliação (capítulo 5, experimento E1): o híbrido recuperou 39,6% dos dispositivos
esperados (R@6) e acertou ao menos um em 68,8% das demandas (Hit@6), contra 24,2% e
43,8% do BGE-M3 sozinho e 36,5% e 56,2% do BM25. Em todos os modos, o portão de
suficiência usa o cosseno do gerador de embeddings, com limiar calibrado por
gerador (experimento E3; 0,52 para o BGE-M3).

---

## Provedores de modelo de linguagem

Selecionados por `LLM_PROVEDOR`, atrás da interface `ProvedorLLM` (RNF08).

| Valor | Uso |
|---|---|
| `echo` | Simulador determinístico, sem rede: só para os testes automatizados e para E1 a E8 (camadas sem modelo de linguagem). Não gera texto de qualidade |
| `groq` | Modelos abertos (gpt-oss, Qwen, Llama) hospedados pela Groq, API no formato OpenAI. Gerador do E9 |
| `gemini` | Modelo comercial do Google por API REST. Revisor de redação (RF13) e réplica do E9 |

Os dois provedores em nuvem são usados no nível gratuito, que limita requisições
e tokens por minuto e por dia; o sistema espera e tenta de novo quando recebe 429.
Para dados pessoais reais em produção, a interface permite acrescentar um modelo
local ou um plano pago com cláusulas de privacidade; esta versão não inclui nenhum.

### Revisão de texto por um segundo modelo

Na tela de revisão, o botão **Revisar texto** pede a um segundo modelo
(`LLM_PROVEDOR_CORRECAO`, de preferência outro fornecedor; `LLM_MODELO_CORRECAO`
opcional) a correção de redação dos campos I e II do art. 8º. Nada é gravado: o
agente compara o texto atual com o sugerido e decide. Ao aplicar, a sugestão vira
nova versão pelo fluxo de revisão (UC05), com o provedor e o modelo registrados na
versão e na auditoria (`CORRECAO_SUGERIDA`, `EDICAO`). A sugestão de um campo é
descartada se alterar menções a artigos ou normas, se alterar números (quantidade,
valor, data) ou se crescer mais que 50% + 200 caracteres. O texto vai minimizado
ao revisor (RN06).

---

## Testes

```bash
cd backend
python -m pytest
```

Cobrem a segmentação normativa (inclusive os casos do HTML oficial), a
minimização de dados pessoais, a verificação de citações, o índice lexical, as
regras de governança (testes T1 a T6), a delimitação dos dados do usuário no
prompt (v0.4) e o percurso completo do UC02 ao UC08 pela API. São 142 testes;
as mudanças de cada versão estão em [CHANGELOG.md](CHANGELOG.md).

## Avaliação (capítulo 6 do TCC)

```bash
cd backend
python -m avaliacao.executar                  # E1 a E8 (~35 min com o BGE-M3 em CPU)
python -m avaliacao.executar --somente-e9 --llm groq                 # E9: 32 demandas, 4 condições
python -m avaliacao.executar --somente-e9 --llm gemini --amostra 6   # réplica: 6 demandas, 4 condições
python -m avaliacao.executar --somente-e9 --llm groq --condicoes completo isolada   # só duas condições
```

O E9 compara o mesmo modelo em até quatro condições: **sistema completo**
(minimização, RAG no modo configurado, portão, esquema de saída e verificação),
**RAG só lexical** (`completo_bm25`: compara os métodos de recuperação de
ponta a ponta), **sistema sem RAG** e **LLM isolada** (a demanda enviada direto
ao modelo, em texto livre, sem nada do sistema). As métricas comuns são citações
por minuta, precisão das citações em relação ao gabarito, citações a artigos
inexistentes, citações a normas revogadas (Lei nº 8.666/1993 e outras) e valores
da demanda presentes no texto, com diferenças pareadas e IC 95% por bootstrap.
Desde a versão 0.4.5, nas condições do sistema as citações são contadas no texto
do documento, como ele é exportado: os campos redigidos com a linha de
fundamentação (as referências dos marcadores T1 a T6 que o modelo indicou) e os
valores extraídos; um marcador sem trecho correspondente conta como citação
inexistente. Para reaplicar essa medida a gerações feitas com a 0.4.4, sem chamar
o modelo de novo:

```bash
cd ../rodada_final && python extrair_minutas_e9.py      # copia as minutas dos bancos temporários
cd ../backend && python -m avaliacao.remedir_e9 --minutas ../rodada_final/e9_minutas/minutas_e9.jsonl
python -m avaliacao.executar --somente-e9 --llm groq    # refaz as tabelas (sem novas gerações)
python -m avaliacao.executar --somente-e9 --llm gemini --amostra 6
```

Desde a versão 0.4.1, o E9 completo (sem `--amostra` nem `--ids`) roda também as
6 demandas adversariais do gabarito nas condições `completo` e `isolada`: cada uma
tem um código-marcador que só aparece na minuta se o modelo obedecer à instrução
injetada (tabela `tab_e9_injecao`). Use `--sem-injecao` para pular essa parte.

Cada provedor/modelo fica numa entrada própria de `resumo.json` (`e9.execucoes`),
e cada geração é gravada em `resultados/e9/<provedor>_<modelo>.jsonl` assim que
termina, com o texto produzido. Se a execução parar (cota diária da Groq ou do Gemini, queda
de rede), rode o mesmo comando de novo: ela continua de onde parou. `--refazer`
descarta o progresso. No `--somente-e9`, os vetores do BGE-M3 ficam em cache
(`avaliacao/cache/`) e a segunda execução não espera a vetorização do corpus.

Roda em banco e índices temporários (não toca a base de desenvolvimento) e grava
em `backend/avaliacao/resultados/` o `resumo.json`, os CSV por demanda, os
gráficos (PDF/PNG) e as tabelas LaTeX usadas no capítulo. O BGE-M3 entra
automaticamente quando o `sentence-transformers` está instalado. O gabarito
(`avaliacao/dados/gabarito.yaml`) e os conjuntos de dados pessoais são fictícios.

---

## Ingestão do corpus real

O repositório inclui amostras em `ingestao/amostra/` para que o sistema funcione
imediatamente. Para o corpus oficial completo:

```bash
pip install pyyaml
python ingestao/baixar.py
```

O HTML bruto é arquivado com data e hash SHA-256; a reindexação só ocorre quando o
hash da fonte muda.

---

## Limitações declaradas

- A minimização de dados pessoais removeu 82% dos dados pessoais no conjunto de
  teste retido (com NER do spaCy); ficaram de fora telefones com prefixo "(045)" ou
  ponto como separador e nomes inteiramente em maiúsculas.
- O gerador de embeddings determinístico existe para testes e **não tem qualidade
  semântica**. A média de vetores do spaCy é fraca na ordenação e, com os 50
  pedidos fora do domínio da v0.4, também no portão: aceita pedidos
  administrativos que não são contratação. A recuperação de referência exige o
  BGE-M3 (`pip install sentence-transformers`).
- O portão de suficiência mede proximidade de assunto, não intenção: pedidos
  jurídico-administrativos sem contratação podem passar e seguem para a
  verificação e a revisão humana.
- A verificação automática confere citações e evidências, não a interpretação
  jurídica de um dispositivo corretamente citado: isso continua com o agente.
- O protótipo é prova de conceito avaliada em ambiente controlado, sem implantação
  em produção nem integração com sistemas institucionais reais.
