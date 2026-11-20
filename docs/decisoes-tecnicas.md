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
