# Histórico de Evolução do MVP

Este projeto não saiu pronto de uma vez — foi construído em ciclos de "implementa →
critica → corrige", como uma mentoria real funcionaria. Registro isso aqui porque é
parte da avaliação ("dedicação ao projeto/mentorias") e porque ajuda a explicar
*por que* certas decisões técnicas existem.

## Ciclo 1 — MVP inicial
Primeira versão funcional: FastAPI + frontend estático, NLU por palavras-chave,
mocks de BSS/OSS/CRM/Catálogo, Context Manager em memória, handoff básico, dashboard
de KPIs. Rodava de ponta a ponta, mas com lacunas conhecidas.

## Ciclo 2 — Revisão crítica ("visão de gerente")
Pedi uma avaliação honesta como se fosse um gestor da Claro decidindo se aprova o
projeto. Pontos levantados:
- NLU por palavra-chave não generaliza para mensagens compostas/reais.
- Nenhuma camada de LGPD visível (mascaramento, consentimento, rate limit).
- Nenhuma noção de capacidade/carga.
- Handoff sem integração com sistema de atendimento real.
- Métricas de negócio (78% de resolução etc.) eram apenas ilustrativas, não provadas.

**O que mudou:** NLU passou a detectar múltiplas intenções na mesma frase e sinais de
frustração recorrente (escalando para handoff mesmo sem a palavra "cancelar");
consentimento LGPD obrigatório, mascaramento de dado exibido e rate limiting;
endpoint de capacidade honesto (sem inventar SLA); padrão `HandoffConnector`
plugável, pronto para um webhook real (Zendesk/Salesforce). O ponto de métricas de
negócio ficou assumidamente sem solução em código — está documentado como algo que só
um piloto real resolve, não uma correção de software.

## Ciclo 3 — Bug real encontrado em uso (tolerância 0 do bot)
Testando manualmente, percebi que uma simples saudação ("oi") escalava direto para
atendimento humano — o motivo era o limiar de confiança do NLU tratando qualquer
mensagem não reconhecida como handoff imediato.

**O que mudou:** saudações passaram a ter resposta própria; mensagens não entendidas
só escalam depois de 3 tentativas seguidas (tolerância real, não zero).

## Ciclo 4 — Continuidade entre canais não estava visível
Relato: mensagens enviadas pelo WhatsApp não pareciam aparecer no Site (e
vice-versa). Investigação mostrou que o backend já compartilhava a sessão
corretamente — o problema era cache do navegador servindo uma versão antiga do
JavaScript, e falta de indicação visual de que a mensagem veio de outro canal.

**O que mudou:** headers de no-cache nos arquivos estáticos + parâmetro de versão
(`?v=`), e uma etiqueta visível ("↳ recebido via WhatsApp") em toda mensagem que
aparece num canal mas se originou no outro — prova visual da continuidade.

## Ciclo 5 — LLM gratuito (Qwen3 4B via Ollama)
Troquei o motor de NLU/resposta por um LLM local de verdade, mantendo fallback
automático por palavras-chave caso o Ollama não esteja disponível. Problemas
encontrados e resolvidos durante a instalação (documentados porque são reais e podem
se repetir): instalador do Windows falhando por `cmd.exe` bloqueado (contornado
rodando tudo via PowerShell + versão portátil do Ollama); timeout de 10s insuficiente
para o primeiro carregamento do modelo na VRAM (aumentado + warm-up automático no
startup do servidor); modo "thinking" do Qwen3 gerando milhares de tokens de
raciocínio interno e estourando o timeout (desligado via `"think": false` na
chamada à API do Ollama).

**Decisão de design importante:** o LLM nunca recebe autonomia para inventar fatos —
cada agente busca o dado real do mock primeiro (fatura, status de rede, catálogo) e
só pede ao LLM para *reescrever* esse fato de forma mais natural. Isso evita
alucinação de valores, prazos ou promessas.

