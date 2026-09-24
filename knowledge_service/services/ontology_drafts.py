"""Governed ontology draft lifecycle and read-side graph projections.

This module is deliberately the domain seam above ``OntologyDraftStore``.  It
owns state transitions, server-side operation compilation, validation snapshots,
review policy and publication preflight.  The final atomic publication callback
is supplied by the repository layer (Task 5); this service never assembles a
partial publication transaction itself.
"""

from __future__ import annotations

import base64
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
    retirement_dependencies,
    validate_ontology_invariants,
)


VALIDATION_RULE_VERSION = 'ontology-drafts/1'
FINAL_STATES = frozenset({'published', 'closed'})
EDITABLE_STATES = frozenset({'editing'})
REVIEW_STATES = frozenset({'submitted'})
NEIGHBORHOOD_LINK_LIMIT = 100


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

    @staticmethod
    def _assert_revision(draft, expected_revision):
        if draft['revision'] != expected_revision:
            raise RevisionConflict(
                'ontology draft revision changed', details={
                    'draft_id': draft['id'],
                    'expected_revision': expected_revision,
                    'current_revision': draft['revision'],
                })

    def _history(self, project_id, draft_id):
        history = self.store.export(project_id)
        operations = [row for row in history['operations']
                      if row['draft_id'] == draft_id]
        decisions = [row for row in history['decisions']
                     if row['draft_id'] == draft_id]
        return operations, decisions

    def _effective_operations(self, project_id, draft_id):
        """Return current operations in stable logical, not append, order."""
        history, _ = self._history(project_id, draft_id)
        current_ids = {
            row['id'] for row in self.store.effective_operations(project_id, draft_id)}
        by_id = {row['id']: row for row in history}
        positions = {row['id']: index for index, row in enumerate(history)}

        def logical_position(operation):
            current = operation
            seen = set()
            while current.get('supersedes_operation_id'):
                parent_id = current['supersedes_operation_id']
                if parent_id in seen or parent_id not in by_id:
                    break
                seen.add(parent_id)
                current = by_id[parent_id]
            return positions[current['id']]

        effective = [row for row in history if row['id'] in current_ids]
        effective.sort(key=lambda row: (logical_position(row), positions[row['id']]))
        return [row for row in effective
                if not (row.get('validation') or {}).get('withdrawn')]

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
        return [row for row in self._effective_operations(project_id, draft_id)
                if (row.get('validation') or {}).get('rebase_status') != 'no-op']

    def _ontology_for_draft(self, project_id, draft):
        operations = self._active_operations(project_id, draft['id'])
        applicable = [row for row in operations
                      if (row.get('validation') or {}).get('rebase_status') != 'conflict'
                      and not (row.get('validation') or {}).get('errors')]
        turtle = self._base_turtle(project_id, draft['base_ontology_id'])
        if applicable:
            turtle = apply_operations(turtle, map(_semantic_operation, applicable))
        return turtle, operations

    def _preview(self, project_id, draft):
        _, all_decisions = self._history(project_id, draft['id'])
        effective = self._effective_operations(project_id, draft['id'])
        effective_ids = {row['id'] for row in effective}
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

    def _authoritative_evidence(self, draft, command):
        requested = command.get('evidence_refs', command.get('evidence'))
        if requested is not None and (
                not isinstance(requested, list)
                or not all(isinstance(item, str) and item.strip()
                           for item in requested)):
            raise ValueError('evidence_refs must be a list of non-empty strings')
        if draft['source_kind'] not in {
                'discovery', 'candidate', 'import', 'turtle'}:
            return list(dict.fromkeys(requested or []))

        context = draft.get('source_context') or {}
        authoritative = []
        for reference in self._source_references(context):
            document_id = reference.get('document_id') or reference.get('id')
            version = reference.get(
                'expected_document_version_id', reference.get(
                    'version_id', reference.get(
                        'expected_document_version', reference.get('version'))))
            if document_id and version is not None:
                authoritative.append(f'document-version:{document_id}:{version}')
            candidate_ids = [*(reference.get('candidate_ids') or [])]
            if reference.get('candidate_id'):
                candidate_ids.append(reference['candidate_id'])
            authoritative.extend(
                f'candidate:{candidate_id}' for candidate_id in candidate_ids)
        for key, prefix in (
                ('candidate_refs', 'candidate'),
                ('document_refs', 'document-version')):
            values = context.get(key) or []
            authoritative.extend(
                value if ':' in str(value) else f'{prefix}:{value}'
                for value in values)
        authoritative.extend(context.get('evidence_refs') or [])
        authoritative = list(dict.fromkeys(authoritative))
        if requested is not None:
            forged = sorted(set(requested) - set(authoritative))
            if forged:
                raise ValueError(
                    'evidence_refs are not present in the frozen source snapshot: '
                    + ', '.join(forged))
            return list(dict.fromkeys(requested))
        return authoritative

    @staticmethod
    def _document_evidence_ref(reference):
        document_id = reference.get('document_id') or reference.get('id')
        version = reference.get(
            'expected_document_version_id', reference.get(
                'version_id', reference.get(
                    'expected_document_version', reference.get('version'))))
        if document_id and version is not None:
            return f'document-version:{document_id}:{version}'
        return None

    def _refresh_operation_evidence(self, draft, refreshed_draft, evidence):
        """Refresh versioned refs without widening an operation's evidence set."""
        authoritative = set(self._authoritative_evidence(refreshed_draft, {}))
        old_documents = {
            (reference.get('document_id') or reference.get('id')): reference
            for reference in self._source_references(
                draft.get('source_context') or {})
        }
        new_documents = {
            (reference.get('document_id') or reference.get('id')): reference
            for reference in self._source_references(
                refreshed_draft.get('source_context') or {})
        }
        refreshed_refs = {}
        for document_id, old_reference in old_documents.items():
            new_reference = new_documents.get(document_id)
            if new_reference is None:
                continue
            old_ref = self._document_evidence_ref(old_reference)
            new_ref = self._document_evidence_ref(new_reference)
            if old_ref and new_ref:
                refreshed_refs[old_ref] = new_ref

        selected = []
        for reference in evidence or []:
            refreshed = refreshed_refs.get(reference, reference)
            if refreshed not in authoritative:
                raise StaleSource(
                    f'operation evidence {reference!r} no longer exists in '
                    'the refreshed source snapshot')
            if refreshed not in selected:
                selected.append(refreshed)
        return selected

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
        with self.repository._transaction():
            latest = self._latest_id(project_id)
            if base_ontology_id_or_none != latest:
                raise StaleBase(
                    'new draft must use the current ontology as its base', details={
                        'base_ontology_id': base_ontology_id_or_none,
                        'current_ontology_id': latest})
            context = dict(source_context or {})
            context['actor'] = actor
            if source_kind in {'discovery', 'candidate'}:
                # Honour an explicit caller snapshot as an optimistic source
                # precondition before filling omitted immutable snapshot fields.
                self._source_snapshot(
                    project_id, {'source_context': context}, require_current=True)
                context = self._refresh_source_context(project_id, context)
            draft = self.store.create(project_id, {
                'id': str(uuid4()), 'base_ontology_id': base_ontology_id_or_none,
                'source_kind': source_kind, 'status': 'editing', 'revision': 1,
                'title': title, 'summary': summary, 'source_context': context,
            })
            if source_kind in {'discovery', 'candidate'}:
                self._source_snapshot(project_id, draft)
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

    @staticmethod
    def _annotation_spec(predicate, value):
        spec = {'predicate': str(predicate), 'value': str(value)}
        if isinstance(value, URIRef):
            spec['type'] = 'iri'
        else:
            spec['language'] = value.language
            spec['datatype'] = str(value.datatype) if value.datatype else None
        return spec

    def _expand_restore(self, operation, ontology):
        """Keep activation atomic while making selected definition edges reviewable."""
        after = operation['after']
        selected = list(after['selected_fields'])
        template = after['template']
        activation = json.loads(_canonical_json(operation))
        activation['after']['activation_only'] = True
        activation['fingerprint'] = operation_fingerprint(activation)
        target = URIRef(operation['target_iri'])
        raw = [activation]

        if 'annotations' in selected:
            structural = {
                RDF.type, RDFS.subClassOf, RDFS.domain, RDFS.range,
                OWL.deprecated,
            }
            current = {
                _canonical_json(self._annotation_spec(predicate, value)):
                self._annotation_spec(predicate, value)
                for predicate, value in ontology.graph.predicate_objects(target)
                if predicate not in structural
            }
            desired = {
                _canonical_json(spec): spec
                for spec in template.get('annotations', [])}
            raw.extend({
                'action': 'remove_annotation', 'target_iri': str(target),
                'before': current[key],
            } for key in sorted(set(current) - set(desired)))
            raw.extend({
                'action': 'add_annotation', 'target_iri': str(target),
                'after': desired[key],
            } for key in sorted(set(desired) - set(current)))

        predicates = {
            'parents': ('parent', RDFS.subClassOf),
            'domain': ('domain', RDFS.domain),
            'range': ('range', RDFS.range),
        }
        for field, (suffix, predicate) in predicates.items():
            if field not in selected:
                continue
            if predicate in {RDFS.domain, RDFS.range}:
                current_values = {
                    str(value) for value in ontology.constraint_types(target, predicate)}
            else:
                current_values = {
                    str(value) for value in ontology.graph.objects(target, predicate)}
            desired_values = set(template.get(field, []))
            raw.extend({
                'action': f'remove_{suffix}', 'target_iri': str(target),
                'before': {'value': value},
            } for value in sorted(current_values - desired_values))
            raw.extend({
                'action': f'add_{suffix}', 'target_iri': str(target),
                'after': {'value': value},
            } for value in sorted(desired_values - current_values))

        if 'datatype' in selected:
            current_values = list(ontology.graph.objects(target, RDFS.range))
            current = str(current_values[0]) if len(current_values) == 1 else None
            desired = template.get('datatype')
            if current != desired:
                raw.append({
                    'action': 'set_datatype', 'target_iri': str(target),
                    'before': {'datatype': current},
                    'after': {'datatype': desired},
                })
        return raw

    def _rebuild_operations(self, project_id, draft, operations, ontology, command):
        evidence = self._authoritative_evidence(draft, command)
        confidence = command.get('confidence')
        rebuilt = []
        working = Ontology(ontology.graph.serialize(format='turtle'))
        for raw in operations:
            action = raw['action']
            target = raw['target_iri']
            impact = {
                **self._impact(project_id, target, ontology=working),
                **(raw.get('impact') or {}),
            }
            validation = {
                key: value for key, value in (raw.get('validation') or {}).items()
                if key not in {'warnings', 'source', 'confidence'}
            }
            warnings = list((raw.get('validation') or {}).get('warnings') or [])
            if action == 'retire_term':
                dependency_report = retirement_dependencies(
                    working, target,
                    active_records=range(impact['formal_records']))
                impact['dependency_report'] = dependency_report
                validation['errors'] = dependency_report['errors']
                validation['info'] = dependency_report['info']
                warnings.extend(dependency_report['warnings'])
            item = build_operation(
                action, target, before=raw.get('before'), after=raw.get('after'),
                source=draft['source_kind'], impact=impact, warnings=warnings,
                confidence=confidence,
                evidence=evidence or raw.get('evidence') or [],
                validation=validation,
                reason=command.get('reason', raw.get('reason')),
                ontology=working)
            if draft['source_kind'] == 'turtle' and item['risk'] == 'low':
                item['risk'] = 'medium'
                item['fingerprint'] = operation_fingerprint(item)
            rebuilt.append(item)
            try:
                applied = apply_operations(
                    working.graph.serialize(format='turtle'), [item])
            except ValueError:
                continue
            working = Ontology(applied)
        return rebuilt

    def _compile_command(self, project_id, draft, command, ontology):
        if not isinstance(command, dict):
            raise ValueError('command must be an object')
        action = command.get('action')
        if action in {'turtle', 'replace_turtle', 'diff_turtle'}:
            edited = command.get('edited_turtle', command.get('turtle'))
            if not isinstance(edited, str):
                raise ValueError('Turtle command requires edited_turtle')
            raw = canonical_turtle_diff(
                ontology.graph.serialize(format='turtle'), edited,
                published=draft['base_ontology_id'] is not None,
                base_ontology_id=draft['base_ontology_id'],
                source_ontology_id=command.get('source_ontology_id'),
                restore_operation_builder=lambda target_iri, source_ontology_id: (
                    build_restore_operation(
                        self.repository, project_id, target_iri,
                        source_ontology_id,
                        selected_fields=command.get('selected_fields') or [])))
            expanded = []
            for operation in raw:
                expanded.extend(
                    self._expand_restore(operation, ontology)
                    if operation['action'] == 'restore_term' else [operation])
            return self._rebuild_operations(
                project_id, draft, expanded, ontology, command)
        target = command.get('target_iri')
        if action == 'restore_term':
            selected = command.get('selected_fields', command.get('selection'))
            operation = build_restore_operation(
                self.repository, project_id, target,
                command.get('source_ontology_id'),
                selected_fields=selected or [])
            operation['reason'] = command.get('reason')
            operation['fingerprint'] = operation_fingerprint(operation)
            return self._rebuild_operations(
                project_id, draft, self._expand_restore(operation, ontology),
                ontology, command)
        action, before, after = self._operation_args(command)
        # Risk, warnings and fingerprints are always recomputed here.  Client
        # supplied values with those names are intentionally ignored.
        return self._rebuild_operations(project_id, draft, [{
            'action': action, 'target_iri': target,
            'before': before, 'after': after,
        }], ontology, command)

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
        records = self.repository.current_records(project_id, vectors='none')
        direct = [
            row for row in records
            if row.get('type') == target_iri
            or target_iri in (row.get('properties') or {})]
        direct_ids = {row['id'] for row in direct}
        linked = [
            row for row in records
            if row.get('kind') == 'relation' and row['id'] not in direct_ids
            and (row.get('subject_id') in direct_ids
                 or row.get('object_id') in direct_ids)]
        affected_records = [*direct, *linked]
        constraint_details = [{
            'subject': str(subject), 'predicate': str(predicate),
        } for subject, predicate in ontology.graph.subject_predicates(target)]
        constraints = len(constraint_details)
        formal_records = len(affected_records)
        pending_ids = set()
        target_names = {target_iri, local_name(target_iri)}
        for row in records:
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
            'record_ids': [row['id'] for row in affected_records],
            'record_ids_truncated': False,
            'record_preview': references['record_preview'],
            'kind_counts': references['kind_counts'],
            'linked_relation_count': references['linked_relation_count'],
            'constraints_detail': constraint_details,
            'pending_candidate_ids': sorted(pending_ids),
            'classification_preview': {
                'records': references['kind_counts'],
                'descendants': len(descendants),
                'constraints': constraints,
                'pending_candidates': pending,
            },
        }

    def command(self, project_id, draft_id, expected_revision, command):
        with self.repository._transaction():
            draft = self.store.get(project_id, draft_id)
            self._assert_revision(draft, expected_revision)
            if draft['status'] not in EDITABLE_STATES:
                raise ValueError('commands are only allowed while a draft is editing')
            self._check_current(project_id, draft, expected_revision)
            base_turtle, effective = self._ontology_for_draft(project_id, draft)
            ontology = Ontology(base_turtle)
            action = command.get('action') if isinstance(command, dict) else None
            supersedes = command.get(
                'supersedes_operation_id', command.get('operation_id'))
            if action == 'withdraw_operation':
                current = {row['id']: row for row in self._effective_operations(
                    project_id, draft_id)}
                withdrawn = current.get(supersedes)
                if withdrawn is None:
                    raise ValueError('withdrawn operation is not current')
                compiled = [{
                    'action': 'withdraw_operation',
                    'target_iri': withdrawn['target_iri'],
                    'before': {'operation_id': withdrawn['id']},
                    'after': None,
                    'evidence': [], 'impact': {},
                    'validation': {'withdrawn': True},
                    'risk': 'low', 'reason': command.get('reason'),
                    'supersedes_operation_id': withdrawn['id'],
                }]
                compiled[0]['fingerprint'] = operation_fingerprint(compiled[0])
            else:
                compiled = self._compile_command(
                    project_id, draft, command, ontology)
            if supersedes:
                current = {row['id']: row for row in effective}
                if supersedes not in current:
                    raise ValueError('superseded operation is not current')
                if len(compiled) != 1:
                    raise ValueError(
                        'one adjustment may supersede exactly one operation')
                compiled[0]['supersedes_operation_id'] = supersedes

            # Replacements occupy the original logical slot so dependent
            # annotations/edges never move ahead of their declaration.
            candidate_operations = list(effective)
            if supersedes:
                slot = next(index for index, row in enumerate(candidate_operations)
                            if row['id'] == supersedes)
                candidate_operations[slot:slot + 1] = (
                    [] if action == 'withdraw_operation' else compiled)
            else:
                candidate_operations.extend(compiled)

            working = self._base_turtle(project_id, draft['base_ontology_id'])
            for operation in candidate_operations:
                validation = operation.get('validation') or {}
                if validation.get('errors') or validation.get('withdrawn'):
                    continue
                try:
                    working = apply_operations(
                        working, [_semantic_operation(operation)])
                except ValueError as exc:
                    if operation not in compiled:
                        raise
                    issue = {
                        'code': 'operation_blocked', 'severity': 'error',
                        'message': str(exc), 'operation_ids': [],
                        'term_iris': [operation['target_iri']],
                    }
                    operation['validation'] = {
                        **validation,
                        'errors': [*(validation.get('errors') or []), issue],
                    }
                    operation['fingerprint'] = operation_fingerprint(operation)
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

    @staticmethod
    def _overlay_dependency_issues(ontology):
        declared = ontology.classes | ontology.relations | ontology.attributes
        structural = {
            RDF.type, RDFS.subClassOf, RDFS.domain, RDFS.range,
            OWL.deprecated,
            URIRef('http://purl.org/dc/terms/isReplacedBy'),
        }
        warnings = []
        for subject, predicate, value in ontology.graph:
            if (subject not in declared or value not in declared
                    or not ontology.is_active_term(subject)
                    or ontology.is_active_term(value)
                    or predicate in structural
                    or str(predicate).startswith('http://www.w3.org/ns/shacl#')):
                continue
            warnings.append({
                'code': 'active_custom_annotation_dependency',
                'severity': 'warning',
                'message': (
                    f'active term references deprecated term through {predicate}'),
                'operation_ids': [],
                'term_iris': [str(subject), str(value)],
            })
        info = [{
            'code': 'deprecated_term_structure_retained',
            'severity': 'info',
            'message': 'deprecated term definition remains available for restoration',
            'operation_ids': [], 'term_iris': [str(term)],
        } for term in sorted(declared, key=str)
            if not ontology.is_active_term(term)]
        return warnings, info

    @staticmethod
    def _extend_unique(target, additions):
        existing = {_canonical_json(item) for item in target}
        for item in additions:
            encoded = _canonical_json(item)
            if encoded not in existing:
                target.append(item)
                existing.add(encoded)

    def _compute_validation(self, project_id, draft):
        operations = self._active_operations(project_id, draft['id'])
        errors, warnings, info = self._operation_issues(operations)
        candidate_turtle = self._base_turtle(project_id, draft['base_ontology_id'])
        graph_report = {'conforms': True, 'errors': []}
        try:
            candidate_turtle = apply_operations(candidate_turtle, [
                _semantic_operation(row) for row in operations
                if (row.get('validation') or {}).get('rebase_status') != 'conflict'
                and not (row.get('validation') or {}).get('errors')])
            candidate_ontology = Ontology(candidate_turtle)
            graph_report = validate_ontology_invariants(candidate_ontology)
            errors.extend(graph_report.get('errors') or [])
            overlay_warnings, overlay_info = self._overlay_dependency_issues(
                candidate_ontology)
            self._extend_unique(warnings, overlay_warnings)
            self._extend_unique(info, overlay_info)
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
        self._assert_revision(draft, expected_revision)
        if draft['status'] in FINAL_STATES:
            raise ValueError('final drafts cannot be validated')
        self._check_current(project_id, draft, expected_revision)
        with self.repository._transaction():
            draft = self.store.get(project_id, draft_id)
            self._assert_revision(draft, expected_revision)
            self._check_current(project_id, draft, expected_revision)
            report, fingerprint, _ = self._compute_validation(project_id, draft)
            updated = self._cas(project_id, draft_id, expected_revision, {
                'validation_report': report,
                'validation_fingerprint': fingerprint,
            })
        return self._preview(project_id, updated)

    def submit(self, project_id, draft_id, expected_revision):
        draft = self.store.get(project_id, draft_id)
        self._assert_revision(draft, expected_revision)
        if draft['status'] != 'editing':
            raise ValueError('only editing drafts may be submitted')
        self._check_current(project_id, draft, expected_revision)
        with self.repository._transaction():
            draft = self.store.get(project_id, draft_id)
            self._assert_revision(draft, expected_revision)
            if draft['status'] != 'editing':
                raise ValueError('only editing drafts may be submitted')
            self._check_current(project_id, draft, expected_revision)
            operations = self._active_operations(project_id, draft_id)
            active = [row for row in operations
                      if (row.get('validation') or {}).get('rebase_status') != 'no-op']
            if not active:
                raise ValidationFailed(
                    'a draft must contain at least one effective operation')
            report, fingerprint, _ = self._compute_validation(project_id, draft)
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
                            acknowledged_warning_codes, *, allow_errors=False):
        report, current, turtle = self._compute_validation(project_id, draft)
        if (not supplied_fingerprint
                or supplied_fingerprint != current
                or draft.get('validation_fingerprint') != current):
            raise ValidationChanged(
                'validation snapshot changed; validate again', details={
                    'validation_fingerprint': current, 'report': report})
        if not report['conforms'] and not allow_errors:
            raise ValidationFailed('draft validation failed', details={'report': report})
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
        self._assert_revision(draft, expected_revision)
        if draft['status'] not in REVIEW_STATES:
            raise ValueError('decisions require a submitted draft')
        self._check_current(
            project_id, draft, expected_revision,
            expected_ontology_id=expected_ontology_id)
        actor = _text(actor, 'actor')
        proposed = _as_list(decisions, 'decisions')
        if not proposed or len(proposed) > 100:
            raise BatchNotAllowed('a decision request must contain 1..100 decisions')
        report, _, turtle = self._require_validation(
            project_id, draft, validation_fingerprint,
            acknowledged_warning_codes, allow_errors=True)
        operations = {row['id']: row for row in self._active_operations(
            project_id, draft_id)}
        _, previous_rows = self._history(project_id, draft_id)
        latest_decisions = {
            row['operation_id']: row
            for row in self._effective_decisions(previous_rows)}
        previous = {
            row['operation_id']: row
            for row in latest_decisions.values()
            if row.get('action') in {'approve', 'reject'}
            and row.get('operation_id') in operations
            and row.get('operation_fingerprint') == operations[
                row['operation_id']]['fingerprint']
        }
        blocked_ids = {
            operation_id
            for issue in report.get('errors') or []
            for operation_id in issue.get('operation_ids') or []}
        has_unscoped_errors = any(
            not (issue.get('operation_ids') or [])
            for issue in report.get('errors') or [])
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
            if (action == 'approve'
                    and (operation['id'] in blocked_ids or has_unscoped_errors)):
                raise ValidationFailed(
                    'blocking operations cannot be approved',
                    details={'report': report, 'operation_id': operation['id']})
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
                    latest_decisions.get(operation['id']) or {}).get('id'),
            })
        with self.repository._transaction():
            locked = self.store.get(project_id, draft_id)
            self._assert_revision(locked, expected_revision)
            if locked['status'] not in REVIEW_STATES:
                raise ValueError('decisions require a submitted draft')
            self._check_current(
                project_id, locked, expected_revision,
                expected_ontology_id=expected_ontology_id)
            self._require_validation(
                project_id, locked, validation_fingerprint,
                acknowledged_warning_codes, allow_errors=True)
            self.store.append_decisions(project_id, draft_id, saved)
            combined = {**previous}
            combined.update({
                row['operation_id']: row for row in saved
                if row['action'] in {'approve', 'reject'}})
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
        self._assert_revision(draft, expected_revision)
        if draft['status'] != 'editing':
            raise ValueError('only editing drafts may be closed')
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

    @staticmethod
    def _rebase_before(ontology, operation):
        """Rebuild graph-derived preconditions while retaining removal intent."""
        action = operation['action']
        target = URIRef(operation['target_iri'])
        old = operation.get('before') or {}
        if action in {'remove_parent', 'remove_domain', 'remove_range'}:
            return {'value': old.get('value')}
        if action == 'remove_annotation':
            return {key: old[key] for key in (
                'predicate', 'value', 'type', 'language', 'datatype')
                    if key in old}
        if action == 'set_datatype':
            values = sorted(
                (str(value) for value in ontology.graph.objects(target, RDFS.range)))
            return {'datatype': values[0] if len(values) == 1 else None}
        if action == 'advanced_rdf_patch':
            # The old fragment is the semantic removal intent for a scoped
            # patch; apply_operations will classify it as a conflict if the
            # latest graph no longer matches that precondition.
            return json.loads(_canonical_json(old))
        return None

    def rebase(self, project_id, draft_id, expected_revision,
               expected_ontology_id):
        draft = self.store.get(project_id, draft_id)
        self._assert_revision(draft, expected_revision)
        if draft['status'] in FINAL_STATES:
            raise ValueError('final drafts cannot be rebased')
        latest = self._latest_id(project_id)
        if expected_ontology_id != latest:
            raise StaleBase('rebase target must be the latest ontology', details={
                'expected_ontology_id': expected_ontology_id,
                'current_ontology_id': latest})
        if (draft['base_ontology_id'] == latest
                and draft['status'] not in {'stale_base', 'stale_source'}):
            raise ValueError('rebase requires a stale_base or stale_source draft')
        old_operations = self._active_operations(project_id, draft_id)
        turtle = self._base_turtle(project_id, latest)
        replacements = []
        classifications = []
        source_snapshot_fingerprint = None
        if draft['source_kind'] in {'discovery', 'candidate'}:
            refreshed_context = self._refresh_source_context(
                project_id, draft.get('source_context') or {})
            source_snapshot_fingerprint = _fingerprint(refreshed_context)
        rebuild_draft = dict(draft)
        if source_snapshot_fingerprint is not None:
            rebuild_draft['source_context'] = refreshed_context
        for operation in old_operations:
            ontology = Ontology(turtle)
            old_validation = operation.get('validation') or {}
            fresh_validation = {}
            if source_snapshot_fingerprint is not None:
                fresh_validation['source_snapshot_fingerprint'] = (
                    source_snapshot_fingerprint)
            rebuild_command = {
                'confidence': old_validation.get('confidence'),
                'reason': operation.get('reason'),
            }
            if source_snapshot_fingerprint is None:
                rebuild_command['evidence_refs'] = operation.get('evidence') or []
            else:
                rebuild_command['evidence_refs'] = self._refresh_operation_evidence(
                    draft, rebuild_draft, operation.get('evidence') or [])
            rebuilt = self._rebuild_operations(
                project_id, rebuild_draft, [{
                    'action': operation['action'],
                    'target_iri': operation['target_iri'],
                    'before': self._rebase_before(ontology, operation),
                    'after': json.loads(_canonical_json(operation.get('after'))),
                    'validation': fresh_validation,
                }], ontology, rebuild_command)[0]
            if self._operation_is_noop(ontology, operation):
                status, message = 'no-op', 'operation is already true on latest base'
            else:
                try:
                    if not (rebuilt.get('validation') or {}).get('errors'):
                        turtle = apply_operations(turtle, [rebuilt])
                    status, message = 'clean', None
                except ValueError as exc:
                    status, message = 'conflict', str(exc)
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
                replacement = rebuilt
                validation = {**(replacement.get('validation') or {}),
                              'rebase_status': status}
                if message:
                    validation['rebase_message'] = message
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
            locked = self.store.get(project_id, draft_id)
            self._assert_revision(locked, expected_revision)
            if self._latest_id(project_id) != expected_ontology_id:
                raise StaleBase('rebase target is no longer latest')
            if locked['source_kind'] in {'discovery', 'candidate'}:
                locked_context = self._refresh_source_context(
                    project_id, locked.get('source_context') or {})
                if _canonical_json(locked_context) != _canonical_json(context):
                    raise StaleSource('source changed while rebasing; retry')
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
        self._assert_revision(draft, expected_revision)
        if draft['status'] != 'reviewed':
            raise ValueError('only reviewed drafts may be published')
        self._check_current(
            project_id, draft, expected_revision,
            expected_ontology_id=expected_ontology_id)
        actor = _text(actor, 'actor')
        idempotency_key = _text(idempotency_key, 'idempotency_key')
        report, fingerprint, _ = self._require_validation(
            project_id, draft, validation_fingerprint,
            acknowledged_warning_codes, allow_errors=True)
        operations = self._active_operations(project_id, draft_id)
        _, decision_rows = self._history(project_id, draft_id)
        decisions = {row['operation_id']: row
                     for row in self._effective_decisions(decision_rows)}
        invalid = [
            operation['id'] for operation in operations
            if (operation['id'] not in decisions
                or decisions[operation['id']]['action'] not in {'approve', 'reject'}
                or decisions[operation['id']]['operation_fingerprint']
                != operation['fingerprint'])]
        if invalid:
            raise ValidationChanged(
                'publish requires a final current decision for every operation',
                details={'operation_ids': invalid})
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
    def _page_slice(items, cursor, limit):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('limit must be between 1 and 200')
        offset = OntologyDrafts._decode_cursor(cursor)
        page = items[offset:offset + limit]
        next_offset = offset + len(page)
        next_cursor = None
        if next_offset < len(items):
            next_cursor = base64.urlsafe_b64encode(
                str(next_offset).encode('ascii')).decode('ascii').rstrip('=')
        return page, {'next_cursor': next_cursor,
                      'total': len(items), 'limit': limit}

    @staticmethod
    def _page(items, cursor, limit):
        page, metadata = OntologyDrafts._page_slice(items, cursor, limit)
        return {'items': page, **metadata}

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
    def _term_item(ontology, node):
        labels = list(ontology.graph.objects(node, RDFS.label))
        zh = en = plain = ''
        for label in labels:
            language = (label.language or '').lower()
            if not zh and language.startswith('zh'):
                zh = str(label)
            elif not en and language.startswith('en'):
                en = str(label)
            elif not plain and not language:
                plain = str(label)
        item = {
            'id': str(node), 'name': local_name(node),
            'label': plain or en or local_name(node),
            'label_zh': zh, 'label_en': en,
            'description': str(ontology.graph.value(node, RDFS.comment) or ''),
            'active': ontology.is_active_term(node),
        }
        if node in ontology.classes:
            item['parents'] = [
                str(value) for value in ontology.graph.objects(
                    node, RDFS.subClassOf)]
        elif node in ontology.relations:
            item['domain'] = [
                str(value) for value in ontology.constraint_types(node, RDFS.domain)]
            item['range'] = [
                str(value) for value in ontology.constraint_types(node, RDFS.range)]
        elif node in ontology.attributes:
            item['domain'] = [
                str(value) for value in ontology.constraint_types(node, RDFS.domain)]
            item['range'] = [
                str(value) for value in ontology.graph.objects(node, RDFS.range)]
        return item

    def _display_path(self, node, parents, ontology):
        memo = {}
        visiting = set()

        def best_path(current):
            if current in memo:
                return memo[current]
            if current in visiting:
                return (current,)
            visiting.add(current)
            direct = sorted(parents.get(current, set()), key=str)
            candidates = [best_path(parent) + (current,) for parent in direct]
            visiting.remove(current)
            best = min(
                candidates or [(current,)],
                key=lambda path: (len(path), tuple(map(str, path))))
            memo[current] = best
            return best

        best = best_path(node)
        return [{'iri': str(item), 'label': self._term_item(
            ontology, item)['label']} for item in best]

    def _class_item(self, node, parents, children, ontology, *, parent=None):
        item = self._term_item(ontology, node)
        return {
            **item, 'iri': str(node), 'canonical_iri': str(node),
            'is_reference': len(parents.get(node, set())) > 1 and parent is not None,
            'child_count': len(children.get(node, set())),
            'other_parent_count': max(0, len(parents.get(node, set())) -
                                      (1 if parent is not None else 0)),
            'display_path': self._display_path(node, parents, ontology),
        }

    def roots(self, project_id, *, ontology_id=None, draft_id=None,
              cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        active, parents, children = self._class_maps(ontology)
        nodes = [node for node in sorted(active, key=str) if not parents[node]]
        page, metadata = self._page_slice(nodes, cursor, limit)
        return {'items': [self._class_item(
            node, parents, children, ontology) for node in page], **metadata}

    def children(self, project_id, iri, *, ontology_id=None, draft_id=None,
                 cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        _, parents, children = self._class_maps(ontology)
        parent = URIRef(iri)
        nodes = sorted(children.get(parent, set()), key=str)
        page, metadata = self._page_slice(nodes, cursor, limit)
        return {'items': [self._class_item(
            node, parents, children, ontology, parent=parent)
            for node in page], **metadata}

    def search(self, project_id, query, *, ontology_id=None, draft_id=None,
               cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        needle = _text(query, 'query').casefold()
        active, parents, children = self._class_maps(ontology)
        declared = sorted(
            (ontology.classes | ontology.relations | ontology.attributes), key=str)
        matches = []
        for node in declared:
            if not ontology.is_active_term(node):
                continue
            all_labels = [str(value) for value in ontology.graph.objects(
                node, RDFS.label)]
            description = str(ontology.graph.value(node, RDFS.comment) or '')
            haystack = ' '.join([
                str(node), local_name(node), description,
                *all_labels,
            ])
            if needle in haystack.casefold():
                matches.append(node)
        page, metadata = self._page_slice(matches, cursor, limit)
        items = []
        for node in page:
            iri = str(node)
            if node in active:
                items.append(self._class_item(
                    node, parents, children, ontology))
            else:
                items.append({**self._term_item(ontology, node),
                              'iri': iri, 'canonical_iri': iri})
        return {'items': items, **metadata}

    def neighborhood(self, project_id, iri, *, ontology_id=None, draft_id=None,
                     cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        active, parents, children = self._class_maps(ontology)
        node = URIRef(iri)
        if node not in active:
            raise KeyError(iri)
        adjacent = sorted(parents[node] | children[node], key=str)
        page, metadata = self._page_slice(adjacent, cursor, limit)
        result = {'items': [self._class_item(
            value, parents, children, ontology,
            parent=node if value in children[node] else None)
            for value in page], **metadata}
        result['term'] = self._class_item(node, parents, children, ontology)
        relation_nodes = sorted((
            value for value in ontology.relations
            if ontology.is_active_term(value)
            and node in (set(ontology.constraint_types(value, RDFS.domain))
                         | set(ontology.constraint_types(value, RDFS.range)))), key=str)
        attribute_nodes = sorted((
            value for value in ontology.attributes
            if ontology.is_active_term(value)
            and node in set(ontology.constraint_types(value, RDFS.domain))), key=str)
        projection_limit = min(limit, NEIGHBORHOOD_LINK_LIMIT)
        result['relations'] = [{
            **self._term_item(ontology, value),
            'iri': str(value), 'kind': 'relation',
        } for value in relation_nodes[:projection_limit]]
        result['attributes'] = [{
            **self._term_item(ontology, value),
            'iri': str(value), 'kind': 'attribute',
        } for value in attribute_nodes[:projection_limit]]
        result.update({
            'relation_count': len(relation_nodes),
            'attribute_count': len(attribute_nodes),
            'relations_truncated': len(relation_nodes) > projection_limit,
            'attributes_truncated': len(attribute_nodes) > projection_limit,
            'link_projection_limit': projection_limit,
        })
        return result

    def matrix(self, project_id, *, ontology_id=None, draft_id=None,
               cursor=None, limit=50):
        ontology, _ = self._read_ontology(project_id, ontology_id, draft_id)
        nodes = sorted([
            *((value, 'relation') for value in ontology.relations
              if ontology.is_active_term(value)),
            *((value, 'attribute') for value in ontology.attributes
              if ontology.is_active_term(value)),
        ], key=lambda item: str(item[0]))
        page, metadata = self._page_slice(nodes, cursor, limit)
        return {
            'items': [{
                **self._term_item(ontology, node),
                'iri': str(node), 'kind': kind,
            } for node, kind in page],
            **metadata,
        }


__all__ = [
    'OntologyDrafts', 'OntologyDraftError', 'RevisionConflict', 'StaleBase',
    'StaleSource', 'ValidationChanged', 'ValidationFailed', 'BatchNotAllowed',
    'VALIDATION_RULE_VERSION',
]
