"""Normalização de textos e URLs usada para comparar e deduplicar vagas."""

import hashlib
import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models.enums import WorkModel

TRACKING_QUERY_PARAMS = {"gclid", "fbclid", "ref", "refid", "trk", "trackingid", "source"}
TRACKING_QUERY_PREFIXES = ("utm_",)
MAX_DESCRIPTION_LENGTH = 20_000

_HTML_TAG = re.compile(r"<[^>]+>")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NON_WORD = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")

_WORK_MODEL_KEYWORDS = {
    WorkModel.REMOTE: ("remoto", "remote", "home office", "anywhere"),
    WorkModel.HYBRID: ("hibrido", "hybrid"),
    WorkModel.ONSITE: ("presencial", "on-site", "onsite"),
}


def normalize_text(value: str) -> str:
    """Minúsculas, sem acentos, sem pontuação e com espaços simples."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _WHITESPACE.sub(" ", _NON_WORD.sub(" ", without_accents)).strip()


def canonicalize_url(url: str) -> str:
    """Remove fragmento, parâmetros de rastreamento e barra final; ordena a query."""
    parts = urlsplit(url.strip())
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if key.lower() not in TRACKING_QUERY_PARAMS
        and not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def content_fingerprint(company: str, title: str, location: str | None) -> str:
    """Identifica a mesma vaga publicada em fontes ou URLs diferentes."""
    parts = [normalize_text(company), normalize_text(title), normalize_text(location or "")]
    return sha256_hex("|".join(parts))


def clean_external_text(value: str, max_length: int = MAX_DESCRIPTION_LENGTH) -> str:
    """Converte conteúdo externo em texto puro: sem HTML nem caracteres de controle.

    O resultado continua sendo dado não confiável — nunca deve ser tratado como
    instrução (a proteção contra prompt injection é aplicada na camada de IA).
    """
    text = html.unescape(_HTML_TAG.sub(" ", value))
    text = _CONTROL_CHARS.sub("", text)
    return text.strip()[:max_length]


def infer_work_model(*texts: str | None) -> WorkModel | None:
    """Deduz a modalidade só quando há exatamente uma menção explícita; senão, None."""
    normalized = normalize_text(" ".join(text for text in texts if text))
    found = {
        model
        for model, keywords in _WORK_MODEL_KEYWORDS.items()
        if any(normalize_text(keyword) in normalized for keyword in keywords)
    }
    return found.pop() if len(found) == 1 else None