## Ciclo 6 — Fluxo cliente vs. interno + migração explícita de canal
Reestruturação da interface: separação clara entre "Sou Cliente" (tela de escolha de
canal + chat com um botão explícito "Continuar no WhatsApp/Site") e "Painel Claro
(interno)" (Dashboard + Handoff). A migração de canal virou uma ação visível do
cliente, não apenas trocar de aba — isso evidencia melhor o papel do orquestrador na
demonstração.

## Ciclo 7 — Auditoria formal contra o Documento de Visão (Sprint 1)
Com a lista formal de RF/RNF em mãos, cruzei requisito por requisito contra o que
estava implementado (ver `RASTREABILIDADE.md`). Lacunas reais encontradas e corrigidas:

- **RF005** (Autenticação Silenciosa) não tinha nenhuma simulação — implementado um
  fluxo de token de SSO emitido silenciosamente ao entrar/migrar de canal.
- **RF006** (Dashboard) pedia explicitamente "custo por atendimento", que não estava
  no painel — adicionado como estimativa claramente rotulada como ilustrativa.
- **RNF001** (Tempo de resposta P95 ≤ 2s) não era medido de verdade — passou a
  registrar a latência real das últimas 200 requisições e comparar contra a meta.
- **RNF006** (Observabilidade) pedia trace ID por jornada e alertas automáticos —
  nenhum dos dois existia — implementados ambos (`trace_id` por sessão + evento,
  endpoint `/api/alertas` com limiares).

Também criei `prova_dos_9.py`, uma suíte de 34 checagens automatizadas que roda
contra o servidor real e falha alto (`sys.exit(1)`) se qualquer comportamento
esperado quebrar — usada antes de cada entrega para garantir que nenhuma correção
nova quebrou algo que já funcionava.

## Ciclo 8 — Banco de dados real + Painel Claro pode iniciar contato
Removida a dependência de dicionários Python hardcoded (`CRM_DB`, `BSS_DB`,
`OSS_DB`, `CATALOGO`). Criado `db.py` com SQLite (nativo do Python, zero
dependência nova): tabelas `customers`, `invoices`, `network_status`, `catalog`,
populadas automaticamente na primeira execução com 5 clientes mock (antes eram
só 2). O seletor de cliente no topo da UI agora é preenchido dinamicamente via
`GET /api/admin/clientes`, em vez de `<option>` fixas no HTML.

Também adicionada uma capacidade nova: o **Painel Claro (interno)** ganhou uma
aba "Iniciar Contato" onde o atendente escolhe **qualquer cliente do banco** e um
motivo (fatura vencendo, retenção, oferta de upgrade, ou mensagem livre) para
iniciar a conversa pelo canal desejado — diferente da campanha proativa (que só
cobria o cenário de fatura), isso generaliza para qualquer motivo de contato
outbound decidido por um humano da operação, não só por um evento automático do
BSS.

`prova_dos_9.py` ganhou as seções 18 e 19 cobrindo isso — inclusive um teste que
prova que um cliente **inserido só no banco** (nunca existiu em código Python)
funciona no fluxo completo, o que seria impossível de provar com os dicts antigos.

## Ciclo 9 — Contexto de sessão persistido (não morre mais com o servidor)
Até aqui, o `Context Manager` guardava tudo (histórico de mensagens, status,
intenção atual, contadores de frustração) num dicionário Python em memória — um
`kill -9` no processo, ou simplesmente reiniciar o `uvicorn`, apagava toda
conversa em andamento. Isso era a maior lacuna real de RF002 (Memória Contextual
de Sessão), que pede explicitamente que o histórico seja mantido "durante toda a
jornada".

**O que mudou:** criada a classe `db.Session` em `db.py` — um objeto que se
comporta exatamente como um dicionário Python (`session["status"] = "handoff"`
continua funcionando de forma idêntica em todo `main.py`), mas por baixo grava
direto em duas tabelas SQLite (`sessions` e `messages`). Nenhuma outra linha do
código que já lia/escrevia `session[...]` precisou mudar — só a implementação de
`get_session()` trocou de "cria um dict em memória" para "busca ou cria uma linha
no banco".

