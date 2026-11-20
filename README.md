# JobPilot AI

API backend de um agente pessoal de candidaturas a vagas de emprego: guarda o perfil
profissional real do candidato e (nas próximas fases) descobre, analisa e acompanha vagas.

> **Estado atual:** Fase 1 (fundação) concluída. Veja [Roadmap](#roadmap) e
> [Limitações conhecidas](#limitações-conhecidas).

## Stack

Python 3.12+ · FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · PostgreSQL · Redis · Pytest · Docker

## Estrutura

```
app/
  api/            Rotas HTTP (/api/v1) e dependências (auth, sessão, rate limit)
  core/           Configuração, segurança (hash/JWT), logging, erros de domínio
  db/             Base declarativa, sessão e tipos comuns
  models/         Modelos SQLAlchemy (tabelas)
  schemas/        Contratos Pydantic de entrada/saída
  services/       Regras de negócio (não conhecem HTTP)
  cli/            Comandos administrativos (criar usuário)
alembic/          Migrations do banco
tests/            unit/ (sem banco) e integration/ (API + PostgreSQL)
docs/             Decisões técnicas
```

Fluxo de uma requisição: **rota** (valida entrada com schema) → **serviço** (regra de
negócio, lança erros de domínio) → **modelo/banco**. Os erros de domínio são convertidos
em respostas HTTP em um único lugar (`app/core/errors.py`).

## Como executar

### Opção A — tudo com Docker

```bash
cp .env.example .env
# edite .env e defina JWT_SECRET_KEY:
python -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up --build -d
docker compose exec api python -m app.cli create-user --email voce@exemplo.com
```

A API fica em http://localhost:8000 e a documentação interativa (OpenAPI) em
http://localhost:8000/docs.

### Opção B — API local, banco e Redis no Docker

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # defina JWT_SECRET_KEY
docker compose up -d db redis
alembic upgrade head
python -m app.cli create-user --email voce@exemplo.com
uvicorn app.main:app --reload
```

> Não existe cadastro público de usuários: o sistema é um agente pessoal, então contas
> são criadas pelo comando administrativo acima (a senha é pedida de forma interativa
> e precisa ter pelo menos 12 caracteres).

## Testes e lint

Os testes usam um PostgreSQL real (o banco `jobpilot_test`, criado automaticamente pelo
`docker compose`). Cada teste roda em uma transação desfeita ao final.

```bash
docker compose up -d db
pytest                                   # toda a suíte
pytest tests/integration/test_auth.py    # um arquivo
pytest -k refresh                        # testes pelo nome
ruff check . && ruff format --check .    # lint e formatação
```

Para usar outro banco de testes, defina `TEST_DATABASE_URL`. **O banco de testes é
recriado do zero a cada execução — nunca aponte para um banco com dados reais.**

## Exemplos de requisições

```bash
# Login
curl -s -X POST localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"voce@exemplo.com","password":"sua-senha-forte"}'
# => {"access_token": "...", "refresh_token": "...", "token_type": "bearer", "expires_in": 900}

TOKEN=...  # access_token

# Criar/substituir o perfil completo
curl -s -X PUT localhost:8000/api/v1/profile \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "full_name": "Ana Souza",
    "desired_titles": ["Desenvolvedora Backend"],
    "desired_work_models": ["remote"],
    "salary_expectation_min": "5000.00", "salary_currency": "BRL",
    "skills": [{"name": "Python", "category": "technology", "level": "intermediate"}],
    "languages": [{"name": "Inglês", "proficiency": "intermediate"}]
  }'

# Enviar currículo em PDF (gera um rascunho para revisão)
curl -s -X POST localhost:8000/api/v1/profile/resumes \
  -H "Authorization: Bearer $TOKEN" -F "file=@curriculo.pdf;type=application/pdf"

