"""Redact secrets from logs, errors, raw archives, and API bodies."""

from __future__ import annotations

import re
from collections.abc import Iterable

_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-+/=]+")
_AUTH_HEADER = re.compile(r"(?i)(authorization\s*[:=]\s*)(\S+)")
_ASSIGNMENT = re.compile(
    r"(?i)\b(APP_TOKEN|TG_TOKEN|TELEGRAM_TOKEN|TYPESAFE_API_KEY|BOT_TOKEN)\b\s*[=:]\s*(\S+)"
)
_TELEGRAM_BOT = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
_QUERY_TOKEN = re.compile(r"(?i)([?&](?:access_token|token|app_token)=)[^&\s]+")


def scrub(text: str, secrets: Iterable[str] | None = None) -> str:
    if text is None:
        return ""
    cleaned = str(text)
    for secret in secrets or ():
        if secret and len(secret) >= 4:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    cleaned = _BEARER.sub("Bearer [REDACTED]", cleaned)
    cleaned = _AUTH_HEADER.sub(r"\1[REDACTED]", cleaned)
    cleaned = _ASSIGNMENT.sub(r"\1=[REDACTED]", cleaned)
    cleaned = _TELEGRAM_BOT.sub("[REDACTED_TELEGRAM]", cleaned)
    cleaned = _QUERY_TOKEN.sub(r"\1[REDACTED]", cleaned)
    return cleaned


class ScrubFilter:
    """logging.Filter that rewrites the rendered message."""

    def __init__(self, secrets: Iterable[str] | None = None):
        self.secrets = list(secrets or [])

    def filter(self, record) -> bool:
        record.msg = scrub(str(record.msg), self.secrets)
        if record.args:
            record.args = tuple(scrub(str(arg), self.secrets) for arg in record.args)
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            scrubbed = scrub(str(exc), self.secrets)
            record.exc_text = scrubbed
            record.msg = scrub(record.msg, self.secrets)
        return True
