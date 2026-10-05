"""本体变更命令的编译与影响评估（把前端命令展开成服务端可校验的操作集）。
"""

from __future__ import annotations

from .ontology_drafts_core import (
    EDITABLE_STATES, _status_guard, _canonical_json, _fingerprint, _semantic_operation,
)
from .ontology import Ontology
from .ontology import local_name
from .ontology import term_impact
from .ontology_operations import apply_operations
from .ontology_operations import build_operation
from .ontology_operations import build_restore_operation
from .ontology_operations import canonical_turtle_diff
from .ontology_operations import operation_fingerprint
from .ontology_operations import retirement_dependencies
from rdflib import RDF
from rdflib import RDFS
from rdflib import URIRef
from rdflib.namespace import OWL
import json



class DraftCommandMixin:
    """本体变更命令的编译与影响评估（把前端命令展开成服务端可校验的操作集）。"""

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
            if draft['source_kind'] in {'turtle', 'discovery'} and item['risk'] == 'low':
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
            _status_guard(draft, EDITABLE_STATES, '修改本体',
                          '先在设计阶段点「提交审核」把变更集合冻结，再逐项审核。')
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
            # 改了变更集合 = 这一轮的提交失效：请求回到「还没提交」，
            # 必须重新提交并校验（旧决定也会因为 operation_fingerprint 变化而失效）。
            updated = self._cas(project_id, draft_id, expected_revision, {
                'validation_report': None, 'validation_fingerprint': None,
                'submitted_at': None})
        return self._preview(project_id, updated)

    # -------------------------------------------------------------- validation
