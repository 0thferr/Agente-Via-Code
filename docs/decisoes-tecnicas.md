# Decisões técnicas

Registro curto das decisões que moldam o projeto: o que foi decidido, por quê e qual a
alternativa descartada. Atualize sempre que uma decisão relevante mudar.

## Fase 1 — Fundação

**SQLAlchemy síncrono (não async).** O FastAPI executa rotas síncronas em um pool de
threads, o que atende com folga um agente pessoal. O modo assíncrono exigiria cuidados
extras (sessões, lazy loading, drivers) sem ganho real neste volume. Pode ser revisto se
houver muitas chamadas externas concorrentes.

**Camadas rota → serviço → modelo.** Rotas só validam entrada e montam a resposta;
serviços concentram as regras e lançam erros de domínio (`app/core/errors.py`), que são
traduzidos para HTTP em um único lugar. Não há camada de "repositório": as consultas do
SQLAlchemy já são uma abstração suficiente e uma camada a mais seria só repasse.

**Sem cadastro público de usuários.** É um agente pessoal; contas são criadas por CLI
(`python -m app.cli create-user`). Isso elimina uma superfície de ataque inteira.

**JWT curto + refresh token rotativo guardado no banco.** O access token (15 min) não
consulta o banco para validar assinatura; o refresh token tem o `jti` registrado, o que
permite revogação e detecção de reutilização (indício de vazamento).

**Rate limiting no Redis com falha fechada.** Funciona com várias instâncias da API. Se
o Redis cair, o login é recusado: preferimos indisponibilidade a ficar sem proteção
contra força bruta.

**Enums como texto (`native_enum=False`).** Adicionar um valor exige só uma migration
simples, sem `ALTER TYPE` do PostgreSQL.

**PUT /profile substitui o perfil inteiro.** Semântica simples e idempotente. As listas
aninhadas são apagadas e recriadas na mesma transação.

**Importação de currículo em duas etapas (rascunho → confirmação).** Nenhum dado
extraído vai para o perfil sem revisão humana. Esta é a base da regra "a IA só usa dados
validados".

**Testes contra PostgreSQL real, com rollback por teste.** O modelo usa recursos do
PostgreSQL (ARRAY, JSONB, `ON CONFLICT`); SQLite daria falsa segurança. As migrations
são aplicadas do zero no início da suíte, então elas também são testadas.

## Fase 2 — Descoberta

**Interface `JobProvider` + catálogo (`ProviderRegistry`).** Cada conector declara
capacidades e tipo de integração (`automated`, `simulated`, `manual_only`). O serviço de
descoberta depende só da interface.

**Plataformas sem integração autorizada aparecem como `manual_only`.** LinkedIn, Gupy,
Indeed, Catho e Vagas.com.br não oferecem API de busca para candidatos (ou restringem a
parceiros). Em vez de scraping, o usuário importa a vaga pelo link (`POST /jobs/import`).
O catálogo deixa isso explícito para o usuário e para o dashboard.

**Deduplicação garantida pelo banco.** Três constraints únicas por usuário:
`(provider_key, external_id)`, `url_hash` (URL canônica, sem rastreamento) e
`content_fingerprint` (empresa + cargo + local normalizados). A inserção usa
`INSERT ... ON CONFLICT DO NOTHING`, que continua correta com buscas simultâneas — ao
contrário de "consultar e depois inserir", que tem condição de corrida.

**Busca síncrona por enquanto.** `POST /jobs/search` roda na requisição, com cada fonte
confirmada separadamente (a falha de uma não desfaz as outras). Na Fase 5 a mesma função
de serviço será chamada pelo agendador em processo separado.

**Conteúdo externo é dado, nunca instrução.** Descrições são convertidas em texto puro
(sem HTML/caracteres de controle) e limitadas em tamanho. A proteção contra prompt
injection propriamente dita fica na camada de IA (Fase 3).

**Modalidade de trabalho só é deduzida quando explícita.** Se o texto menciona mais de
uma modalidade, ou nenhuma, o campo fica vazio — não presumimos.

## Fase 3 — Inteligência

**A IA não dá nota nem decide.** Ela devolve, requisito a requisito, um status e as
referências do perfil que o comprovam. O código valida as referências, calcula a nota e
decide. Isso torna o resultado auditável e reduz o impacto de prompt injection: não há
número que o texto da vaga consiga "forçar".

