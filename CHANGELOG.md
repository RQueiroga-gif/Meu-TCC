# Histórico de versões

## 0.4.5 — 23 de novembro de 2026

Correção da medida de citações do E9. A aplicação não mudou; mudou só o roteiro de
avaliação (`backend/avaliacao`).

- **Defeito:** nas condições do sistema (completo, RAG só lexical e sem RAG), as
  citações eram contadas apenas no texto dos campos redigidos. O sistema, porém, não
  escreve as citações no texto: o modelo indica os marcadores dos trechos (T1 a T6)
  que fundamentam cada campo, e o documento exportado os mostra na linha
  "Fundamentação". A contagem antiga deixava quase todas as citações do sistema fora
  da medida e tornava indefinidas a precisão e a taxa de citações inexistentes, o que
  impediria o teste da hipótese. Achado ao conferir os primeiros resultados do E9,
  antes de qualquer análise.
- **Correção:** `documento_e9` monta o texto como o documento exportado (campos,
  linha de fundamentação e valores) e `medir_citacoes_sistema` conta as citações
  sobre ele; um marcador sem trecho correspondente no contexto conta como citação
  inexistente. A LLM isolada continua medida sobre o próprio texto. O checkpoint
  passa a guardar `fundamentacao` e `marcadores_invalidos`.
- **Gerações já feitas:** `python -m avaliacao.remedir_e9` reaplica a medida às linhas
  gravadas, com os marcadores copiados dos bancos temporários das execuções por
  `rodada_final/extrair_minutas_e9.py`; guarda o checkpoint original em
  `*.antes_remedicao.jsonl`. Nenhum modelo é chamado de novo.
- **Conferência manual das citações "inexistentes":** a contagem automática apontou 12
  citações a artigo inexistente na LLM isolada da Groq e 2 na do Gemini. Conferidas uma a
  uma, nenhuma é inexistente: oito remetem à Constituição Federal ou à LDB, normas fora da
  base que o extrator não reconhece, e seis são artigos da Lei nº 14.133/2021 listados em
  tabelas, que o extrator associa ao decreto do título seguinte. A conferência fica em
  `avaliacao/dados/auditoria_e9.yaml`; `remedir_e9` desconta só o que a medida apontou
  (confere cada entrada) e guarda a contagem original em `inexistentes_automaticas`.
- Comparação pareada também para as minutas que citam norma revogada.
- **Detector da injeção:** o canário escrito com acento ("CANÁRIO-5150") não era
  reconhecido; os acentos passam a ser retirados antes da comparação.
- Nota da tabela do E9 descreve a medida e a conferência. Quatro testes novos (146 no total).

## 0.4.4 — 22 de novembro de 2026

Escopo final de provedores: só Groq e Gemini como modelos reais.

- **Provedor Ollama retirado** (`ProvedorOllama`, `OLLAMA_URL`, `OLLAMA_MODELO`,
  valor `ollama` de `LLM_PROVEDOR` e de `--llm`). Nunca foi usado na avaliação.
  Um `.env` antigo com essas variáveis continua funcionando: elas são ignoradas.
- **Papel do provedor `echo`** explicitado: simulador determinístico, sem rede,
  restrito aos testes automatizados e aos experimentos E1 a E8.
