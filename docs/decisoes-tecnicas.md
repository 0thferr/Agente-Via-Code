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
