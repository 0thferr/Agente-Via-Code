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
