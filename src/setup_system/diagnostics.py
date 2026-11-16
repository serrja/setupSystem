"""Bounded diagnostic redaction shared by installation boundaries."""

from __future__ import annotations

import re
import subprocess


def error_text(error: BaseException | str) -> str:
    """Keep actionable failure details without credentials, terminal controls or raw argv."""
    if isinstance(error, subprocess.CalledProcessError):
        return f"Command failed with exit status {error.returncode}"
    if isinstance(error, subprocess.TimeoutExpired):
        return f"Command timed out after {error.timeout} seconds"
    text = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[REDACTED]@", str(error))
    text = re.sub(
        r"(?im)((?:proxy-)?authorization[ \t]*[:=][ \t]*)[^\r\n]*",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"""(?i)((?:password|passwd|token|secret|api[-_]key|authorization)\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;&]+)""",
        r"\1[REDACTED]",
        text,
    )
    text = " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text).split())
    if len(text) > 2000:
        text = text[:800] + " ... " + text[-1195:]
    return text or "No additional error details"
