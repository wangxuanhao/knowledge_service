"""Governed ontology draft lifecycle and read-side graph projections.

This module is deliberately the domain seam above ``OntologyDraftStore``.  It
owns state transitions, server-side operation compilation, validation snapshots,
review policy and publication preflight.  The final atomic publication callback
is supplied by the repository layer (Task 5); this service never assembles a
partial publication transaction itself.
"""

from __future__ import annotations

import base64
from collections import deque
import hashlib
import json
from uuid import uuid4

from rdflib import Literal, RDF, RDFS, URIRef
from rdflib.namespace import OWL

from ..repository.ontology_draft_store import OntologyDraftConflict
from .ontology import Ontology, local_name, term_impact
from .ontology_operations import (
    apply_operations,
    build_operation,
    build_restore_operation,
    canonical_turtle_diff,
    is_batch_eligible,
    operation_fingerprint,
    validate_ontology_invariants,
)


VALIDATION_RULE_VERSION = 'ontology-drafts/1'
FINAL_STATES = frozenset({'published', 'closed'})
EDITABLE_STATES = frozenset({'editing'})
REVIEW_STATES = frozenset({'submitted', 'reviewed'})


class OntologyDraftError(ValueError):
    """Stable domain error intended for later HTTP error mapping."""

    code = 'ontology_draft_error'

    def __init__(self, message, *, details=None):
        self.details = dict(details or {})
        super().__init__(message)


class RevisionConflict(OntologyDraftError):
    code = 'revision_conflict'


class StaleBase(OntologyDraftError):
    code = 'stale_base'


class StaleSource(OntologyDraftError):
    code = 'stale_source'


class ValidationChanged(OntologyDraftError):
    code = 'validation_changed'


class ValidationFailed(OntologyDraftError):
    code = 'validation_failed'


class BatchNotAllowed(OntologyDraftError):
    code = 'batch_not_allowed'


def _canonical_json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(',', ':'))


def _fingerprint(value) -> str:
    return hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()


def _semantic_operation(operation):
    """Remove append-only ledger columns before invoking the compiler seam."""
    return {key: value for key, value in operation.items()
            if key not in {'id', 'project_id', 'draft_id', 'created_at',
                           'supersedes_operation_id'}}


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a non-empty string')
    return value.strip()


def _as_list(value, field):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f'{field} must be a list')
    return list(value)


