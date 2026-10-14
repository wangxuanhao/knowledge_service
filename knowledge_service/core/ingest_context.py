"""Thread-local commit boundary supplied by leased ingestion, consumed by storage."""
from contextvars import ContextVar

ingest_commit_guard = ContextVar('ingest_commit_guard', default=None)
