# Situação 3: sistema ao vivo (23/11/2026 07:06)

- Provedor de geração: groq · revisor: gemini
- Modelo de embeddings: (do .env) · versão do sistema: 0.4.4
- Modo: modelo real

| ID | Verificação | Esperado | Obtido | Situação | Captura |
|---|---|---|---|---|---|
| V01 | Tela de acesso | formulário de acesso visível | visível | PASSOU | t01_acesso.png |
| V02 | Login do demandante | painel do agente | painel exibido | PASSOU | — |
| V03 | Contador de caracteres removido (Quadro 22) | sem contador | sem contador | PASSOU | t03_registro.png |
| V04 | Aviso de privacidade honesto (Quadro 22) | aviso sobre remoção incompleta | presente | PASSOU | t03_registro.png |
| V05 | Geração da minuta de D12 com modelo real | tela de revisão | minuta gerada em 26 s | PASSOU | — |
| V06 | Selo declara os limites da verificação (Quadro 22) | selo com o limite, ou aviso de inconsistência | selo com limite | PASSOU | t04_revisao.png |
| V07 | Grade 2 x 2 de indicadores (Quadro 22) | quatro indicadores visíveis | visíveis | PASSOU | t05_conformidade.png |
| V08 | Modelo e versão do prompt registrados na versão | modelo gpt-oss-120b; prompt p-1.1 | modelo openai/gpt-oss-120b; prompt p-1.1 | PASSOU | t04c_versoes.png |
| V09 | Revisão de redação por segundo modelo (RF13, UC05b) | sugestão comparada lado a lado pelo revisor gemini | sugestão exibida; revisor gemini · gemini-3 | PASSOU | t04b_revisao_texto.png |
| V10 | Aceite ou descarte da sugestão | decisão do agente | nenhuma mudança sugerida; descartada | PASSOU | — |
| V11 | Aprovação com 7 de 8 campos confirmados (RF07) | botão Aprovar desabilitado | desabilitado | PASSOU | t06_validacao.png |
| V12 | Aprovação com os 8 campos e a declaração | versão aprovada | aprovada | PASSOU | — |
| V13 | Exportar após editar minuta aprovada (CT07, RN01) | botão desabilitado e API com código 409 | botão desabilitado; API 409 | PASSOU | t06b_edicao_pos_aprovacao.png |
| V14 | Exportação .docx: integridade e hash | arquivo abre e SHA-256 igual ao exibido | 53 parágrafos; SHA-256 igual (cd5b1dcd93455ebe…) | PASSOU | t07_exportacao.png |
| V15-F49 | Pedido fora do domínio F49 (fluxo A1) | geração barrada, demanda preservada, nenhuma chamada ao modelo | não barrada | FALHOU | t09_fluxo_a1_f49.png |
| V15-F09 | Pedido fora do domínio F09 (fluxo A1) | geração barrada, demanda preservada, nenhuma chamada ao modelo | não barrada | FALHOU | t09_fluxo_a1_f09.png |
| V16 | Minimização de CPF, e-mail, telefone e nome antes do banco | aviso na tela e nenhum identificador no banco | aviso exibido; nenhum identificador gravado | PASSOU | t10_dados_pessoais.png |
| V17 | Provedor indisponível (fluxo A2) | mensagem clara e demanda preservada | mensagem exibida; demanda preservada | PASSOU | t12_fluxo_a2.png |
| V18 | Painel do demandante com situações | documentos listados | 5 documentos | PASSOU | t02_painel.png |
| V19 | Painel de vigência do gestor | 3 de 3 normas; 1.734 trechos no banco e no índice | normas 3 de 3; trechos 1734 no banco e 1734 no índice | PASSOU | t08_base_normativa.png |
| V20 | Consulta à base normativa | trechos com similaridade e limiar | trechos exibidos | PASSOU | t08b_consulta_base.png |
| V21 | Integridade da cadeia de auditoria | cadeia íntegra | íntegra, 17 registros | PASSOU | t11_auditoria.png |
| V22 | Painel de indicadores do gestor | indicadores exibidos | exibido | PASSOU | t13_indicadores.png |

Resultado: 21 de 23 verificações aprovadas.