class OntologyDrafts:
    """Deep interface for draft editing, review, validation and graph reads."""

    def __init__(self, repository, *, publisher=None,
                 validation_rule_version=VALIDATION_RULE_VERSION):
        self.repository = repository
        self.store = repository._ontology_drafts
        self.publisher = publisher
        self.validation_rule_version = _text(
            validation_rule_version, 'validation_rule_version')

    # ------------------------------------------------------------------ basics
    def _latest_id(self, project_id):
        versions = self.repository.list_ontologies(project_id)
        return versions[-1]['id'] if versions else None

    def _base_turtle(self, project_id, base_ontology_id):
        if base_ontology_id is None:
            return ''
        return self.repository.get_ontology(project_id, base_ontology_id)['turtle']

    def _translate_conflict(self, exc):
        return RevisionConflict(str(exc), details={
            'draft_id': exc.draft_id,
            'expected_revision': exc.expected_revision,
        })

    def _cas(self, project_id, draft_id, expected_revision, changes):
        try:
            return self.store.compare_and_set(
                project_id, draft_id, expected_revision, changes)
        except OntologyDraftConflict as exc:
            raise self._translate_conflict(exc) from exc

    def _history(self, project_id, draft_id):
        history = self.store.export(project_id)
        operations = [row for row in history['operations']
                      if row['draft_id'] == draft_id]
        decisions = [row for row in history['decisions']
                     if row['draft_id'] == draft_id]
        return operations, decisions

    @staticmethod
    def _effective_decisions(all_decisions):
        superseded = {
            row['supersedes_decision_id'] for row in all_decisions
            if row.get('supersedes_decision_id')}
        latest = [row for row in all_decisions if row['id'] not in superseded]
        # A later decision for the same operation is authoritative even when an
        # imported legacy history omitted its explicit supersession link.
        by_operation = {}
        for row in latest:
            by_operation[row['operation_id']] = row
        return list(by_operation.values())

    def _active_operations(self, project_id, draft_id):
        return [row for row in self.store.effective_operations(project_id, draft_id)
                if (row.get('validation') or {}).get('rebase_status') != 'no-op']

    def _ontology_for_draft(self, project_id, draft):
        operations = self._active_operations(project_id, draft['id'])
        applicable = [row for row in operations
                      if (row.get('validation') or {}).get('rebase_status') != 'conflict']
        turtle = self._base_turtle(project_id, draft['base_ontology_id'])
        if applicable:
            turtle = apply_operations(turtle, map(_semantic_operation, applicable))
        return turtle, operations

    def _preview(self, project_id, draft):
        operations, all_decisions = self._history(project_id, draft['id'])
        effective_ids = {
            row['id'] for row in self.store.effective_operations(project_id, draft['id'])}
        effective = [row for row in operations if row['id'] in effective_ids]
        decisions = [row for row in self._effective_decisions(all_decisions)
                     if row['operation_id'] in effective_ids
                     and row['operation_fingerprint'] == next(
                         (op['fingerprint'] for op in effective
                          if op['id'] == row['operation_id']), None)]
        try:
            turtle, _ = self._ontology_for_draft(project_id, draft)
        except ValueError:
            turtle = self._base_turtle(project_id, draft['base_ontology_id'])
        return {
            **draft,
            'draft': draft,
            'operations': effective,
            'decisions': decisions,
            'turtle': turtle,
            'ontology': Ontology(turtle).summary() if turtle.strip() else {
                'classes': [], 'relations': [], 'attributes': [], 'triples': 0},
        }

    # ----------------------------------------------------------- source checks
    @staticmethod
    def _source_references(context):
        references = []
        documents = context.get('documents') if isinstance(context, dict) else None
        if isinstance(documents, list):
            references.extend(item for item in documents if isinstance(item, dict))
        if isinstance(context, dict) and context.get('document_id'):
            references.append(context)
        return references

    @staticmethod
    def _candidate(document, candidate_id):
        metadata = document.get('metadata') or {}
        candidates = [*(metadata.get('review_candidates') or []),
                      *(metadata.get('discovery_candidates') or [])]
        return next((item for item in candidates
                     if item.get('id') == candidate_id), None)

    def _source_snapshot(self, project_id, draft, *, require_current=True):
        context = draft.get('source_context') or {}
        snapshots = []
        for reference in self._source_references(context):
            document_id = reference.get('document_id') or reference.get('id')
            try:
                document = self.repository.get_record(project_id, document_id)
            except KeyError as exc:
                if require_current:
                    raise StaleSource(
                        f'source document {document_id!r} no longer exists') from exc
                continue
            expected_version = reference.get(
                'expected_document_version', reference.get('version'))
            expected_version_id = reference.get(
                'expected_document_version_id', reference.get('version_id'))
            if require_current and expected_version is not None and (
                    document.get('version') != expected_version):
                raise StaleSource(
                    f'source document {document_id!r} revision changed', details={
                        'document_id': document_id,
                        'expected_version': expected_version,
                        'current_version': document.get('version'),
                    })
            if require_current and expected_version_id is not None and (
                    document.get('version_id') != expected_version_id):
                raise StaleSource(
                    f'source document {document_id!r} version changed')
            candidate_ids = reference.get('candidate_ids') or []
            if reference.get('candidate_id'):
                candidate_ids = [*candidate_ids, reference['candidate_id']]
            candidates = []
            for candidate_id in candidate_ids:
                candidate = self._candidate(document, candidate_id)
                if candidate is None:
                    if require_current:
                        raise StaleSource(
                            f'source candidate {candidate_id!r} no longer exists')
                    continue
                expected_status = reference.get('expected_candidate_status')
                statuses = reference.get('candidate_statuses') or {}
                expected_status = statuses.get(candidate_id, expected_status)
                if (require_current and expected_status is not None
                        and candidate.get('status') != expected_status):
                    raise StaleSource(
                        f'source candidate {candidate_id!r} status changed')
                expected_fingerprints = reference.get('candidate_fingerprints') or {}
                actual_fingerprint = _fingerprint(candidate)
                if (require_current and expected_fingerprints.get(candidate_id)
                        and expected_fingerprints[candidate_id] != actual_fingerprint):
                    raise StaleSource(
                        f'source candidate {candidate_id!r} changed')
                candidates.append({
                    'id': candidate_id, 'status': candidate.get('status'),
                    'fingerprint': actual_fingerprint})
            snapshots.append({
                'document_id': document_id,
                'version': document.get('version'),
                'version_id': document.get('version_id'),
                'candidates': candidates,
            })
        return snapshots

    def _mark_stale(self, project_id, draft, expected_revision, status, error):
        if draft['status'] != status:
            draft = self._cas(project_id, draft['id'], expected_revision,
                              {'status': status})
        error.details.setdefault('draft', draft)
        raise error

    def _check_current(self, project_id, draft, expected_revision, *,
                       expected_ontology_id=None, check_source=True):
        latest = self._latest_id(project_id)
        expected = draft['base_ontology_id']
        if expected != latest:
            self._mark_stale(
                project_id, draft, expected_revision, 'stale_base',
                StaleBase('draft base is not the current project ontology',
                          details={'base_ontology_id': expected,
                                   'current_ontology_id': latest}))
        if expected_ontology_id is not None and expected_ontology_id != latest:
            # A stale client precondition does not make a current draft stale.
            # Only persist stale_base when the draft's own immutable base fell
            # behind the project ontology.
            raise StaleBase(
                'expected ontology is not the current project ontology', details={
                    'expected_ontology_id': expected_ontology_id,
                    'current_ontology_id': latest,
                    'draft': draft,
                })
        if check_source and draft['source_kind'] in {'discovery', 'candidate'}:
            try:
                self._source_snapshot(project_id, draft)
            except StaleSource as exc:
                self._mark_stale(
                    project_id, draft, expected_revision, 'stale_source', exc)
        return latest

    # --------------------------------------------------------------- lifecycle
    def create(self, project_id, base_ontology_id_or_none=None, source='manual',
               title=None, actor=None, *, source_context=None, summary='',
               base_ontology_id=None):
        if base_ontology_id is not None:
            if (base_ontology_id_or_none is not None
                    and base_ontology_id_or_none != base_ontology_id):
                raise ValueError('base ontology was provided twice')
            base_ontology_id_or_none = base_ontology_id
        if isinstance(source, dict):
            source_data = dict(source)
            source_kind = source_data.pop('kind', source_data.pop('source_kind', None))
            source_context = {**source_data, **(source_context or {})}
        else:
            source_kind = source
        title = _text(title, 'title')
        actor = _text(actor, 'actor')
        latest = self._latest_id(project_id)
        if base_ontology_id_or_none != latest:
            raise StaleBase(
                'new draft must use the current ontology as its base', details={
                    'base_ontology_id': base_ontology_id_or_none,
                    'current_ontology_id': latest})
        context = dict(source_context or {})
        context.setdefault('actor', actor)
        if source_kind in {'discovery', 'candidate'}:
            # Honour an explicit caller snapshot as an optimistic source
            # precondition before filling any omitted snapshot fields.
            self._source_snapshot(
                project_id, {'source_context': context}, require_current=True)
            context = self._refresh_source_context(project_id, context)
        draft = self.store.create(project_id, {
            'id': str(uuid4()), 'base_ontology_id': base_ontology_id_or_none,
            'source_kind': source_kind, 'status': 'editing', 'revision': 1,
            'title': title, 'summary': summary, 'source_context': context,
        })
        if source_kind in {'discovery', 'candidate'}:
            try:
                self._source_snapshot(project_id, draft)
            except StaleSource:
                # Creation is atomic from the caller's perspective.
                with self.repository._transaction():
                    with self.repository._allow_ontology_history_delete():
                        self.repository._db.execute(
                            'DELETE FROM ontology_drafts WHERE project_id=? AND id=?',
                            (project_id, draft['id']))
                raise
        return draft

    @staticmethod
    def _operation_args(command):
        action = command.get('action')
        before = command.get('before')
        after = command.get('after')
        if action == 'create_term':
            after = after if after is not None else {'kind': command.get('kind')}
        elif action in {'add_parent', 'remove_parent'}:
            value = command.get('parent_iri', command.get('value'))
            if action.startswith('add_'):
                after = after if after is not None else {'value': value}
            else:
                before = before if before is not None else {'value': value}
        elif action in {'add_domain', 'remove_domain', 'add_range', 'remove_range'}:
            value = command.get('value') or command.get(action.split('_', 1)[1] + '_iri')
            if action.startswith('add_'):
                after = after if after is not None else {'value': value}
            else:
                before = before if before is not None else {'value': value}
        elif action in {'add_annotation', 'remove_annotation'}:
            spec = {
                'predicate': command.get('predicate'), 'value': command.get('value'),
                'language': command.get('language'), 'datatype': command.get('datatype'),
            }
            if command.get('value_type'):
                spec['type'] = command['value_type']
            spec = {key: value for key, value in spec.items() if value is not None}
            if action == 'add_annotation':
                after = after if after is not None else spec
            else:
                before = before if before is not None else spec
        elif action == 'set_datatype':
            after = after if after is not None else {
                'datatype': command.get('datatype')}
        return action, before, after

    def _compile_command(self, project_id, draft, command, ontology):
        if not isinstance(command, dict):
            raise ValueError('command must be an object')
        action = command.get('action')
        if action in {'turtle', 'replace_turtle', 'diff_turtle'}:
            edited = command.get('edited_turtle', command.get('turtle'))
            if not isinstance(edited, str):
                raise ValueError('Turtle command requires edited_turtle')
            return canonical_turtle_diff(
                ontology.graph.serialize(format='turtle'), edited,
                published=draft['base_ontology_id'] is not None,
                base_ontology_id=draft['base_ontology_id'],
                source_ontology_id=command.get('source_ontology_id'),
                restore_operation_builder=lambda target_iri, source_ontology_id: (
                    build_restore_operation(
                        self.repository, project_id, target_iri,
                        source_ontology_id,
                        selected_fields=command.get('selected_fields') or [])))
        target = command.get('target_iri')
        if action == 'restore_term':
            selected = command.get('selected_fields', command.get('selection'))
            operation = build_restore_operation(
                self.repository, project_id, target,
                command.get('source_ontology_id'),
                selected_fields=selected or [])
            operation['reason'] = command.get('reason')
            operation['fingerprint'] = operation_fingerprint(operation)
            return [operation]
        action, before, after = self._operation_args(command)
        # Risk, warnings and fingerprints are always recomputed here.  Client
        # supplied values with those names are intentionally ignored.
        return [build_operation(
            action, target, before=before, after=after,
            source=draft['source_kind'],
            impact=self._impact(project_id, target, ontology=ontology),
            confidence=command.get('confidence'),
            evidence=command.get('evidence_refs', command.get('evidence', [])),
            reason=command.get('reason'), ontology=ontology)]

    def _impact(self, project_id, target_iri, *, ontology=None):
        ontology = ontology or Ontology(
            self._base_turtle(project_id, self._latest_id(project_id)))
        target = URIRef(target_iri)
        descendants = set()
        pending_nodes = [target]
        while pending_nodes:
            parent = pending_nodes.pop()
            for child in ontology.graph.subjects(RDFS.subClassOf, parent):
                if child not in descendants and ontology.is_active_term(child):
                    descendants.add(child)
                    pending_nodes.append(child)
        references = term_impact(self, project_id, target_iri, ontology)
        constraints = references['constraint_count']
        formal_records = references['record_count']
        pending_ids = set()
        target_names = {target_iri, local_name(target_iri)}
        for row in self.repository.current_records(project_id, vectors='none'):
            metadata = row.get('metadata') or {}
            candidates = [*(metadata.get('review_candidates') or []),
                          *(metadata.get('discovery_candidates') or [])]
            for candidate in candidates:
                referenced_names = {
                    candidate.get(key)
                    for key in ('target_type', 'proposed_type', 'predicate')}
                if (candidate.get('status') == 'pending'
                        and target_names & referenced_names):
                    pending_ids.add(candidate.get('id') or _fingerprint(candidate))
        pending = len(pending_ids)
        referenced = bool(descendants or constraints or formal_records or pending)
        return {
            'formal_records': formal_records,
            'pending': pending,
            'descendants': len(descendants),
            'constraints': constraints,
            'leaf': not descendants,
            'referenced': referenced,
        }

    def command(self, project_id, draft_id, expected_revision, command):
        draft = self.store.get(project_id, draft_id)
        if draft['status'] not in EDITABLE_STATES:
            raise ValueError('commands are only allowed while a draft is editing')
        self._check_current(project_id, draft, expected_revision)
        base_turtle, effective = self._ontology_for_draft(project_id, draft)
        ontology = Ontology(base_turtle)
        compiled = self._compile_command(project_id, draft, command, ontology)
        supersedes = command.get(
            'supersedes_operation_id', command.get('operation_id'))
        if supersedes:
            current = {row['id']: row for row in effective}
            if supersedes not in current:
                raise ValueError('superseded operation is not current')
            if len(compiled) != 1:
                raise ValueError('one adjustment may supersede exactly one operation')
            compiled[0]['supersedes_operation_id'] = supersedes
        # Prove the proposed overlay is valid before persisting any audit row.
        candidate_operations = [row for row in effective
                                if row['id'] != supersedes] + compiled
        apply_operations(
            self._base_turtle(project_id, draft['base_ontology_id']),
            map(_semantic_operation, candidate_operations))
        with self.repository._transaction():
            self.store.append_operations(project_id, draft_id, compiled)
            updated = self._cas(project_id, draft_id, expected_revision, {
                'validation_report': None, 'validation_fingerprint': None})
        return self._preview(project_id, updated)

    # -------------------------------------------------------------- validation
    @staticmethod
    def _operation_issues(operations):
        errors, warnings, info = [], [], []
        for operation in operations:
            validation = operation.get('validation') or {}
            for issue in validation.get('errors') or []:
                errors.append({**issue, 'operation_ids': [operation['id']]})
            for issue in validation.get('warnings') or []:
                warnings.append({**issue, 'operation_ids': [operation['id']]})
            for issue in validation.get('info') or []:
                info.append({**issue, 'operation_ids': [operation['id']]})
            if validation.get('rebase_status') == 'conflict':
                errors.append({
                    'code': 'rebase_conflict', 'severity': 'error',
                    'message': validation.get('rebase_message',
                                              'operation conflicts with new base'),
                    'operation_ids': [operation['id']],
                    'term_iris': [operation['target_iri']],
                })
        return errors, warnings, info

    def _compute_validation(self, project_id, draft):
        operations = self._active_operations(project_id, draft['id'])
        errors, warnings, info = self._operation_issues(operations)
        candidate_turtle = self._base_turtle(project_id, draft['base_ontology_id'])
        graph_report = {'conforms': True, 'errors': []}
        try:
            candidate_turtle = apply_operations(candidate_turtle, [
                _semantic_operation(row) for row in operations
                if (row.get('validation') or {}).get('rebase_status') != 'conflict'])
            graph_report = validate_ontology_invariants(Ontology(candidate_turtle))
            errors.extend(graph_report.get('errors') or [])
        except ValueError as exc:
            issue = {'code': 'graph_integrity', 'severity': 'error',
                     'message': str(exc), 'operation_ids': [], 'term_iris': []}
            graph_report = {'conforms': False, 'errors': [issue]}
            errors.append(issue)
        historical = {'checked_records': 0, 'nonconforming_records': 0,
                      'errors': []}
        prospective = {'conforms': not errors, 'errors': list(errors)}
        if not errors:
            records = self.repository.current_records(project_id, vectors='none')
            formal = [row for row in records
                      if row.get('kind') in {'entity', 'relation', 'attribute'}]
            historical['checked_records'] = len(formal)
            if formal:
                historical_report = Ontology(candidate_turtle).validate_timeline(formal)
                if not historical_report.get('conforms'):
                    historical['nonconforming_records'] = len(
                        historical_report.get('errors') or [])
                    historical['errors'] = historical_report.get('errors') or []
                    info.append({
                        'code': 'historical_impact', 'severity': 'info',
                        'message': 'historical records do not satisfy the prospective ontology',
                        'operation_ids': [], 'term_iris': [],
                    })
        report = {
            'rule_version': self.validation_rule_version,
            'graph_integrity': graph_report,
            'prospective_new_write_contract': prospective,
            'historical_impact': historical,
            'errors': errors, 'warnings': warnings, 'info': info,
            'conforms': not errors,
        }
        source_snapshot = self._source_snapshot(project_id, draft)
        fingerprint = _fingerprint({
            'rule_version': self.validation_rule_version,
            'base_ontology_id': draft['base_ontology_id'],
            'source_versions': source_snapshot,
            'operation_fingerprints': [row['fingerprint'] for row in operations],
            'report': report,
        })
        return report, fingerprint, candidate_turtle

    def validate(self, project_id, draft_id, expected_revision):
        draft = self.store.get(project_id, draft_id)
        if draft['status'] in FINAL_STATES:
            raise ValueError('final drafts cannot be validated')
        self._check_current(project_id, draft, expected_revision)
        report, fingerprint, _ = self._compute_validation(project_id, draft)
        updated = self._cas(project_id, draft_id, expected_revision, {
            'validation_report': report,
            'validation_fingerprint': fingerprint,
        })
        return self._preview(project_id, updated)

    def submit(self, project_id, draft_id, expected_revision):
        draft = self.store.get(project_id, draft_id)
        if draft['status'] != 'editing':
            raise ValueError('only editing drafts may be submitted')
        self._check_current(project_id, draft, expected_revision)
        operations = self._active_operations(project_id, draft_id)
        active = [row for row in operations
                  if (row.get('validation') or {}).get('rebase_status') != 'no-op']
        if not active:
            raise ValidationFailed('a draft must contain at least one effective operation')
        report, fingerprint, _ = self._compute_validation(project_id, draft)
        if not report['conforms']:
            raise ValidationFailed('draft validation failed', details={'report': report})
        current_ids = {row['id'] for row in active}
        _, decision_rows = self._history(project_id, draft_id)
        retained = {
            row['operation_id']: row
            for row in self._effective_decisions(decision_rows)
            if row['operation_id'] in current_ids
            and row['action'] in {'approve', 'reject'}
            and row['operation_fingerprint'] == next(
                (operation['fingerprint'] for operation in active
                 if operation['id'] == row['operation_id']), None)}
        status = 'submitted'
        if all(operation_id in retained for operation_id in current_ids):
            status = ('closed' if all(
                retained[operation_id]['action'] == 'reject'
                for operation_id in current_ids) else 'reviewed')
        updated = self._cas(project_id, draft_id, expected_revision, {
            'status': status, 'validation_report': report,
            'validation_fingerprint': fingerprint,
        })
        return self._preview(project_id, updated)

    # ---------------------------------------------------------------- decisions
    @staticmethod
    def _warning_codes(report):
        return {row.get('code') for row in (report.get('warnings') or [])
                if row.get('code')}

    def _require_validation(self, project_id, draft, supplied_fingerprint,
                            acknowledged_warning_codes):
        report, current, turtle = self._compute_validation(project_id, draft)
        if not report['conforms']:
            raise ValidationFailed('draft validation failed', details={'report': report})
        if (not supplied_fingerprint
                or supplied_fingerprint != current
                or draft.get('validation_fingerprint') != current):
            raise ValidationChanged(
                'validation snapshot changed; validate again', details={
                    'validation_fingerprint': current, 'report': report})
        acknowledged = set(acknowledged_warning_codes or [])
        missing = self._warning_codes(report) - acknowledged
        if missing:
            raise ValidationFailed(
                'unacknowledged warnings: ' + ', '.join(sorted(missing)),
                details={'warning_codes': sorted(missing), 'report': report})
        return report, current, turtle

    def decide(self, project_id, draft_id, expected_revision,
               expected_ontology_id, validation_fingerprint, decisions,
               acknowledged_warning_codes, actor):
        draft = self.store.get(project_id, draft_id)
        if draft['status'] not in REVIEW_STATES:
            raise ValueError('decisions require a submitted or reviewed draft')
        self._check_current(
            project_id, draft, expected_revision,
            expected_ontology_id=expected_ontology_id)
        actor = _text(actor, 'actor')
        proposed = _as_list(decisions, 'decisions')
        if not proposed or len(proposed) > 100:
            raise BatchNotAllowed('a decision request must contain 1..100 decisions')
        report, _, turtle = self._require_validation(
            project_id, draft, validation_fingerprint,
            acknowledged_warning_codes)
        operations = {row['id']: row for row in self._active_operations(
            project_id, draft_id)}
        _, previous_rows = self._history(project_id, draft_id)
        previous = {row['operation_id']: row
                    for row in self._effective_decisions(previous_rows)}
        warning_codes = self._warning_codes(report)
        if len(proposed) > 1:
            eligible = all(
                item.get('action') == 'approve'
                and item.get('operation_id') in operations
                and is_batch_eligible(
                    operations[item['operation_id']], ontology=turtle)
                and not warning_codes
                for item in proposed)
            if not eligible:
                raise BatchNotAllowed(
                    'batch decisions allow only no-warning low-risk approvals')
        saved = []
        for item in proposed:
            if not isinstance(item, dict):
                raise ValueError('each decision must be an object')
            operation = operations.get(item.get('operation_id'))
            if operation is None:
                raise ValueError('decision operation is not current')
            if item.get('operation_fingerprint') != operation['fingerprint']:
                raise ValidationChanged('decision operation fingerprint changed')
            action = item.get('action')
            if action not in {'approve', 'reject', 'request_changes'}:
                raise ValueError('unsupported decision action')
            reason = item.get('reason')
            if action in {'reject', 'request_changes'}:
                reason = _text(reason, 'reason')
            if action == 'approve' and operation['risk'] == 'high':
                reason = _text(reason, 'reason')
            if action == 'approve' and warning_codes:
                reason = _text(reason, 'reason')
            saved.append({
                'id': item.get('id') or str(uuid4()),
                'operation_id': operation['id'],
                'operation_fingerprint': operation['fingerprint'],
                'action': action, 'reason': reason, 'actor': actor,
                'supersedes_decision_id': (
                    previous.get(operation['id']) or {}).get('id'),
            })
        with self.repository._transaction():
            self.store.append_decisions(project_id, draft_id, saved)
            combined = {**previous}
            combined.update({row['operation_id']: row for row in saved})
            if any(row['action'] == 'request_changes' for row in saved):
                status = 'editing'
            elif all(op_id in combined for op_id in operations):
                status = ('closed' if all(
                    combined[op_id]['action'] == 'reject' for op_id in operations)
                          else 'reviewed')
            else:
                status = 'submitted'
            updated = self._cas(project_id, draft_id, expected_revision, {
                'status': status,
                **({'validation_report': None, 'validation_fingerprint': None}
                   if status == 'editing' else {}),
            })
        return self._preview(project_id, updated)

    def close(self, project_id, draft_id, expected_revision, actor, reason):
        draft = self.store.get(project_id, draft_id)
        if draft['status'] in FINAL_STATES:
            raise ValueError('draft is already final')
        actor, reason = _text(actor, 'actor'), _text(reason, 'reason')
        context = dict(draft.get('source_context') or {})
        context['closure'] = {'actor': actor, 'reason': reason}
        return self._cas(project_id, draft_id, expected_revision, {
            'status': 'closed', 'source_context': context})

    # ------------------------------------------------------------------ rebase
    @staticmethod
    def _operation_is_noop(ontology, operation):
        graph = ontology.graph
        target = URIRef(operation['target_iri'])
        action = operation['action']
        before, after = operation.get('before') or {}, operation.get('after') or {}
        if action == 'create_term':
            declarations = {
                'class': {OWL.Class, RDFS.Class},
                'relation': {OWL.ObjectProperty},
                'attribute': {OWL.DatatypeProperty},
            }.get(after.get('kind'), set())
            return any((target, RDF.type, kind) in graph for kind in declarations)
        predicates = {
            'parent': RDFS.subClassOf, 'domain': RDFS.domain, 'range': RDFS.range}
        for suffix, predicate in predicates.items():
            if action == f'add_{suffix}':
                return (target, predicate, URIRef(after.get('value', ''))) in graph
            if action == f'remove_{suffix}':
                return (target, predicate, URIRef(before.get('value', ''))) not in graph
        if action == 'add_annotation':
            value = after.get('value')
            node = URIRef(value) if after.get('type') == 'iri' else Literal(
                value, lang=after.get('language'),
                datatype=URIRef(after['datatype']) if after.get('datatype') else None)
            return (target, URIRef(after.get('predicate', '')), node) in graph
        if action == 'remove_annotation':
            value = before.get('value')
            node = URIRef(value) if before.get('type') == 'iri' else Literal(
                value, lang=before.get('language'),
                datatype=URIRef(before['datatype']) if before.get('datatype') else None)
            return (target, URIRef(before.get('predicate', '')), node) not in graph
        if action == 'retire_term':
            return target in (ontology.classes | ontology.relations | ontology.attributes) \
                and not ontology.is_active_term(target)
        if action == 'restore_term':
            return ontology.is_active_term(target)
        if action == 'set_datatype':
            values = {str(value) for value in graph.objects(target, RDFS.range)}
            requested = after.get('datatype')
            return values == ({requested} if requested else set())
        return False

    def rebase(self, project_id, draft_id, expected_revision,
               expected_ontology_id):
        draft = self.store.get(project_id, draft_id)
        if draft['status'] in FINAL_STATES:
            raise ValueError('final drafts cannot be rebased')
        latest = self._latest_id(project_id)
        if expected_ontology_id != latest:
            raise StaleBase('rebase target must be the latest ontology', details={
                'expected_ontology_id': expected_ontology_id,
                'current_ontology_id': latest})
        old_operations = self._active_operations(project_id, draft_id)
        turtle = self._base_turtle(project_id, latest)
        replacements = []
        classifications = []
        source_snapshot_fingerprint = None
        if draft['source_kind'] in {'discovery', 'candidate'}:
            refreshed_context = self._refresh_source_context(
                project_id, draft.get('source_context') or {})
            source_snapshot_fingerprint = _fingerprint(refreshed_context)
        for operation in old_operations:
            ontology = Ontology(turtle)
            if self._operation_is_noop(ontology, operation):
                status, message = 'no-op', 'operation is already true on latest base'
                rebuilt = None
            else:
                try:
                    old_validation = operation.get('validation') or {}
                    carried_validation = {
                        key: value for key, value in old_validation.items()
                        if key not in {'warnings', 'source', 'confidence',
                                       'rebase_status', 'rebase_message'}}
                    if source_snapshot_fingerprint is not None:
                        carried_validation['source_snapshot_fingerprint'] = (
                            source_snapshot_fingerprint)
                    impact = {**(operation.get('impact') or {}),
                              **self._impact(project_id, operation['target_iri'])}
                    rebuilt = build_operation(
                        operation['action'], operation['target_iri'],
                        before=operation.get('before'), after=operation.get('after'),
                        source=old_validation.get('source', draft['source_kind']),
                        impact=impact,
                        confidence=old_validation.get('confidence'),
                        evidence=operation.get('evidence') or [],
                        validation=carried_validation,
                        reason=operation.get('reason'), ontology=ontology)
                    turtle = apply_operations(turtle, [rebuilt])
                    status, message = 'clean', None
                except ValueError as exc:
                    status, message = 'conflict', str(exc)
                    rebuilt = None
            classifications.append({'operation_id': operation['id'],
                                    'classification': status,
                                    'message': message})
            if (status == 'clean' and rebuilt is not None
                    and rebuilt['fingerprint'] == operation['fingerprint']):
                # An unchanged semantic operation remains byte-for-byte stable,
                # preserving its decisions as required.
                continue
            if status == 'clean':
                replacement = rebuilt
            else:
                validation = {**(operation.get('validation') or {}),
                              'rebase_status': status}
                if message:
                    validation['rebase_message'] = message
                replacement = {
                    key: operation.get(key) for key in (
                        'action', 'target_iri', 'before', 'after', 'evidence',
                        'impact', 'risk', 'reason')}
                replacement['validation'] = validation
                replacement['fingerprint'] = operation_fingerprint(
                    _semantic_operation(replacement))
            replacement.update({
                'id': str(uuid4()),
                'supersedes_operation_id': operation['id']})
            replacements.append(replacement)
        context = dict(draft.get('source_context') or {})
        if draft['source_kind'] in {'discovery', 'candidate'}:
            context = refreshed_context
        with self.repository._transaction():
            if replacements:
                self.store.append_operations(project_id, draft_id, replacements)
            updated = self._cas(project_id, draft_id, expected_revision, {
                'base_ontology_id': latest, 'status': 'editing',
                'source_context': context,
                'validation_report': None, 'validation_fingerprint': None,
            })
        result = self._preview(project_id, updated)
        result['rebase'] = classifications
        return result

    def _refresh_source_context(self, project_id, context):
        refreshed = json.loads(_canonical_json(context))
        refs = refreshed.get('documents')
        targets = refs if isinstance(refs, list) else [refreshed]
        for reference in targets:
            if not isinstance(reference, dict):
                continue
            document_id = reference.get('document_id') or reference.get('id')
            if not document_id:
                continue
            try:
                document = self.repository.get_record(project_id, document_id)
            except KeyError as exc:
                raise StaleSource(
                    f'source document {document_id!r} no longer exists') from exc
            reference['expected_document_version'] = document.get('version')
            reference['expected_document_version_id'] = document.get('version_id')
            candidate_ids = reference.get('candidate_ids') or []
            if reference.get('candidate_id'):
                candidate_ids = [*candidate_ids, reference['candidate_id']]
            candidates = {}
            for candidate_id in dict.fromkeys(candidate_ids):
                candidate = self._candidate(document, candidate_id)
                if candidate is None:
                    raise StaleSource(
                        f'source candidate {candidate_id!r} no longer exists')
                candidates[candidate_id] = candidate
            reference['candidate_fingerprints'] = {
                candidate_id: _fingerprint(candidate)
                for candidate_id, candidate in candidates.items()}
            reference['candidate_statuses'] = {
                candidate_id: candidate.get('status')
                for candidate_id, candidate in candidates.items()}
        return refreshed

    # --------------------------------------------------------------- publishing
    def publish_preflight(self, project_id, draft_id, expected_revision,
                          expected_ontology_id, validation_fingerprint,
                          acknowledged_warning_codes, idempotency_key, actor):
        draft = self.store.get(project_id, draft_id)
        if draft['status'] != 'reviewed':
            raise ValueError('only reviewed drafts may be published')
        self._check_current(
            project_id, draft, expected_revision,
            expected_ontology_id=expected_ontology_id)
        actor = _text(actor, 'actor')
        idempotency_key = _text(idempotency_key, 'idempotency_key')
        report, fingerprint, _ = self._require_validation(
            project_id, draft, validation_fingerprint,
            acknowledged_warning_codes)
        operations = self._active_operations(project_id, draft_id)
        _, decision_rows = self._history(project_id, draft_id)
        decisions = {row['operation_id']: row
                     for row in self._effective_decisions(decision_rows)}
        approved = []
        for operation in operations:
            decision = decisions.get(operation['id'])
            if (decision is not None and decision['action'] == 'approve'
                    and decision['operation_fingerprint'] == operation['fingerprint']):
                approved.append(operation)
        if not approved:
            raise ValidationFailed('publish requires at least one current approval')
        try:
            approved_turtle = apply_operations(
                self._base_turtle(project_id, draft['base_ontology_id']),
                map(_semantic_operation, approved))
        except ValueError as exc:
            raise ValidationFailed(
                'approved operation subset failed validation',
                details={'reason': str(exc)}) from exc
        request = {
            'project_id': project_id, 'draft': draft,
            'draft_id': draft_id, 'expected_revision': expected_revision,
            'expected_ontology_id': expected_ontology_id,
            'validation_fingerprint': fingerprint,
            'validation_report': report, 'operations': approved,
            'decisions': [decisions[row['id']] for row in approved],
            'turtle': approved_turtle,
            'summary': Ontology(approved_turtle).summary(),
            'acknowledged_warning_codes': sorted(
                set(acknowledged_warning_codes or [])),
            'idempotency_key': idempotency_key, 'actor': actor,
        }
        request['request_hash'] = _fingerprint({
            key: value for key, value in request.items()
            if key not in {'draft', 'validation_report', 'summary'}})
        return request

    def publish(self, project_id, draft_id, expected_revision,
                expected_ontology_id, validation_fingerprint,
                acknowledged_warning_codes, idempotency_key, actor):
        prepared = self.publish_preflight(
            project_id, draft_id, expected_revision, expected_ontology_id,
            validation_fingerprint, acknowledged_warning_codes,
            idempotency_key, actor)
        if self.publisher is None:
            raise RuntimeError(
                'atomic ontology publication delegate is not configured')
        if callable(self.publisher):
            return self.publisher(**prepared)
        publish = getattr(self.publisher, 'publish_ontology_draft', None)
        if publish is None:
            raise TypeError('publisher must be callable or expose publish_ontology_draft')
        return publish(**prepared)

    # ------------------------------------------------------------ read models
    def _read_ontology(self, project_id, ontology_id=None, draft_id=None):
        if ontology_id is None:
            ontology_id = self._latest_id(project_id)
        turtle = self._base_turtle(project_id, ontology_id)
        if draft_id is not None:
            draft = self.store.get(project_id, draft_id)
            if ontology_id != draft['base_ontology_id']:
                raise ValueError('draft overlay must use its own base ontology')
            turtle, _ = self._ontology_for_draft(project_id, draft)
        return Ontology(turtle), ontology_id

    @staticmethod
    def _decode_cursor(cursor):
        if cursor in (None, ''):
            return 0
        try:
            return int(base64.urlsafe_b64decode(
                str(cursor).encode('ascii') + b'===').decode('ascii'))
        except (ValueError, UnicodeError) as exc:
            raise ValueError('invalid cursor') from exc

    @staticmethod
    def _page(items, cursor, limit):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('limit must be between 1 and 200')
        offset = OntologyDrafts._decode_cursor(cursor)
        page = items[offset:offset + limit]
        next_offset = offset + len(page)
        next_cursor = None
        if next_offset < len(items):
            next_cursor = base64.urlsafe_b64encode(
                str(next_offset).encode('ascii')).decode('ascii').rstrip('=')
        return {'items': page, 'next_cursor': next_cursor,
                'total': len(items), 'limit': limit}

    @staticmethod
    def _class_maps(ontology):
        active = {node for node in ontology.classes if ontology.is_active_term(node)}
        parents = {
            node: {parent for parent in ontology.graph.objects(node, RDFS.subClassOf)
                   if parent in active}
            for node in active}
        children = {node: set() for node in active}
        for child, values in parents.items():
            for parent in values:
                children[parent].add(child)
        return active, parents, children

    @staticmethod
    def _labels(ontology):
        rows = ontology.summary(active_only=True)
        return {item['id']: item for item in [
            *rows['classes'], *rows['relations'], *rows['attributes']]}

    @staticmethod
    def _display_path(node, parents, labels):
        paths = []
        pending = deque([(node, [node])])
        while pending:
            current, path = pending.popleft()
            values = sorted(parents.get(current, set()), key=str)
            if not values:
                paths.append(path)
                continue
            for parent in values:
                if parent not in path:
                    pending.append((parent, [parent, *path]))
        best = min(paths or [[node]], key=lambda path: (len(path), [str(x) for x in path]))
        return [{'iri': str(item), 'label': labels.get(str(item), {}).get(
            'label', str(item))} for item in best]

    def _class_item(self, node, parents, children, labels, *, parent=None):
        item = labels.get(str(node), {'id': str(node), 'label': str(node)})
        return {
            **item, 'iri': str(node), 'canonical_iri': str(node),
            'is_reference': len(parents.get(node, set())) > 1 and parent is not None,
            'child_count': len(children.get(node, set())),
            'other_parent_count': max(0, len(parents.get(node, set())) -
                                      (1 if parent is not None else 0)),
            'display_path': self._display_path(node, parents, labels),
        }

    def roots(self, project_id, *, ontology_id=None, draft_id=None,
              cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        active, parents, children = self._class_maps(ontology)
        labels = self._labels(ontology)
        items = [self._class_item(node, parents, children, labels)
                 for node in sorted(active, key=str) if not parents[node]]
        return self._page(items, cursor, limit)

    def children(self, project_id, iri, *, ontology_id=None, draft_id=None,
                 cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        _, parents, children = self._class_maps(ontology)
        parent = URIRef(iri)
        labels = self._labels(ontology)
        items = [self._class_item(node, parents, children, labels, parent=parent)
                 for node in sorted(children.get(parent, set()), key=str)]
        return self._page(items, cursor, limit)

    def search(self, project_id, query, *, ontology_id=None, draft_id=None,
               cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        needle = _text(query, 'query').casefold()
        active, parents, children = self._class_maps(ontology)
        labels = self._labels(ontology)
        class_items = {
            str(node): self._class_item(node, parents, children, labels)
            for node in active}
        items = []
        for iri, item in sorted(labels.items()):
            haystack = ' '.join(str(item.get(key, '')) for key in (
                'id', 'name', 'label', 'label_zh', 'label_en', 'description'))
            if needle in haystack.casefold():
                found = class_items.get(iri, {**item, 'iri': iri,
                                              'canonical_iri': iri})
                items.append(found)
        return self._page(items, cursor, limit)

    def neighborhood(self, project_id, iri, *, ontology_id=None, draft_id=None,
                     cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        active, parents, children = self._class_maps(ontology)
        labels = self._labels(ontology)
        node = URIRef(iri)
        if node not in active:
            raise KeyError(iri)
        adjacent = sorted(parents[node] | children[node], key=str)
        items = [self._class_item(value, parents, children, labels,
                                  parent=node if value in children[node] else None)
                 for value in adjacent]
        result = self._page(items, cursor, limit)
        result['term'] = self._class_item(node, parents, children, labels)
        result['relations'] = [item for item in ontology.summary(active_only=True)[
            'relations'] if iri in item.get('domain', []) + item.get('range', [])]
        result['attributes'] = [item for item in ontology.summary(active_only=True)[
            'attributes'] if iri in item.get('domain', [])]
        return result

    def matrix(self, project_id, *, ontology_id=None, draft_id=None,
               cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        summary = ontology.summary(active_only=True)
        items = [{**item, 'iri': item['id'], 'kind': 'relation'}
                 for item in summary['relations']]
        items.extend({**item, 'iri': item['id'], 'kind': 'attribute'}
                     for item in summary['attributes'])
        items.sort(key=lambda item: item['iri'])
        return self._page(items, cursor, limit)


__all__ = [
    'OntologyDrafts', 'OntologyDraftError', 'RevisionConflict', 'StaleBase',
    'StaleSource', 'ValidationChanged', 'ValidationFailed', 'BatchNotAllowed',
    'VALIDATION_RULE_VERSION',
]
