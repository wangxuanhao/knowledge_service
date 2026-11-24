"""Application-facing workflows used by the HTTP layer.

This module is the public boundary between transport code and persistence.  It
deliberately exposes the repository's already-stable application operations
while keeping the concrete repository type, connection and private primitives
out of route modules.  Larger use cases can be moved behind explicit methods
without changing their HTTP contracts.
"""
from __future__ import annotations


class ApplicationData:
    """Public application data port.

    ``Repository`` remains an implementation detail of the service layer.  The
    explicit transaction helpers below are the only write orchestration seams;
    ordinary public repository operations are forwarded to preserve the
    existing API while route-owned use cases are incrementally deepened.
    """

    def __init__(self, repository):
        self.__repository = repository

    @property
    def backend_label(self):
        return self.__repository.backend_label

    @property
    def ontology_drafts_store(self):
        """Public store port required by the governed ontology workflow."""
        return self.__repository._ontology_drafts

    def unit_of_work(self, *, serializable=False):
        return self.__repository.unit_of_work(serializable=serializable)

    def assertions_batch(self, project_id, record_ids):
        """Bounded evidence for a displayed ledger page."""
        from ..repository.page_reads import PageReads
        return PageReads(self.__repository).assertions_batch(project_id,record_ids)

    def assertion_evidence(self, project_id, assertion_id):
        """Resolve frozen sources using the concrete persistence dependency."""
        from .evidence import resolve_assertion_evidence
        return resolve_assertion_evidence(self.__repository, project_id, assertion_id)

    def latest_document_processing_status(self, project_id, document_id):
        return self.__repository.ingest_batches.latest_document_status(project_id, document_id)

    def metadata_groups(self, project_id, scope):
        """Scoped metadata objects weighted by their record counts."""
        from ..repository.page_reads import PageReads
        return PageReads(self.__repository).metadata_groups(project_id, scope)

    def projection_status(self, project_id, projection_sync):
        """Selected-project index delivery state and active collection."""
        from ..repository.projection_store import milvus_delivery_state, projection_is_ready
        from ..utils.diagnostics import redact
        with self.__repository.independent_read_view() as reader:
            reader.get_project(project_id)
            cursor = reader.projections.cursor(project_id, 'milvus')
            watermark = reader.projections.project_watermark(project_id)
            rebuild = (cursor or {}).get('rebuild_state') or {}
            details = reader.projections.index_summary(project_id, rebuild.get('index_version'))
        schema_version = projection_sync.schema_version
        state = milvus_delivery_state(cursor, schema_version)
        if state == 'leased':
            state = 'building'
        elif state == 'available':
            state = 'ready' if projection_is_ready(watermark, cursor, schema_version) else 'building'
        adapter = projection_sync.adapters.get('milvus')
        encoder = getattr(adapter, '_encoder', None)
        if encoder and rebuild.get('embedding_model') and rebuild['embedding_model'] != encoder.identity:
            state = 'requires_rebuild'
        job = details.get('job')
        if job and job['status'] in {'queued','running','retry_wait'}:
            state = 'building'
        return {'project_id': project_id, 'state': state,
                'last_error': redact((cursor or {}).get('last_error') or (job or {}).get('error') or ''),
                'collection': (cursor or {}).get('active_destination'),
                'automatic': adapter is not None, 'watermark': watermark,
                'applied_seq': (cursor or {}).get('applied_seq', 0),
                'index_version': rebuild.get('index_version'),
                'embedding_model': rebuild.get('embedding_model'),
                **details}

    def insert_if_absent(self, project_id, record):
        return self.__repository.insert_if_absent(project_id, record)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self.__repository, name)
