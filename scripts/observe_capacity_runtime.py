"""Sample one existing app PID and PostgreSQL metadata; never start workloads.

Resource and PostgreSQL loops checkpoint independently so a slow database probe
does not stop the one-second resource series. No SQL source or DSN is recorded.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import signal
import sys
import threading
import time

from run_capacity_audited_service import Recorder


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
APPLICATION_NAME = 'capacity-audit-observer'

# md5 is computed inside PostgreSQL: the observer never receives SQL source.
ACTIVITY_SQL = """
SELECT pid, application_name, state, wait_event_type, wait_event,
       EXTRACT(EPOCH FROM clock_timestamp()-query_start)::double precision AS query_age_seconds,
       EXTRACT(EPOCH FROM clock_timestamp()-xact_start)::double precision AS transaction_age_seconds,
       pg_blocking_pids(pid) AS blocking_pids,
       CASE WHEN query IS NULL OR query='<insufficient privilege>' THEN NULL
            ELSE md5(query) END AS query_hash
FROM pg_stat_activity
WHERE datname=current_database() AND pid<>pg_backend_pid()
  AND application_name<>%s AND backend_type='client backend'
ORDER BY pid
"""
LOCKS_SQL = """
SELECT l.pid, l.locktype, l.mode, l.granted, count(*)::int AS lock_count,
       EXTRACT(EPOCH FROM clock_timestamp()-min(l.waitstart))::double precision AS wait_age_seconds
FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
WHERE a.datname=current_database() AND a.pid<>pg_backend_pid()
  AND a.application_name<>%s AND a.backend_type='client backend'
