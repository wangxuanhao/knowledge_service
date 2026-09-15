"""Task-local stage tracing; no prompts, response bodies or credentials."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import re
import time

_report = ContextVar('ingest_report', default=None)


def redact(value):
    text = str(value)
    for name, secret in os.environ.items():
        if secret and len(secret)>3 and any(k in name.upper() for k in ('KEY','TOKEN','PASSWORD','SECRET')):
            text = text.replace(secret, '<REDACTED>')
    return re.sub(r'(?i)(bearer\s+|api[_-]?key[=:]\s*)[^\s,;]+', r'\1<REDACTED>', text)


def event(message, percent=None):
    callback = _report.get()
    if callback:
        callback(redact(message), percent)


@contextmanager
def reporting(callback):
    token = _report.set(callback)
    try:
        yield
    finally:
        _report.reset(token)


@contextmanager
def stage(name, percent=None):
    started = time.monotonic()
    event(name+' · 开始', percent)
    try:
        yield
    except Exception as exc:
        # Exception types retain causality without exposing provider bodies.
        chain=[]; current=exc
        while current is not None and len(chain)<5:
            chain.append(type(current).__name__)
            current=current.__cause__
        event(f'{name} · 失败 · {time.monotonic()-started:.1f}s · '+ ' ← '.join(chain))
        raise
    else:
        event(f'{name} · 完成 · {time.monotonic()-started:.1f}s')
