"""Contrato comum dos conectores de vagas (JobProvider).

Cada conector declara o que sabe fazer (capacidades) e o tipo de integração
que oferece. O restante do sistema depende apenas desta interface, então
adicionar uma fonte nova não exige mudar o serviço de descoberta.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.models.enums import Seniority, WorkModel


class ProviderCapability(StrEnum):
    SEARCH = "search"
    JOB_DETAILS = "job_details"
    COMPANY_IDENTIFICATION = "company_identification"
    ORIGINAL_URL = "original_url"
    REQUIREMENTS = "requirements"
    WORK_MODEL = "work_model"
    AUTHORIZED_APPLY = "authorized_apply"


class IntegrationStatus(StrEnum):
    AUTOMATED = "automated"  # integração real e autorizada (API oficial ou feed público)
    SIMULATED = "simulated"  # dados fictícios para desenvolvimento e demonstração
    MANUAL_ONLY = "manual_only"  # sem integração: o usuário cadastra os links manualmente


class ProviderError(Exception):
    """Falha ao consultar uma fonte externa (rede, autenticação, limite de uso...)."""


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    name: str
    integration_status: IntegrationStatus
    capabilities: frozenset[ProviderCapability]
    notes: str = ""

    def supports(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True)
class JobSearchQuery:
    title: str | None = None
    keywords: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    seniority: Seniority | None = None
    location: str | None = None
    work_model: WorkModel | None = None
    min_salary: Decimal | None = None
    company: str | None = None
    posted_after: datetime | None = None


@dataclass(frozen=True)
class RawJob:
    """Vaga como recebida da fonte, antes da normalização e da deduplicação."""

    url: str
    title: str
    company: str
    description: str = ""
    external_id: str | None = None
    location: str | None = None
    work_model: WorkModel | None = None
    seniority: Seniority | None = None
    requirements: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    salary_currency: str | None = None
    posted_at: datetime | None = None


class JobProvider(ABC):
    info: ProviderInfo

    @abstractmethod
    def search(self, query: JobSearchQuery) -> list[RawJob]:
        """Pesquisa vagas. Lança ProviderError em caso de falha da fonte."""

    @abstractmethod
    def get_details(self, external_id: str) -> RawJob | None:
        """Consulta os detalhes de uma vaga pelo identificador da fonte."""


class ManualOnlyProvider(JobProvider):
    """Plataforma conhecida, mas sem integração autorizada disponível.

    Existe para que o catálogo informe com clareza que a fonte depende do fluxo
    manual (POST /jobs/import), em vez de fingir uma integração inexistente.
    """

    def __init__(self, key: str, name: str, notes: str) -> None:
        self.info = ProviderInfo(
            key=key,
            name=name,
            integration_status=IntegrationStatus.MANUAL_ONLY,
            capabilities=frozenset(),
            notes=notes,
        )

    def search(self, query: JobSearchQuery) -> list[RawJob]:
        raise ProviderError(f"{self.info.name} não possui integração automática de pesquisa.")

    def get_details(self, external_id: str) -> RawJob | None:
        raise ProviderError(f"{self.info.name} não possui integração automática de consulta.")