GROUP BY l.pid,l.locktype,l.mode,l.granted
ORDER BY l.pid,l.locktype,l.mode,l.granted
"""


def safe_error(exc):
    """Exception metadata is sufficient; omit messages that may contain secrets."""
    state = getattr(exc, 'sqlstate', None)
    return {'error_type': type(exc).__name__,
            'sqlstate': state if isinstance(state, str) and re.fullmatch(r'[A-Z0-9]{5}', state) else None}


def optional(obj, name):
    return getattr(obj, name, None) if obj is not None else None


def resource_sample(psutil, process, *, first=False):
    fields = {'host_cpu_percent': None, 'host_memory_available_bytes': None,
              'host_memory_total_bytes': None, 'host_memory_percent': None,
              'process_cpu_percent': None, 'process_rss_bytes': None,
              'process_threads': None, 'process_status': None,
              'host_swap_used_bytes': None, 'host_swap_in_bytes': None,
              'host_swap_out_bytes': None}
    if psutil is None:
        fields['psutil_available'] = False
        return fields
    fields['psutil_available'] = True
    try:
        cpu = psutil.cpu_percent(interval=None)
        fields['host_cpu_percent'] = None if first else cpu
        memory = psutil.virtual_memory()
        fields.update(host_memory_available_bytes=optional(memory, 'available'),
                      host_memory_total_bytes=optional(memory, 'total'),
                      host_memory_percent=optional(memory, 'percent'))
        swap = psutil.swap_memory()
        fields['host_swap_used_bytes'] = optional(swap, 'used')
        # Windows exposes sin/sout as constant zero, not usable paging evidence.
        if sys.platform != 'win32':
            fields['host_swap_in_bytes'] = optional(swap, 'sin')
            fields['host_swap_out_bytes'] = optional(swap, 'sout')
    except Exception as exc:
        fields['host_error'] = safe_error(exc)
    if process is not None:
        try:
            with process.oneshot():
                cpu = process.cpu_percent(interval=None)
                fields.update(process_cpu_percent=None if first else cpu,
                              process_rss_bytes=optional(process.memory_info(), 'rss'),
                              process_threads=process.num_threads(),
                              process_status=process.status())
        except Exception as exc:
            fields['process_error'] = safe_error(exc)
    return fields


def pg_sample(connection):
    with connection.cursor() as cursor:
        cursor.execute(ACTIVITY_SQL, (APPLICATION_NAME,))
        activity = cursor.fetchall()
        cursor.execute(LOCKS_SQL, (APPLICATION_NAME,))
        locks = cursor.fetchall()
    return {'activity': activity, 'locks': locks,
            'activity_rows': len(activity), 'lock_groups': len(locks),
            'blocked_backends': sum(bool(row['blocking_pids']) for row in activity),
            'ungranted_locks': sum(row['lock_count'] for row in locks if not row['granted'])}


def observe_postgres(recorder, stop, deadline, interval=1.0):
    connection = None
    try:
        import psycopg
        from psycopg.rows import dict_row
        from knowledge_service.core.config import load_environment
        from knowledge_service.repository.connection import resolve_dsn
        load_environment()
        dsn = resolve_dsn()
        next_sample = time.monotonic()
        while not stop.is_set() and time.monotonic() < deadline:
            if stop.wait(max(0.0, next_sample-time.monotonic())):
                break
            started = time.perf_counter()
            try:
                if connection is None:
                    connection = psycopg.connect(
                        dsn, application_name=APPLICATION_NAME, connect_timeout=3,
                        autocommit=True, row_factory=dict_row)
                    with connection.cursor() as cursor:
                        cursor.execute('SET default_transaction_read_only = on')
                        cursor.execute('SET statement_timeout = 1500')
                        cursor.execute('SET lock_timeout = 250')
                fields = pg_sample(connection)
                recorder.emit('postgres.sample', time.perf_counter()-started, **fields)
                next_sample += interval
                if next_sample < time.monotonic():
                    next_sample = time.monotonic() + interval
            except Exception as exc:
                recorder.emit('postgres.error', time.perf_counter()-started, **safe_error(exc))
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
                    connection = None
                next_sample = time.monotonic() + 5.0
    except Exception as exc:
        recorder.emit('postgres.unavailable', **safe_error(exc))
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:
                recorder.emit('postgres.close_error', **safe_error(exc))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', required=True, type=int)
    parser.add_argument('--duration', type=float, default=2200)
    parser.add_argument('--interval', type=float, default=1.0)
    parser.add_argument('--output', '--output-privatepath', required=True, dest='output')
    args = parser.parse_args()
    if args.pid <= 0 or args.duration <= 0 or args.interval <= 0:
        parser.error('pid, duration and interval must be positive')
    recorder = Recorder(args.output)
    recorder.emit('observer.started', target_pid=args.pid, duration=args.duration,
                  interval=args.interval, swap_counters_do_not_prove_paging_causality=True)
    stop = threading.Event()

    def stop_requested(signum, frame):
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop_requested)
    try:
        import psutil
    except ImportError as exc:
        recorder.emit('resources.unavailable', **safe_error(exc))
        psutil = None
    process = None
    if psutil is not None:
        try:
            process = psutil.Process(args.pid)
            process.cpu_percent(interval=None)
            psutil.cpu_percent(interval=None)
        except Exception as exc:
            recorder.emit('resources.process_unavailable', **safe_error(exc))
    started = time.monotonic()
    deadline = started + args.duration
    worker = threading.Thread(target=observe_postgres,
                              args=(recorder, stop, deadline, args.interval),
                              name='capacity-pg-observer', daemon=True)
    worker.start()
    next_sample = started
    samples = 0
    try:
        while not stop.is_set() and time.monotonic() < deadline:
            if stop.wait(max(0.0, next_sample-time.monotonic())):
                break
            sample_started = time.perf_counter()
            fields = resource_sample(psutil, process, first=samples == 0)
            recorder.emit('resources.sample', time.perf_counter()-sample_started,
                          target_pid=args.pid, elapsed_seconds=time.monotonic()-started, **fields)
            samples += 1
            next_sample += args.interval
            if next_sample < time.monotonic():
                next_sample = time.monotonic() + args.interval
    finally:
        stop.set()
        worker.join(timeout=5)
        recorder.emit('observer.completed', time.monotonic()-started,
                      resource_samples=samples, pg_thread_stopped=not worker.is_alive())


if __name__ == '__main__':
    main()