**Critérios eliminatórios fora da IA.** Salário, modalidade e localização são regras
simples e objetivas; implementá-las em código evita custo, variação e erro do modelo. Se
um eliminatório falha, a IA nem é chamada.

**Dado desconhecido não elimina.** Vaga sem salário ou sem modalidade não é descartada:
o critério fica `unknown`, sai da média e reduz a confiança.

**Evidências por referência curta ("skill:1", "exp:2").** São fáceis de o modelo citar
e de o código conferir. Referência inexistente é descartada; requisito "atendido" sem
evidência vira `unclear`.

**Provedor simulado como padrão.** O projeto roda e é testado sem chave nem custo. A
troca para OpenAI é por configuração, sem mudar código (interface `StructuredLLM`).

**HTTPX em vez do SDK da OpenAI.** Só uma chamada é necessária e o HTTPX já é dependência;
o transporte simulado do HTTPX também facilita os testes.

**Minimização de dados (LGPD).** Só fatos profissionais vão para a IA; dados de contato
ficam de fora. Prompts e respostas não são registrados em log.

**Análises e textos versionados.** Cada análise é um registro novo (com pesos, nota
mínima e versão do perfil usados), e cada texto gerado ou editado é uma nova versão. Isso
mantém o histórico dos motivos de aprovação e descarte.

## Fase 4 — Candidaturas

**Máquina de estados em tabela explícita.** Todas as mudanças de status passam por
`transition`, que valida contra `ALLOWED_TRANSITIONS` e grava o evento na linha do tempo.
É impossível, por exemplo, ir de `AWAITING_APPROVAL` direto para `APPLYING`.

**Uma candidatura por vaga, garantida pelo banco.** `UNIQUE (user_id, job_id)` e criação
com `ON CONFLICT DO NOTHING`, no mesmo padrão da deduplicação de vagas.

**Concorrência em duas camadas.** `SELECT ... FOR UPDATE` serializa ações sobre a mesma
candidatura (ex.: dois cliques em "enviar"), e uma coluna de versão (`row_version`) faz
uma atualização concorrente falhar em vez de sobrescrever a outra em silêncio.

**Idempotência do envio.** A chave é gerada uma vez e reutilizada em todas as tentativas;
`APPLYING` é gravado antes da chamada externa. Assim, falha ou reinício no meio do envio
fica visível, e uma nova tentativa não cria candidatura duplicada na plataforma.
"Resultado incerto" nunca é tratado como sucesso.

**Aprovação vinculada à versão.** A aprovação guarda o texto e o currículo revisados; o
envio só vale para essa combinação. Editar o texto depois exige nova aprovação.

**Modo automático por lista explícita.** Além do modo, cada plataforma precisa estar em
`auto_submit_providers`. As regras ficam em um só lugar (`policy.py`), usado tanto na
preparação quanto no envio.

**Sequência para ordenar eventos.** No PostgreSQL, `now()` é fixo durante a transação, e a
preparação gera vários eventos de uma vez. Ordenar por `created_at` seria ambíguo, por
isso os eventos têm uma coluna `sequence` (identity).

## Fase 5 — Automação

**Coordenação pelo banco, não pelo agendador.** O APScheduler só dispara o ciclo do worker
a cada N segundos. Quem garante que uma execução lógica rode uma vez só, mesmo com várias
instâncias, é o PostgreSQL: `UNIQUE (rule_id, scheduled_for)` ao enfileirar e
`FOR UPDATE SKIP LOCKED` ao reservar. O APScheduler sozinho não coordena processos
diferentes.

**Lease em vez de "matar" tarefas.** Python não interrompe uma thread com segurança. Cada
reserva tem prazo; se o worker morrer, outro retoma a execução depois do prazo. Isso só é
seguro porque todas as tarefas são idempotentes (deduplicação de vagas, uma candidatura por
vaga, chave de idempotência no envio).

**Erro de domínio x erro transitório.** Falhas de integração (IA, plataforma) são repetidas
com espera exponencial; erros de configuração não, porque repetir não resolve.

**Sem rajada de execuções atrasadas.** A próxima execução é calculada a partir de "agora".
Se o worker ficar parado por um dia, ele roda uma vez ao voltar, não 24 vezes.

**`app/wiring.py`.** As fábricas de componentes (IA, provedores, enviadores) saíram de
`app/api/deps.py` para que o worker use a mesma configuração sem depender da camada HTTP.

## Fase 6 — Comunicação

