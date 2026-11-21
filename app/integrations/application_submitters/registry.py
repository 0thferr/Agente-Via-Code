from app.integrations.application_submitters.base import ApplicationSubmitter


class SubmitterRegistry:
    """Enviadores disponíveis por provedor. Provedor ausente = só fluxo manual."""

    def __init__(self, submitters: list[ApplicationSubmitter]) -> None:
        self._submitters = {submitter.provider_key: submitter for submitter in submitters}

    def get(self, provider_key: str) -> ApplicationSubmitter | None:
        return self._submitters.get(provider_key)