- **Interface, fluxos A1 e A2:** a caixa "Não foi possível gerar a minuta" recolhia
  ao passar para erro e escondia o motivo e a orientação ("a demanda foi preservada;
  reformule..."). Agora fica aberta (`frontend/pages/1_Registrar_demanda.py`).
  Achado do teste ao vivo com o navegador automatizado (rodada final).
- **Tabelas geradas:** a do portão (E3) e a do E9 ajustam a largura à mancha do texto
  (`avaliacao/relatorio.py`, parâmetro `ajustar_largura`); a do E9 passa a se chamar
  "Resultados do E9 por provedor e condição", porque inclui as quatro condições.
- Um modelo local continua possível como nova implementação de `ProvedorLLM`
  (RNF08), como condição para dados pessoais reais em produção; não faz parte
  desta prova de conceito.

## 0.4.3 — 22 de novembro de 2026

Só provedores de modelo de linguagem gratuitos. Nenhuma regra de negócio mudou.

- **Provedor Anthropic retirado:** classe `ProvedorAnthropic`, dependência
  `anthropic`, variáveis `ANTHROPIC_API_KEY` e `LLM_MODELO`, valor `anthropic` de
  `LLM_PROVEDOR` e os testes do provedor. Restam Groq, Gemini, Ollama e o modo eco.
  Um `.env` antigo com essas variáveis continua funcionando: elas são ignoradas.
- **E9 com provedores gratuitos:** a Groq (`openai/gpt-oss-120b`, temperatura
  zero) é o gerador, nas 32 demandas e nas quatro condições, com as 6 demandas
  adversariais; o Gemini (`gemini-3.6-flash`) é a réplica, nas quatro condições
  com 6 demandas (`--amostra 6`), e segue como revisor de redação (RF13).
- **`--condicoes` passa a ter as quatro condições como padrão** (antes: três, sem
  `completo_bm25`). O comando do E9 da Groq fica `--somente-e9 --llm groq`.
- **Testes:** 142 (saem os 2 da versão 0.4.2; os do E9 usam a Groq).
- README e COMPILAR.md: tabela de provedores, comandos do E9 e capítulo 6 do TCC.

## 0.4.2 — 22 de novembro de 2026

Correção de compatibilidade do provedor Anthropic. Nenhuma regra de negócio mudou.

- **Erro corrigido:** com o SDK Python atual, a geração pela Anthropic falhava com
  `TypeError: Messages.create() got an unexpected keyword argument 'temperature'`.
  O SDK retirou `temperature` da assinatura de `messages.create`, e a versão
  2026-10-21 da API recusa o parâmetro com erro 400.
- **Como ficou:** quando o modelo aceita temperatura (Claude Haiku 4.5, por
  exemplo), ela vai no corpo da requisição (`extra_body`) e a requisição fixa a
  versão `2023-06-01` da API, que ainda a aceita. Assim a temperatura zero do
  desenho do E9 continua valendo. Se a API recusar a temperatura também nessa
  versão, a minuta é gerada sem ela, e a versão gravada deixa de registrar
  `temperatura` (`app/llm/provedores.py`, `ProvedorAnthropic._criar`).
- **Testes:** 2 novos em `tests/test_provedores.py`, com um cliente falso que,
  como o SDK atual, não aceita `temperature` como argumento. Total: 144 testes.

## 0.4.1 — 22 de novembro de 2026

Atende a três apontamentos da avaliação de 11/11/2026 (itens 7, 8 e 9). Nenhuma
regra de negócio mudou e nenhum número de E1, E2, E4, E5 e E7 se alterou: a
reexecução completa desta versão (Linux, sem o BGE-M3) reproduziu os valores de
27/10 para os geradores disponíveis.

### Avaliação
- **Injeção de prompt testada com modelo real (E9).** O gabarito (versão 1.2)
  ganhou 6 demandas adversariais (A01–A06): pedidos legítimos com uma instrução
  injetada (instrução direta, fechamento do delimitador, falso fundamento
  normativo, troca de papel, pedido de vazamento das instruções e instrução em
  texto citado). Cada uma traz um código-marcador (canário) que só aparece na
  minuta se o modelo obedecer à instrução. O E9 roda essas 6 demandas nas
  condições `completo` e `isolada` e reporta quantas obedeceram, com IC de
  Wilson (`e9_injecao`, tabela `tab_e9_injecao`). É pulado com `--amostra`,
  `--ids` ou `--sem-injecao`. Custo: 12 gerações por provedor.
- **Versões dos pacotes no `resumo.json`** (`ambiente.pacotes`), para que o
  Quadro de tecnologias do TCC tenha origem verificável.
- **Resultados versionados.** `avaliacao/resultados/` continua com a execução
  de 27/10 (v0.3, Windows, com o BGE-M3). A execução completa da v0.4.1 está em
  `avaliacao/resultados_2026-11-22_v0.4.1_linux_sem_bge/`; é dela que vêm, no
  TCC, o E3 com 50 pedidos (hash, spaCy, BM25) e o E8 com 142 testes.

- **Tabelas LaTeX no formato do modelo da UDC (2025-11).** A fonte e a nota das
  tabelas geradas (`relatorio.py`) usam os comandos `\fonte` e `\nota` do abnTeX2,
  de modo que as tabelas da reexecução entram no TCC sem edição.

### Portão de suficiência
- **Limiar do spaCy de 0,64 para 0,74**, o limiar sugerido pela validação
  cruzada com os 50 pedidos fora do domínio. Com 0,64 o portão aceitava 42%
  desses pedidos; com 0,74 aceita 26% (IC 16%–40%) e 91% das demandas. O spaCy
  continua não recomendado como gerador do portão (mede assunto, não intenção)
  e não é usado por padrão: sem o BGE-M3, o sistema cai no gerador por hash
  (`core/embeddings.py`, `obter_embedder`).

### Testes
- 3 testes novos em `tests/test_avaliacao_e9.py` (canários únicos, detecção do
  canário com variações de grafia, contagem por condição). Total: 142 testes,
  todos aprovados; cobertura de 86,7%.

### Para fazer na sua máquina
1. Use um ambiente com `sentence-transformers` e `spacy` (o ambiente de 27/10
   tem os dois; o de 11/11 não tem) e rode `python -m avaliacao.executar`.
2. Rode o E9 completo: as demandas adversariais entram automaticamente.

## 0.4.0 — 8 de novembro de 2026

Revisão de código para a versão final do TCC. Nenhuma regra de negócio mudou:
os gatilhos RN01 e RN05, o portão de suficiência, a verificação e a minimização
seguem iguais. Os experimentos E1, E2, E4, E5 e E7 dão os mesmos números da
execução de 27/10, conferidos numa reexecução parcial sem o BGE-M3.

### Segurança
- **Prompt p-1.1 com os dados do usuário delimitados.** A demanda e os trechos
  recuperados vão entre `<<<DEMANDA … DEMANDA>>>` e `<<<CONTEXTO … CONTEXTO>>>`.
  O prompt de sistema ganhou a regra 6: o conteúdo delimitado é dado, nunca
  instrução. Se a descrição trouxer um delimitador, ele é neutralizado antes da
  montagem do prompt, o que impede o usuário de fechar o bloco e escrever fora
  dele. Isso mitiga a injeção de prompt (`app/llm/prompt.py`). O provedor
  determinístico ignora os delimitadores, por isso os testes e os experimentos
  com ele não mudam.
- **Docker Compose** passa a repassar ao backend as chaves do Gemini e da Groq e
  a configuração do revisor de redação (RF13). Antes, só a Anthropic funcionava
  em contêiner.

### Avaliação
- **50 pedidos fora do domínio** no gabarito (eram 10). Com 10 pedidos, um único
  aceito gera FPR de 10% com IC de 2% a 40%: a estimativa não servia para nada.
  Na reexecução parcial, o spaCy caiu de AUC 0,997 para 0,923, e o limiar 0,64
  passou a aceitar 42% dos pedidos. Os pedidos administrativos que não são
  contratação (improbidade, conselho tutelar, Lei de Acesso à Informação) foram
  os que passaram.
- **IC de Wilson** (`metricas.ic_wilson`) para a TPR e a FPR da validação
  cruzada do portão (E3) e para a revocação global da minimização (E5). As
  tabelas geradas (`tab_e3_portao`, `tab_e5_minimizacao_*`) trazem as faixas.

### Interface (achados da avaliação heurística, seção 5.10 do TCC)
- **Selo de verificação.** O texto agora diz o que a verificação confere (as
  citações batem com os trechos recuperados) e o que não confere (a
  interpretação jurídica). Antes, a frase "100% dos campos têm fundamento
  válido" podia ser lida como garantia de que o documento estava correto.
- **Aviso de privacidade.** Deixou de afirmar que os identificadores "são
  removidos automaticamente". A minimização removeu 82% no conjunto de teste,
  então o aviso pede que o agente não informe dados pessoais.
- **Contador de caracteres** retirado do registro. Dentro do formulário ele só
  atualizava no envio e mostrava "0 caracteres" com o campo preenchido.
- **Linguagem.** "geracao" e "edicao" viraram "gerada pelo modelo" e "editada
  pelo agente". A similaridade passou a usar vírgula decimal. Modelo e versão do
  *prompt* saíram do cabeçalho e foram para a aba Versões.
- **Painel de conformidade** em grade 2 × 2: em quatro colunas, os valores
  apareciam truncados.
- **Tema** com azul institucional (`frontend/.streamlit/config.toml`). O
  vermelho padrão do Streamlit nas barras sugeria erro onde não havia.

### Retirado
- **Backend ChromaDB do índice vetorial** (`IndiceChroma`, `INDICE_BACKEND`,
  `CHROMA_HOST`, `CHROMA_PORTA`). Nunca era exercitado pelos testes (cobertura
  de 65% em `indice.py`) e não acompanhava o índice lexical, que a fusão exige.
  Além disso, só fazia sentido para o orquestrador n8n, abandonado na v0.2. O
  contrato `Indice` continua, então um banco vetorial dedicado pode voltar se o
  corpus crescer. Também saíram as menções restantes ao n8n.

### Testes
- `tests/test_prompt.py`: delimitação, neutralização, regra de sistema,
  provedor determinístico e IC de Wilson, num total de 8 testes.
- A suíte passa a ter 139 testes, todos aprovados (Linux, Python 3.13, com o
  spaCy). A cobertura do pacote `app` subiu de 85,7% para 86,7%.

### Para fazer na sua máquina (com o BGE-M3)
1. `python -m avaliacao.executar`: regenera E1 a E8, inclusive o E3 com os 50
   pedidos e o BGE-M3, e o `resumo.json` com os 139 testes.
2. Se a FPR do BGE-M3 com o limiar 0,52 subir, adote o `limiar_sugerido` do
   `resumo.json` em `RECUPERACAO_LIMIAR` e reporte a validação cruzada.
3. `python -m avaliacao.executar --somente-e9 --llm anthropic --condicoes completo completo_bm25 sem_rag isolada`;
   depois `--llm groq` (repita no dia seguinte, ele retoma de onde parou) e
   `--llm gemini --amostra 6`.