**Prova de que funciona de verdade:** matei o processo do servidor no meio de uma
conversa (`kill -9`, sem shutdown gracioso) e subi de novo — o histórico completo,
o status da sessão e o consentimento LGPD continuaram exatamente onde pararam.
Isso virou a seção 20 do `prova_dos_9.py`, que lê o arquivo `clarounify.db` com
uma conexão SQLite totalmente separada da do servidor, comparando os dados
gravados em disco com o que a API retorna — se os números baterem, é prova de
persistência real, não só de memória bem organizada.

## Ciclo 10 — Raciocínio interno do LLM vazando na resposta final
Em uso real (não no fake server de teste), apareceu no chat uma resposta assim:

> "Okay, the user says their internet is going down all the time. Let me check
> the available facts... Let me phrase it concisely.\n\</think\>\nPrezado
> cliente, detectamos instabilidade..."

Ou seja: o `"think": false` que eu tinha configurado no payload do Ollama **não
foi respeitado** por essa combinação de modelo/versão — o raciocínio interno
inteiro (em inglês, narrando como montar a resposta) vazou junto com a resposta
final, incluindo uma tag `</think>` solta no meio do texto.

**O que mudou:** adicionada `strip_thinking()` como segunda camada de defesa,
independente do parâmetro `think` funcionar ou não — remove qualquer bloco
`<think>...</think>` completo e, se sobrar um `</think>` solto (sem abertura
visível, como aconteceu aqui), descarta tudo antes dele e fica só com o texto
final. Aplicada dentro de `call_ollama_chat()`, então protege tanto a
classificação de intenção quanto a reescrita de resposta, sem precisar mexer em
mais nenhum lugar do código.

Reproduzi o bug exato com um Ollama falso devolvendo a mesma string com
`</think>` solto, confirmei que a correção limpa perfeitamente, e só depois
apaguei o servidor de teste — não ficou nenhum artefato de debug no projeto
final.

## Ciclo 11 — "Iniciar Contato" não escala pra base real da Claro
Pedido direto: numa Claro de verdade (milhões de clientes), uma aba que lista
todos os clientes pra escolher um a um nunca funcionaria — ninguém rola uma
lista gigante, e nenhum atendente contata cliente por cliente quando o motivo é
o mesmo pra um grupo inteiro (ex.: todo mundo com fatura atrasada).

**O que mudou:**
- `db.list_customers()` ganhou busca (nome/telefone), filtro por segmento
  (`fatura_status`, `status_rede`) e paginação de verdade (`limit`/`offset` com
  `total` contado à parte) — o endpoint `GET /api/admin/clientes` nunca mais
  devolve a base inteira de uma vez, mesmo que peçam.
- Base de seed expandida de 5 para 20 clientes (com nomes/planos/status
  variados) — o suficiente pra busca, filtro e paginação fazerem sentido de
  verdade na demo, em vez de uma lista de 2-3 itens onde filtro não muda nada.
- Nova funcionalidade: `POST /api/admin/iniciar-contato-lote` — contato **em
  massa** com todos os clientes que baterem com um filtro, não um por um. A
  lógica de montar a mensagem (`montar_mensagem_contato()`) foi extraída pra
  ser reaproveitada tanto no contato individual quanto no em massa, evitando
  duplicar a regra de negócio.
- Frontend: chips de segmento clicáveis ("🔴 Fatura atrasada", "🔴
  Instabilidade de rede"), busca por texto, paginação com "Anterior/Próxima", e
  um bloco separado de contato em massa com confirmação antes de disparar (já
  que afeta N clientes de uma vez, não só um).

`prova_dos_9.py` ganhou as seções 21 e 22, incluindo um teste que confirma que
a paginação nunca ultrapassa o `limit` pedido e que o contato em massa realmente
atinge todo o segmento filtrado — não só uma amostra dele.

---

O padrão em todos os ciclos: uma crítica (minha, do usuário, ou de uma auditoria
formal contra requisitos) → um diagnóstico da causa raiz (não só do sintoma) → uma
correção testada de ponta a ponta antes de considerar resolvido.
