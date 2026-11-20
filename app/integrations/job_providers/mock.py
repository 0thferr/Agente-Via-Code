"""Provedor simulado: permite validar descoberta e deduplicação sem integrações externas."""

from app.core.text_normalization import normalize_text
from app.integrations.job_providers.base import (
    IntegrationStatus,
    JobProvider,
    JobSearchQuery,
    ProviderCapability,
    ProviderInfo,
    RawJob,
)
from app.integrations.job_providers.mock_data import MOCK_JOBS

MOCK_PROVIDER_KEY = "mock"


class MockJobProvider(JobProvider):
    info = ProviderInfo(
        key=MOCK_PROVIDER_KEY,
        name="Provedor simulado",
        integration_status=IntegrationStatus.SIMULATED,
        capabilities=frozenset(
            {
                ProviderCapability.SEARCH,
                ProviderCapability.JOB_DETAILS,
                ProviderCapability.COMPANY_IDENTIFICATION,
                ProviderCapability.ORIGINAL_URL,
                ProviderCapability.REQUIREMENTS,
                ProviderCapability.WORK_MODEL,
            }
        ),
        notes="Vagas fictícias para desenvolvimento e testes. Não representa vagas reais.",
    )

    def __init__(self, jobs: list[RawJob] | None = None) -> None:
        self._jobs = MOCK_JOBS if jobs is None else jobs

    def search(self, query: JobSearchQuery) -> list[RawJob]:
        return [job for job in self._jobs if _matches(job, query)]

    def get_details(self, external_id: str) -> RawJob | None:
        return next((job for job in self._jobs if job.external_id == external_id), None)


def _matches(job: RawJob, query: JobSearchQuery) -> bool:
    searchable = normalize_text(
        " ".join([job.title, job.company, job.description, *job.technologies])
    )
    checks = [
        query.title is None or normalize_text(query.title) in normalize_text(job.title),
        all(normalize_text(word) in searchable for word in query.keywords),
        all(normalize_text(tech) in searchable for tech in query.technologies),
        query.seniority is None or job.seniority == query.seniority,
        query.work_model is None or job.work_model == query.work_model,
        query.company is None or normalize_text(query.company) in normalize_text(job.company),
        query.location is None
        or normalize_text(query.location) in normalize_text(job.location or ""),
        # Sem salário informado a vaga não é descartada aqui: salário desconhecido não é
        # o mesmo que salário baixo (a análise de compatibilidade trata esse caso).
        query.min_salary is None or job.salary_max is None or job.salary_max >= query.min_salary,
        query.posted_after is None or job.posted_at is None or job.posted_at >= query.posted_after,
    ]
    return all(checks)
