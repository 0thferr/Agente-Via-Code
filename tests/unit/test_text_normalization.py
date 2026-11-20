import pytest

from app.core.text_normalization import (
    canonicalize_url,
    clean_external_text,
    content_fingerprint,
    infer_work_model,
    normalize_text,
)
from app.models.enums import WorkModel


def test_normalize_text_removes_accents_case_and_punctuation():
    assert normalize_text("  Desenvolvedora(o)   Júnior! ") == "desenvolvedora o junior"


@pytest.mark.parametrize(
    "url",
    [
        "https://Jobs.Example.com/vaga/1",
        "https://jobs.example.com/vaga/1/",
        "https://jobs.example.com/vaga/1?utm_source=x&utm_medium=y",
        "https://jobs.example.com/vaga/1#apply",
        "https://jobs.example.com/vaga/1?gclid=abc",
    ],
)
def test_canonicalize_url_ignores_tracking_and_formatting(url):
    assert canonicalize_url(url) == "https://jobs.example.com/vaga/1"


def test_canonicalize_url_keeps_meaningful_params_sorted():
    url = "https://jobs.example.com/vaga?b=2&a=1&utm_campaign=z"

    assert canonicalize_url(url) == "https://jobs.example.com/vaga?a=1&b=2"


def test_fingerprint_is_stable_across_formatting():
    first = content_fingerprint("Acme Tech", "Backend Júnior", "São Paulo, SP")
    second = content_fingerprint("ACME  tech", "backend junior", "Sao Paulo SP")

    assert first == second


def test_fingerprint_differs_for_different_location():
    assert content_fingerprint("Acme", "Dev", "Recife") != content_fingerprint("Acme", "Dev", None)


def test_clean_external_text_strips_html_and_control_chars():
    raw = "<p>Vaga&nbsp;<b>Python</b></p>\x00<script>alert(1)</script>"

    cleaned = clean_external_text(raw)

    assert "<" not in cleaned and "\x00" not in cleaned
    assert "Python" in cleaned


def test_clean_external_text_truncates():
    assert len(clean_external_text("a" * 50, max_length=10)) == 10


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (("Remoto - Brasil",), WorkModel.REMOTE),
        (("São Paulo", "Dev Híbrido"), WorkModel.HYBRID),
        (("Presencial em Recife",), WorkModel.ONSITE),
        (("São Paulo",), None),
        (("Remoto ou presencial",), None),  # ambíguo: não presumimos
    ],
)
def test_infer_work_model_only_when_explicit(texts, expected):
    assert infer_work_model(*texts) == expected
