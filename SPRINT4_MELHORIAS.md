# Challenge Sprint 4 — ClaroUnifyHub v7

## Contexto
As melhorias abaixo foram propostas pela equipe durante a evolução do MVP; **não há registro de feedback específico dos professores ou da Claro**. Não atribuir esses pontos à banca sem evidência.

| Melhoria | Antes (v6.3) | Depois (v7) | Justificativa | Demonstração |
|---|---|---|---|---|
| 1. Chat contextual | Frases curtas sem palavra-chave frequentemente geravam resposta de não entendimento | Respostas curtas predefinidas como “pode explicar” e “quero saber mais” retomam intenção anterior válida | Diminuir repetição sem inferir decisões irreversíveis | Pergunte sobre internet; depois “pode explicar” |
| 2. Dashboard comparativo | Gráfico por período e totais, sem base comparativa imediata | Indicadores de volume atual vs. janela anterior, variação, sessões iniciadas, resoluções e duração média da sessão | Melhor leitura de tendência; comparação calculada sobre dados SQLite | Selecione 7/14/30 dias e mostre cartões |
| 3. Atendimento com resumo | Histórico e intenção estavam na fila, sem síntese estruturada | Resumo extrativo das últimas 3 mensagens do cliente, motivo e prioridade alta/normal | Diminuir leitura inicial e destacar recorrência/risco | Simule múltiplas reclamações e abra a fila |
| 4. Responsividade e acessibilidade | Alguns elementos compactos e pontos de foco pouco consistentes | Alvos de toque de 44 px no mobile, layout fluido, foco visível, movimentos reduzidos e ajustes no HTML | Melhor uso em celular e com teclado | Reduza janela, navegue com Tab |
| 5. Consentimento LGPD | Botões pequenos, texto menos claro e interrupção da primeira solicitação | Componente acessível com texto direto, botões com SVG e retomada da mensagem após aceite (v6.3) | Clareza, transparência e continuidade | Compare com a primeira versão e aceite consentimento |

**Limitações honestas:** interpretação contextual é baseada em um conjunto seguro de respostas breves, não em compreensão semântica universal. Resumo de handoff é extrativo e não usa um LLM. A duração de sessão não é tempo de resposta do operador. Base e canais são demonstrativos.

## Evidências para o documento
Capture imagens reais de cada melhoria nas versões de referência (v6.3 para as melhorias 1–4 e versão anterior à v6.3 para LGPD) e da v7. Adicione legenda *Antes*, *Depois*, justificativa e resultado para cada par. **As imagens não estão anexadas a este arquivo porque precisam ser obtidas com a aplicação rodando.**

## Roteiro de vídeo (~4min30s)
- 0:00–0:25 — apresentação da equipe e objetivo.
- 0:25–1:15 — chat contextual, antes/depois.
- 1:15–2:05 — dashboard, antes/depois.
- 2:05–3:00 — handoff com resumo/prioridade, antes/depois.
- 3:00–3:35 — consentimento LGPD, antes/depois.
- 3:35–4:25 — responsividade, teclado e conclusão.

Todos os integrantes devem aparecer com câmera aberta. O link público do vídeo deve ser inserido no documento final após o upload.


## v8 — central de diagnóstico de rede
O backend disponibiliza POST /api/network/diagnostic (após consentimento), devolve status, região e previsão da base OSS fictícia, registra no histórico como mensagem estruturada e inclui status do diagnóstico no resumo do handoff. O frontend converte o registro persistido em card visual com indicação explícita de dados simulados e botão para atendimento humano.


## v9 — melhoria 4: Simulador Inteligente de Planos
- Comparação interativa com os preços e características do catálogo fictício persistido no SQLite.
- Recomendações determinísticas por tipo de uso (leve, moderado, intenso, residencial) e orçamento.
- Tela no chat com seleção de perfil, cards e preços; escolha de plano encaminha interesse ao atendente, sem contratação real.
- APIs `POST /api/plans/simulate` e `POST /api/plans/select`; consentimento obrigatório; histórico persistente e verificações de perfil e catálogo.
- As melhorias de chat contextual continuam no código, mas deixam de figurar como uma das cinco evidências centrais.
