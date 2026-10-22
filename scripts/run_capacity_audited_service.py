"""Temporary, opt-in capacity instrumentation; run the normal app exactly once.

No SQL text, arguments, request bodies, query strings, or credentials are logged.
Durations are nested observations and MUST NOT be added together.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from uuid import UUID, uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUEST = ContextVar('capacity_audit_request', default=None)
PHASES = ContextVar('capacity_audit_phases', default=())
_UUID = re.compile(r'^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$')


class Recorder:
    """Serialize small JSONL checkpoints without making logging a business error."""

    def __init__(self, path=None, *, stream=None):
        if stream is None:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = target.open('x', encoding='utf-8', buffering=1)
        self.stream = stream
        self.lock = threading.Lock()
        self.failed = False

    def emit(self, label, duration_seconds=0.0, **fields):
        row = {'wall_time': time.time(), 'label': label,
               'duration_seconds': duration_seconds,
               'request_id': REQUEST.get(), 'thread_id': threading.get_ident(),
               **fields}
        try:
            with self.lock:
                self.stream.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + '\n')
                self.stream.flush()
        except Exception as exc:
            if not self.failed:
                self.failed = True
                print(f'Capacity audit output failed: {type(exc).__name__}', file=sys.stderr)


def row_counts(value):
    if isinstance(value, (list, tuple)):
        return {'rows': len(value)}
    if isinstance(value, dict):
        names = ('records', 'assertions', 'assertion_events', 'fact_keys',
                 'record_version_assertions', 'resolution_reviews',
                 'merge_operations', 'accepted_records', 'actions')
        return {f'{key}_rows': len(value[key]) for key in names
                if isinstance(value.get(key), (list, tuple))}
    return {}


def callsite_stack(limit=8):
    """Read frame metadata without loading source lines on every lock acquire."""
    result = []
    frame = sys._getframe(2)
    try:
        while frame is not None and len(result) < limit:
            result.append(f'{frame.f_code.co_name}:{frame.f_lineno}')
            frame = frame.f_back
        return result
    finally:
        del frame


@contextmanager
def phase(recorder, label, **fields):
    parents = PHASES.get()
    token = PHASES.set((*parents, label))
    started = time.perf_counter()
    outcome = dict(fields)
    try:
        yield outcome
    except BaseException as exc:
        outcome['error_type'] = type(exc).__name__
        raise
    finally:
        elapsed = time.perf_counter() - started
        PHASES.reset(token)
        recorder.emit(label, elapsed, parent=parents[-1] if parents else None, **outcome)


class AuditedRLock:
    """Delegate locking semantics; observe only successful outer ownership spans."""

    def __init__(self, lock, recorder, label):
        self._raw = lock
        self._recorder = recorder
        self._label = label
        self._state = threading.Lock()
        self._owner = None
        self._depth = 0
        self._since = None
        self._owner_request = None
        self._owner_phase = None
        self._owner_stack = None

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def acquire(self, *args, **kwargs):
        with self._state:
            observed_owner = self._owner
            observed_request = self._owner_request
            observed_phase = self._owner_phase
            observed_stack = self._owner_stack
        started = time.perf_counter()
        acquired = self._raw.acquire(*args, **kwargs)
        waited = time.perf_counter() - started
        if acquired:
            with self._state:
                if self._owner == threading.get_ident():
                    self._depth += 1
                else:
                    self._owner = threading.get_ident()
                    self._depth = 1
                    self._since = time.perf_counter()
                    self._owner_request = REQUEST.get()
                    self._owner_phase = PHASES.get()[-1] if PHASES.get() else None
                    self._owner_stack = callsite_stack()
                depth = self._depth
        else:
            depth = None
        self._recorder.emit(self._label + '.wait', waited, acquired=bool(acquired),
                            depth=depth, owner_thread_id=observed_owner,
                            owner_request_id=observed_request, owner_phase=observed_phase,
                            owner_stack=observed_stack)
        return acquired

    def release(self):
        held = None
        with self._state:
            self._raw.release()  # Preserve RuntimeError on an invalid release.
            if self._owner == threading.get_ident():
                self._depth -= 1
                if self._depth == 0:
                    held = time.perf_counter() - self._since
                    self._owner = self._since = self._owner_request = None
                    self._owner_phase = self._owner_stack = None
        if held is not None:
            self._recorder.emit(self._label + '.hold', held)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


_TABLES = ('record_versions', 'record_operation_reservations', 'projects', 'ontologies',
           'assertions', 'assertion_events', 'record_version_assertions', 'fact_keys',
           'resolution_reviews', 'merge_operations', 'projection_outbox', 'artifacts')


def sql_label(statement):
    """Return only allowlisted structural labels, never SQL or literal values."""
    words = re.findall(r'[a-z_]+', str(statement).lower())
    operation = words[0] if words and words[0] in {
        'select', 'insert', 'update', 'delete', 'lock', 'begin', 'commit',
        'rollback', 'savepoint', 'release', 'set', 'with'} else 'other'
    if 'pg_advisory_xact_lock' in words:
        return operation + '.advisory_lock'
    table = next((name for name in _TABLES if name in words), None)
    return operation + ('.' + table if table else '')


class CursorProbe:
    def __init__(self, cursor, recorder, label):
        self._cursor, self._recorder, self._label = cursor, recorder, label

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __iter__(self):
        return iter(self._cursor)

    def __next__(self):
        return next(self._cursor)

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self._cursor.__exit__(*args)

    def fetchall(self):
        with phase(self._recorder, self._label + '.fetchall') as counts:
            result = self._cursor.fetchall()
            counts['rows'] = len(result)
            return result

    def fetchone(self):
        with phase(self._recorder, self._label + '.fetchone') as counts:
            result = self._cursor.fetchone()
            counts['rows'] = int(result is not None)
            return result

    def fetchmany(self, *args, **kwargs):
        with phase(self._recorder, self._label + '.fetchmany') as counts:
            result = self._cursor.fetchmany(*args, **kwargs)
            counts['rows'] = len(result)
            return result


def wrap_method(target, name, recorder, label):
    original = getattr(target, name, None)
    if original is None:
        return False

    @wraps(original)
    def measured(*args, **kwargs):
        with phase(recorder, label) as counts:
            result = original(*args, **kwargs)
            counts.update(row_counts(result))
            return result

    setattr(target, name, measured)
    return True


def install_instrumentation(app, recorder):
    """Patch this process after ordinary app creation, including future readers."""
    from knowledge_service.repository.core import Repository
    from knowledge_service.repository.pg_engine import SqliteishConnection
    from knowledge_service.repository.formal_write_executor import FormalWriteExecutor
    from knowledge_service.services.formal_writes import FormalFactWriter

    service = app.state.service
    repository = service.repository
    service.lock = AuditedRLock(service.lock, recorder, 'lock.service')
    repository._lock = AuditedRLock(repository._lock, recorder, 'lock.repository.writer')
    original_read_view = Repository.independent_read_view

    @contextmanager
    @wraps(original_read_view)
    def read_view(self):
        with phase(recorder, 'repo.independent_read_view'):
            with original_read_view(self) as reader:
                if not isinstance(reader._lock, AuditedRLock):
                    reader._lock = AuditedRLock(reader._lock, recorder, 'lock.repository.reader')
                yield reader

    Repository.independent_read_view = read_view
    for name in ('execute', 'executemany'):
        original = getattr(SqliteishConnection, name)

        def make_sql_wrapper(original, operation):
            @wraps(original)
            def measured(self, statement, *args, **kwargs):
                role = 'writer' if self._owns_connection else 'reader'
                label = f'sql.{role}.{operation}.{sql_label(statement)}'
                with phase(recorder, label) as counts:
                    cursor = original(self, statement, *args, **kwargs)
                    count = getattr(cursor, 'rowcount', -1)
                    counts['rows'] = count if count >= 0 else None
                return CursorProbe(cursor, recorder, label)
            return measured

        setattr(SqliteishConnection, name, make_sql_wrapper(original, name))

    methods = (
        (Repository, 'formal_write_snapshot', 'repo.planning_snapshot'),
        (Repository, '_formal_write_snapshot_data', 'repo.snapshot_data'),
        (Repository, '_formal_write_snapshot_locked', 'repo.snapshot_locked'),
        (Repository, '_reserve_record_operation', 'repo.reserve_operation'),
        (Repository, 'execute_formal_write', 'repo.execute_formal_write'),
        (Repository, '_lock_project_write_tables', 'repo.table_lock'),
        (Repository, 'current_records', 'repo.current_records'),
        (Repository, 'history', 'repo.record_history'),
        (FormalFactWriter, 'prepare', 'formal.prepare'),
        (FormalFactWriter, 'apply', 'formal.apply'),
        (FormalWriteExecutor, 'execute', 'formal.execute_actions'),
        (type(service), '_timeline_check', 'service.timeline_check'),
        (type(service), 'annotate_structure_pending', 'service.annotate_result'),
        (type(service), '_drain_projections', 'service.projection_nudge'),
        (type(service.encoder), 'encode', 'encoder.encode'),
    )
    installed = [label for target, name, label in methods
                 if wrap_method(target, name, recorder, label)]
    # Keep the descriptor semantics of the static fingerprint function.
    original_fingerprint = Repository._publish_fingerprint

    @wraps(original_fingerprint)
    def fingerprint(value):
        with phase(recorder, 'repo.canonical_fingerprint'):
            return original_fingerprint(value)

    Repository._publish_fingerprint = staticmethod(fingerprint)
    for name in ('drain_once', 'rebuild'):
        sync = getattr(service, 'projection_sync', None)
        if sync is not None:
            wrap_method(type(sync), name, recorder, 'projection.' + name)
    app.add_middleware(RequestAudit, recorder=recorder)
    recorder.emit('audit.installed', pid=os.getpid(), installed=installed,
                  nested_durations_are_additive=False)


def request_id(headers):
    for key, value in headers:
        if key.lower() == b'x-capacity-request':
            try:
                text = value.decode('ascii')
                if _UUID.fullmatch(text):
                    return str(UUID(text))
            except (UnicodeError, ValueError):
                pass
    return str(uuid4())


class RequestAudit:
    def __init__(self, app, recorder):
        self.app, self.recorder = app, recorder

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        identifier = request_id(scope.get('headers', ()))
        request_token = REQUEST.set(identifier)
        phases_token = PHASES.set(())
        started = time.perf_counter()
        status = None
        byte_count = 0
        error_type = None
        method = scope.get('method')
        if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'}:
            method = 'OTHER'
        self.recorder.emit('http.start', method=method)

        async def audited_send(message):
            nonlocal status, byte_count
            if message['type'] == 'http.response.start':
                status = message['status']
                message = {**message, 'headers': [
                    *[(key, value) for key, value in message.get('headers', ())
                      if key.lower() != b'x-capacity-request'],
                    (b'x-capacity-request', identifier.encode('ascii'))]}
            elif message['type'] == 'http.response.body':
                byte_count += len(message.get('body', b''))
            await send(message)

        try:
            return await self.app(scope, receive, audited_send)
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            route = getattr(scope.get('route'), 'path', None) or 'unmatched'
            self.recorder.emit('http.total', time.perf_counter() - started,
                               method=method, route=route, status=status,
                               response_bytes=byte_count, error_type=error_type)
            PHASES.reset(phases_token)
            REQUEST.reset(request_token)


def main():
    from knowledge_service.core.config import load_environment
    load_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8100)
    parser.add_argument('--output', '--output-privatepath', required=True, dest='output')
    args = parser.parse_args()
    from knowledge_service.core.logging import configure_logging
    configure_logging()
    from knowledge_service.api import create_app
    import uvicorn
    recorder = Recorder(args.output)
    app = create_app()
    install_instrumentation(app, recorder)
    # Uvicorn's default access log includes query strings; the JSONL request
    # observations above use only registered route templates instead.
    uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == '__main__':
    main()
