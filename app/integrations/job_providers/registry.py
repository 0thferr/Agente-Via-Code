"""Catálogo dos conectores de vagas disponíveis.

Plataformas sem integração autorizada aparecem como MANUAL_ONLY: o usuário
importa as vagas pelo link (POST /jobs/import). Nenhuma delas faz scraping.
"""

from app.core.errors import NotFoundError
from app.integrations.job_providers.base import JobProvider, ManualOnlyProvider
from app.integrations.job_providers.mock import MockJobProvider

MANUAL_PROVIDER_KEY = "manual"

_MANUAL_ONLY_PLATFORMS = [
    (
        "linkedin",
        "LinkedIn",
        "A API de vagas do LinkedIn é restrita a parceiros aprovados; scraping viola os "
        "termos de uso. Use a importação manual.",
    ),
    (
        "gupy",
        "Gupy",
        "A API oficial da Gupy é voltada a empresas recrutadoras, não a candidatos. "
        "Use a importação manual.",
    ),
    (
        "indeed",
        "Indeed",
        "A API pública de busca do Indeed foi descontinuada; o acesso atual é restrito a "
        "parceiros. Use a importação manual.",
    ),
    ("catho", "Catho", "Sem API pública para candidatos. Use a importação manual."),
    ("vagas_com_br", "Vagas.com.br", "Sem API pública para candidatos. Use a importação manual."),
    (
        "career_page",
        "Páginas de carreira",
        "Páginas oficiais de empresas. Algumas expõem feeds públicos (ex.: ATS como "
        "Greenhouse/Lever); conectores específicos podem ser adicionados por empresa.",
    ),
    (MANUAL_PROVIDER_KEY, "Importação manual", "Vagas cadastradas pelo próprio usuário."),
]


class ProviderRegistry:
    def __init__(self, providers: list[JobProvider]) -> None:
        self._providers = {provider.info.key: provider for provider in providers}

    def get(self, key: str) -> JobProvider:
        provider = self._providers.get(key)
        if provider is None:
            raise NotFoundError(f"Provedor de vagas '{key}' não existe.")
        return provider

    def all(self) -> list[JobProvider]:
        return list(self._providers.values())


def build_default_registry(*, include_simulated: bool) -> ProviderRegistry:
    providers: list[JobProvider] = [
        ManualOnlyProvider(key, name, notes) for key, name, notes in _MANUAL_ONLY_PLATFORMS
    ]
    if include_simulated:
        providers.insert(0, MockJobProvider())
    return ProviderRegistry(providers)