**Somente leitura e só metadados.** Os escopos OAuth não permitem enviar e-mails, então
"nunca responder recrutadores sem aprovação" é garantido pela permissão, não só pelo
código. Guardamos assunto, remetente, trecho curto e datas; o corpo não é baixado
(minimização, LGPD).

**OAuth com `state` + PKCE.** O `state` (guardado só como hash) protege contra CSRF e vale
uma vez; o PKCE impede que um código interceptado seja trocado por tokens.

**Tokens cifrados com Fernet.** Um vazamento do banco não expõe tokens de e-mail. A chave
fica fora do banco (`TOKEN_ENCRYPTION_KEY`).

**Classificação por regras, sem IA.** É previsível, explicável e não envia o conteúdo do
e-mail a terceiros. A IA pode ser adicionada depois com as mesmas defesas da Fase 3.

**Associação conservadora.** Só associa sozinho com um candidato claro; na dúvida, pede
validação. Um e-mail associado errado mudaria a etapa da candidatura errada.

**Notificar ≠ entregar.** `notify` só grava no banco, na mesma transação do fato que gerou
a notificação; o worker faz as entregas externas com retentativas. Assim, uma API externa
lenta não atrasa requisições, e não há notificação de um fato que foi desfeito (rollback).

## Fase 7 — Indicadores

**Métricas calculadas na hora, a partir dos registros.** Nada de contadores guardados que
podem divergir dos dados. Para o volume de um agente pessoal, consultas agregadas com os
índices certos são rápidas; se crescer, dá para materializar depois.

**Etapas alcançadas pela linha do tempo.** "Entrevistas" conta candidaturas que *passaram*
por `INTERVIEW` no período (eventos), não só as que estão lá agora. Assim, uma
candidatura que já virou proposta continua contando como entrevista.

**Última análise por vaga com `row_number()`.** Uma window function, em vez de
`DISTINCT ON`, porque a forma de escrever `DISTINCT ON` mudou entre o SQLAlchemy 2.0 e o
2.1; a window function funciona nas duas.

**Índices para os filtros do dashboard.** `(user_id, created_at)` em vagas,
`(user_id, submitted_at)` em candidaturas e `(to_status, created_at)` nos eventos.

**Intervalo máximo de 366 dias.** Evita consultas que varrem o histórico inteiro sem
necessidade.

## Modo assistido (LinkedIn e Gupy)

**A API prepara, o usuário envia.** Foi pedido que a API preenchesse os formulários do
LinkedIn e da Gupy com limite de 10 por dia. Não implementamos automação de navegador:
viola os termos de uso (o LinkedIn proíbe bots e extensões que automatizam o serviço),
arrisca banir a conta do usuário, exige guardar a sessão dele e quebra a cada mudança de
layout. O modo assistido entrega quase o mesmo ganho (todos os dados prontos) sem esses
riscos.

**Plataforma da vaga importada.** Antes toda importação virava `manual`; agora a
plataforma é detectada pela URL ou informada. Sem isso não haveria como limitar por
plataforma.

**Cota = enviadas hoje + kits pendentes.** Contar só os envios deixaria a fila crescer sem
limite (o worker prepararia dezenas de kits por dia). Somar os pendentes mantém a fila do
tamanho do que dá para enviar. A verificação acontece antes de qualquer mudança de estado,
então uma preparação recusada não deixa rastros.

**Gravar um envio nunca é bloqueado.** `/mark-applied` registra o que já aconteceu na
plataforma; recusar perderia informação. O limite atua na preparação dos kits.

## LGPD — exportação e exclusão

**Exportação genérica com lista de exclusão.** Cada tabela do usuário é serializada pelas
colunas do modelo, exceto uma lista fixa de campos sensíveis (hash de senha, tokens
cifrados, segredos, chaves de armazenamento). Uma coluna nova aparece na exportação
automaticamente; um campo sensível novo precisa entrar na lista.

**Exclusão em cascata pelo banco.** Todas as chaves estrangeiras para `users` usam
`ON DELETE CASCADE` (ou `SET NULL`), então apagar o usuário remove tudo de forma
consistente numa única transação. Os arquivos em disco são apagados depois do commit:
se o banco falhar, nada é perdido; se um arquivo falhar, o erro é registrado.

**Reautenticação para excluir.** Um token de acesso roubado não basta: a exclusão exige a
senha atual, a frase de confirmação e tem limite de tentativas.

