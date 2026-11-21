from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.integrations.job_providers.base import JobSearchQuery, ProviderCapability, ProviderError
from app.integrations.job_providers.mock import MockJobProvider
from app.integrations.job_providers.registry import build_default_registry
from app.models.enums import Seniority, WorkModel

provider = MockJobProvider()


def test_search_without_filters_returns_all():
    assert len(provider.search(JobSearchQuery())) == len(provider._jobs)


def test_search_filters_by_technology_and_seniority():
    jobs = provider.search(JobSearchQuery(technologies=["python"], seniority=Seniority.JUNIOR))

    assert jobs
    assert all(job.seniority is Seniority.JUNIOR for job in jobs)


def test_search_filters_by_work_model():
    jobs = provider.search(JobSearchQuery(work_model=WorkModel.ONSITE))

    assert {job.work_model for job in jobs} == {WorkModel.ONSITE}


def test_min_salary_keeps_jobs_without_salary_information():
    jobs = provider.search(JobSearchQuery(min_salary=Decimal("8000")))

    assert all(job.salary_max is None or job.salary_max >= 8000 for job in jobs)
    assert any(job.salary_max is None for job in jobs)


def test_search_filters_by_posted_after():
    cutoff = datetime(2026, 11, 16, tzinfo=UTC)

    jobs = provider.search(JobSearchQuery(posted_after=cutoff))

    assert jobs and all(job.posted_at >= cutoff for job in jobs)


def test_get_details():
    assert provider.get_details("mock-001").company == "Acme Tech"
    assert provider.get_details("missing") is None


def test_registry_marks_real_platforms_as_manual_only():
    registry = build_default_registry(include_simulated=False)
    linkedin = registry.get("linkedin")

    assert not linkedin.info.supports(ProviderCapability.SEARCH)
    with pytest.raises(ProviderError):
        linkedin.search(JobSearchQuery())


def test_registry_can_exclude_simulated_provider():
    keys = {p.info.key for p in build_default_registry(include_simulated=False).all()}

    assert "mock" not in keys
