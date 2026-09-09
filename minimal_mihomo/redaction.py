"""Conservative redaction for diagnostics; raw core output never becomes errors."""
import re
from urllib.parse import urlsplit, urlunsplit


def redact_url(value: str) -> str:
    try:
        url = urlsplit(value)
        host = url.hostname or ''
        if ':' in host:
            host = '[' + host + ']'
        if url.port:
            host += ':' + str(url.port)
        # Subscription credentials are sometimes encoded in path segments too.
        return urlunsplit((url.scheme, host, '/[redacted]', '', ''))
    except ValueError:
        return '[redacted URL]'


def redact(text: str, secrets=()) -> str:
    for secret in sorted((str(s) for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, '[redacted]')
    text = re.sub(r'https?://[^\s\"\'<>]+', lambda m: redact_url(m.group()), text)
    text = re.sub(r'(?i)(authorization\s*[:=]\s*)([^\n]+)', r'\1[redacted]', text)
    text = re.sub(r'(?i)((?:password|passwd|secret|token|uuid|private-key)\s*[\"\']?\s*[:=]\s*)([^,\s}\]]+)', r'\1[redacted]', text)
    return text


def collect_secrets(value):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if any(word in str(key).lower() for word in ('password', 'secret', 'token', 'uuid', 'private-key')):
                if isinstance(item, (str, int)):
                    found.append(str(item))
            found.extend(collect_secrets(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(collect_secrets(item))
    return found