# Confirmar o rascunho revisado (só então os dados vão para o perfil)
curl -s -X POST localhost:8000/api/v1/profile/resumes/<id>/confirm \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"profile": { ...perfil revisado... }}'
```

## Endpoints disponíveis

| Método | Rota | Descrição |
| --- | --- | --- |
| GET | `/api/v1/health` | Liveness (processo de pé) |
| GET | `/api/v1/ready` | Readiness (banco e Redis respondem) |
| POST | `/api/v1/auth/login` | Login; devolve access + refresh token |
| POST | `/api/v1/auth/refresh` | Rotação do refresh token |
| GET | `/api/v1/auth/me` | Usuário autenticado |
| GET / PUT | `/api/v1/profile` | Consulta / cria-ou-substitui o perfil |
| GET | `/api/v1/profile/skills` | Competências do perfil |
| POST / GET | `/api/v1/profile/resumes` | Envia / lista currículos |
| GET | `/api/v1/profile/resumes/{id}` | Detalhe e rascunho extraído |
| GET | `/api/v1/profile/resumes/{id}/file` | Download do PDF (só o dono) |
| POST | `/api/v1/profile/resumes/{id}/confirm` | Confirma o rascunho revisado no perfil |

Erros seguem sempre o formato `{"error": {"code": "...", "message": "..."}}` (erros de
validação de entrada usam o formato padrão do FastAPI, com status 422).

## Segurança (o que já está implementado)

- Senhas com **Argon2**; mensagem de erro e tempo de resposta iguais para e-mail
  inexistente e senha errada (evita enumeração de usuários).
- **JWT** de acesso curto (15 min) + refresh token com **rotação**; reutilizar um refresh
  token já usado revoga todos os tokens do usuário (detecção de vazamento).
- **Rate limiting** no login (por IP e por e-mail) e no refresh, via Redis.
- **Autorização por dono**: toda consulta filtra por `user_id`; recurso de outro usuário
  responde 404, sem revelar que existe.
- Upload: valida tipo, *magic bytes* `%PDF-`, tamanho máximo; arquivos salvos com nome
  aleatório fora do alcance de path traversal.
- Logs estruturados em JSON que nunca incluem corpo, query string ou cabeçalhos, e
  mascaram chaves sensíveis (`password`, `token`, `authorization`...).
- **Auditoria** (`audit_logs`) de login, falhas de login, reutilização de token, perfil e
  currículos — gravada na mesma transação da ação.
- Contêiner executa como usuário sem privilégios.

## Roadmap

| Fase | Escopo | Estado |
| --- | --- | --- |
| 1 | Fundação: API, Docker, PostgreSQL, migrations, auth, perfil, testes | ✅ Concluída |
| 2 | Descoberta: conectores, provedor simulado, importação manual, deduplicação | ⏳ |
| 3 | Inteligência: IA, compatibilidade, regras eliminatórias, apresentações | ⏳ |
| 4 | Candidaturas: máquina de estados, aprovação, envio autorizado | ⏳ |
| 5 | Automação: agendador em processo separado, retentativas, resumo diário | ⏳ |
| 6 | Comunicação: OAuth de e-mail, monitoramento, notificações | ⏳ |
| 7 | Indicadores: dashboard e métricas | ⏳ |

## Limitações conhecidas

- **Importação de currículo**: a extração atual é determinística — preenche apenas
  e-mail, telefone, LinkedIn e GitHub encontrados literalmente no texto. Experiências,
  formação e competências precisam ser completadas pelo usuário na revisão. A
  estruturação com IA está prevista para a Fase 3, sempre com revisão humana.
- PDFs digitalizados (imagem) não têm texto extraível; o arquivo é guardado, mas o
  rascunho vem vazio (não há OCR).
- Arquivos ficam em disco local (`STORAGE_DIR`). Em produção com várias instâncias, use
  um armazenamento compartilhado (ex.: S3) implementando a mesma interface de
  `LocalFileStorage`.
- Se o Redis estiver indisponível, o login é recusado (falha fechada), por segurança.

## Recomendações de produção

- Defina `APP_ENV=production`, um `JWT_SECRET_KEY` forte e exclusivo, e mantenha o
  `.env` fora do controle de versão (use um gerenciador de segredos).
- Coloque a API atrás de um proxy reverso com HTTPS; o `--proxy-headers` do uvicorn já
  está habilitado para o rate limiting enxergar o IP real.
- Use credenciais de banco próprias (não as do `docker-compose.yml`) e backups.
- Restrinja `CORS_ORIGINS` aos domínios do dashboard.
