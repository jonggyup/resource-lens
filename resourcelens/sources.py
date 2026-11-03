"""Bounded reads with explicit outcomes; no missing-to-zero conversion."""

from dataclasses import dataclass
import errno
from pathlib import Path


def error_reason(error, missing='not_exposed'):
    if error.errno in (errno.EACCES, errno.EPERM):
        return 'permission_denied'
    if error.errno in (errno.ENOENT, errno.ESRCH, errno.ENOTDIR):
        return missing
    if error.errno in (errno.ETIMEDOUT, errno.EAGAIN):
        return 'timed_out'
    return 'unknown'


@dataclass(frozen=True)
class ReadResult:
    text: str | None
    reason: str | None = None
    bytes_read: int = 0

    @property
    def status(self):
        return 'ok' if self.reason is None else 'unavailable'


def read_text(path, limit=262144, missing='not_exposed'):
    raw = b''
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            return ReadResult(None, 'budget_exceeded', len(raw))
        return ReadResult(raw.decode('utf-8').strip(), bytes_read=len(raw))
    except UnicodeError:
        return ReadResult(None, 'parse_error', len(raw))
    except OSError as exc:
        return ReadResult(None, error_reason(exc, missing))
