"""Current candidate read views; writes belong to selected ontology intake."""
from ..repository.discovery_run_store import discovery_result_kind
from ..repository.page_reads import PageReads
from .discovery_vocabulary import audit_formal_vocabulary
from .ontology_discovery import (
    _candidates, _candidate_lifecycle, _candidate_mindmap, _quality_warnings, _summary,
)


class OntologyDiscoveryWorkflow:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def _runs(repository, project):
        return repository.list_intake_runs(project)

    def overview(self, p):
        with self.service.repository.independent_read_view() as repository:
            return self._overview_from(repository, p)

    def _overview_from(self, repository, p):
        inputs = PageReads(repository).discovery_inputs(p)
        candidates = _candidates(inputs['documents'], inputs['assertions'])
        runs = self._runs(repository, p)
        _, counts = _candidate_lifecycle(candidates, runs, inputs['materialized'])
        ontologies = repository.list_ontologies(p)
        latest = next(iter(runs), None)
        return {**_summary(candidates),
            'quality_warnings': _quality_warnings(candidates),
            'relation_constraint_policy': 'open_no_domain_range',
            'published': bool(ontologies),
            'ontology_id': ontologies[-1]['id'] if ontologies else None,
            'candidate_status_counts': counts,
            'latest_run': ({'id': latest['id'], 'status': latest['status'],
                'result_kind': discovery_result_kind(latest),
                'unified_draft_id': latest.get('unified_draft_id'),
                'diagnostics': latest.get('diagnostics') or {},
                'created_at': latest.get('created_at'),
                'updated_at': latest.get('updated_at')} if latest else None),
            'unpublished_candidate_count': counts['pending'] + counts['included_in_draft'],
            'requires_candidate_review': bool(counts['pending'] + counts['included_in_draft'] + counts['approved'])}

    def list_runs(self, p):
        with self.service.repository.independent_read_view() as repository:
            repository.get_project(p)
            items = self._runs(repository, p)
            return {'items': items, 'total': len(items)}

    def get_run(self, p, run_id):
        with self.service.repository.independent_read_view() as repository:
            repository.get_project(p)
            run = repository.get_discovery_run(p, run_id)
            if not run.get('intake_request_hash'):
                raise KeyError(run_id)
            return run

    def audit(self, p):
        with self.service.repository.independent_read_view() as repository:
            repository.get_project(p)
            versions = repository.list_ontologies(p)
            return audit_formal_vocabulary(versions[-1]['turtle'] if versions else '')

    def candidate_mindmap(self, p, limit=500):
        with self.service.repository.independent_read_view() as repository:
            inputs = PageReads(repository).discovery_inputs(p)
            candidates = _candidates(inputs['documents'], inputs['assertions'])
            runs = self._runs(repository, p)
            states, _ = _candidate_lifecycle(candidates, runs, inputs['materialized'])
            return _candidate_mindmap(candidates, states, limit)