**Auditoria anonimizada, não apagada.** Os registros de segurança continuam úteis (ex.:
investigar abuso), mas perdem o vínculo com a pessoa e não contêm dados de contato.

**POST em vez de DELETE com corpo.** Alguns clientes e proxies descartam o corpo de
requisições DELETE, e a senha precisa ir no corpo (nunca na URL).

## Correções da revisão de código (PR #1)

- **Sincronização de e-mail paginada.** Antes, só as 50 mensagens mais recentes eram
  lidas e o cursor avançava, perdendo as demais para sempre. Agora as páginas são
  percorridas, as mensagens já gravadas são ignoradas (sem baixar de novo) e o cursor só
  avança quando o período foi lido por completo.
- **E-mails fora de recrutamento guardam só a referência** (id e data). A referência é
  necessária para a deduplicação e a paginação; o conteúdo, não.
- **Prazos só em e-mails de recrutamento e por palavra inteira** ("ate" não casa mais
  com "candidate").
- **Versão do perfil sempre atualizada ao salvar**, mesmo quando só as listas mudam;
  senão análises antigas eram reaproveitadas.
- **Vagas com análise falha voltam para a fila do worker**, até 3 tentativas.
- **Rate limiter compatível com qualquer Redis:** `SET ... EX ... NX` + `INCR` no lugar
  de `EXPIRE ... NX` (que exige Redis 7).
- **Limite diário sem condição de corrida:** as verificações do envio rodam sob uma trava
  por usuário (`pg_advisory_xact_lock`), e envios em `APPLYING` contam no limite. A trava
  sozinha não bastaria: ela é liberada no commit que grava `APPLYING`, antes de existir
  `submitted_at`.
- **Analisador simulado compara termos inteiros** ("Go" não é mais confundido com "django").
- **Teste fraco substituído** por dois testes que verificam algo de fato: tarefas de e-mail
  podem ser agendadas, e tarefa sem handler é recusada.

## Segunda passada de revisão

- **Regra desativada não executa.** Execuções já enfileiradas (ou em retentativa) de uma
  regra desativada são encerradas sem rodar, e não é possível disparar manualmente uma
  regra desativada. Desligar o envio automático precisa valer na hora.
- **Tentativa interrompida fica no histórico.** Ao retomar um job cuja reserva venceu, o
  worker registra a tentativa perdida como falha.
- **Notificações com espera exponencial.** Até 5 tentativas, com 1, 2, 4 e 8 minutos entre
  elas (coluna `next_attempt_at`), em vez de 3 tentativas em ~90 segundos.
- **Erros inesperados registrados com `request_id`** no log estruturado.
- **Associação de e-mail por palavra inteira** também nas palavras do cargo.
- **Resumo do perfil nunca cortado no meio de uma palavra** no texto simulado.

## Fuso horário do usuário

**Fuso na conta, não em cada configuração.** Um único campo `users.timezone` (padrão
`America/Sao_Paulo`) vale para limites diários, cota do modo assistido, datas de e-mails,
resumo diário e dashboard. As rotinas de automação mantêm o fuso próprio, porque uma
regra pode precisar de outro horário.

**Banco em UTC, conversão nas bordas.** As datas continuam gravadas em UTC
(`timestamptz`); o fuso só entra para calcular a "meia-noite local"
(`local_start_of_day`), interpretar datas digitadas em e-mails e agrupar o dashboard
(`date_trunc` com fuso).

**Classificador recebe o fuso por parâmetro.** É uma função pura; quem chama passa o fuso
do usuário. Isso mantém a função testável sem banco.

## Estruturação do currículo

**Mesmo padrão da análise de vagas: a IA sugere, o código confere, o usuário decide.**
Cada item extraído precisa aparecer literalmente no texto do PDF (comparação por termos
inteiros, sem acento e sem diferença de maiúsculas). Isso impede, por construção, que a
IA "complete" o currículo com experiências ou certificações inexistentes.

**Leitor simulado conservador.** Ele reconhece seções simples (competências, idiomas) e não
tenta interpretar experiências e formação, cujos formatos variam demais; errar ali geraria
dados falsos no rascunho.

**Contatos fora da IA.** E-mail, telefone e links são extraídos por regras locais e
removidos do texto antes de ir para o provedor de IA.

**Upload fora do event loop.** A rota é `async`, mas o serviço é síncrono e pode esperar a
IA por dezenas de segundos; ele roda em `run_in_threadpool` para não travar as outras
requisições.
