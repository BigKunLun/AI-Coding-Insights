import re

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(?:api[_-]?key|token|password|secret)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{8,}"),
]

def redact_secrets(text: str) -> str:
    for p in _PATTERNS:
        text = p.sub("[REDACTED]", text)
    return text
