"""Application operations for scoped graph views and paged knowledge records."""
from contextlib import nullcontext
from functools import wraps

from ..repository.scoped_reads import ScopedReads
from .graph_sessions import GraphSessions
from .graph_neighborhood import present_neighborhood
from .service import public


def _snapshot_operation(operation):
    @wraps(operation)
    def read(self, *args, **kwargs):
        factory = getattr(self.service, 'independent_read_view', None)
        with factory() if factory is not None else nullcontext(self.service) as service:
            workspace = self if service is self.service else GraphWorkspace(service)
            return operation(workspace, *args, **kwargs)
    return read


class GraphWorkspace:
    def __init__(self, service):
        self.service = service
        self.reads = ScopedReads(service.repository)
        self.sessions = GraphSessions(service.repository)

    @_snapshot_operation
    def neighborhood(self, project, values):
        values = dict(values)
        options = {key: values.pop(key) for key in ('seeds', 'hops', 'direction', 'max_nodes', 'max_edges')}
        result = self.reads.neighbors(project, values, **options)
        return present_neighborhood(self.service, self.reads, project, values, result, options['seeds'])

    @_snapshot_operation
    def records_page(self, project, values):
        values = dict(values)
        query, cursor, limit, include_total, record_ids = (
            values.pop(key) for key in ('query', 'cursor', 'limit', 'include_total', 'record_ids'))
        if record_ids is not None:
            if cursor is not None or query:
                raise ValueError('精确记录定位不能同时提供分页游标或关键词')
            records = self.reads.by_ids(project, values, record_ids)
            result = {'records': records, 'total': len(records) if include_total else None, 'next_cursor': None}
        else:
            result = self.reads.page(project, values, query=query, after=cursor,
                                     limit=limit, include_total=include_total)
        endpoint_ids = {record.get(key) for record in result['records']
                        for key in ('subject_id', 'object_id') if record.get(key)}
        endpoints = self.reads.by_ids(project, values, sorted(endpoint_ids), kinds=['entity']) if endpoint_ids else []
        self.service.annotate_structure_pending(project, [*result['records'], *endpoints])
        return {**result, 'records': [public(record) for record in result['records']]}
