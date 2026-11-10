"""Secret redaction for anything flakehound writes out.

Test failure messages can contain secrets (a test that prints a config, an
assertion comparing tokens...). Every string that leaves flakehound through a
report goes through :func:`redact` first.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

REDACTED = "[REDACTED]"

# Well-known token formats.
_TOKEN_PATTERNS = [
    r"gh[pousr]_[A-Za-z0-9]{20,}",  # GitHub tokens
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"(?:AKIA|ASIA)[A-Z0-9]{16}",  # AWS access key ids
    r"xox[abprs]-[A-Za-z0-9-]{10,}",  # Slack
    r"sk-[A-Za-z0-9_-]{20,}",  # OpenAI / Anthropic style keys
    r"AIza[0-9A-Za-z_-]{35}",  # Google API keys
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",  # JWTs
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
]
_TOKEN_RE = re.compile("|".join(f"(?:{p})" for p in _TOKEN_PATTERNS))

# key=value / key: value where the key name sounds secret.
_SECRET_WORDS = r"pass(?:word|wd)?|secret|token|api[_-]?key|auth|credential|private[_-]?key"  # noqa: S105 - a regex, not a password
_SECRET_NAME = rf"[A-Za-z0-9_.-]*(?:{_SECRET_WORDS})[A-Za-z0-9_.-]*"
_ASSIGN_RE = re.compile(
    rf"(?i)(?P<key>{_SECRET_NAME})(?P<sep>['\"]?\s*[:=]\s*['\"]?)(?P<val>[^\s'\",;&]+)"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_CRED_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@")

_SECRET_ENV_NAME = re.compile(r"(?i)(pass|secret|token|key|auth|credential|cookie|session)")
_MIN_ENV_SECRET_LEN = 6


def _secret_env_values(environ: Mapping[str, str]) -> list[str]:
    values = {
        v
        for k, v in environ.items()
        if _SECRET_ENV_NAME.search(k) and len(v) >= _MIN_ENV_SECRET_LEN
    }
    # Longest first so overlapping values are fully replaced.
    return sorted(values, key=len, reverse=True)


def redact(text: str, environ: Mapping[str, str] | None = None) -> str:
    """Return ``text`` with anything that looks like a secret replaced."""
    if not text:
        return text
    env = os.environ if environ is None else environ
    for value in _secret_env_values(env):
        text = text.replace(value, REDACTED)
    text = _TOKEN_RE.sub(REDACTED, text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _URL_CRED_RE.sub(lambda m: f"{m.group('scheme')}{REDACTED}@", text)
    text = _ASSIGN_RE.sub(lambda m: f"{m.group('key')}{m.group('sep')}{REDACTED}", text)
    return text


def truncate(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n... [truncated]"
