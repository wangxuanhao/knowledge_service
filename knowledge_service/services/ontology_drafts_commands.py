"""本体变更命令的编译与影响评估（把前端命令展开成服务端可校验的操作集）。
"""

from __future__ import annotations

from .ontology_drafts_core import (
    EDITABLE_STATES, _status_guard, _canonical_json, _fingerprint, _semantic_operation,
)
from .ontology import Ontology
from .ontology import local_name
from .ontology import term_impact
from .ontology_operations import _apply_operations, apply_operations
from .ontology_operations import build_operation
from .ontology_operations import build_restore_operation
from .ontology_operations import canonical_turtle_diff
from .ontology_operations import operation_fingerprint
from .ontology_operations import retirement_dependencies
from rdflib import BNode, RDF
from rdflib import RDFS
from rdflib import URIRef
from rdflib.namespace import OWL
from rdflib.plugins.serializers.turtle import TurtleSerializer
import json
from .llm_runtime import llm_operation



class DraftCommandMixin:
    """本体变更命令的编译与影响评估（把前端命令展开成服务端可校验的操作集）。"""

    def _type_merge_plan(self, project_id, draft, ontology, source_iri, target_iri):
        from .ontology_type_merge import plan_class_merge, canonical_graph_snapshot
        records = self.repository.current_records(project_id)
        impact = self._impact(project_id, source_iri, ontology=ontology, records=records)
        direct = []
        for row in records:
            if row.get('kind') != 'entity' or not row.get('type'):
                continue
            try:
                resolved = ontology.resolve(row['type'], allowed=ontology.classes)
            except ValueError:
                continue
            if str(resolved) == source_iri:
                direct.append(row)
        direct_ids = {row['id'] for row in direct}
        linked = [row for row in records if row.get('kind') == 'relation'
                  and (row.get('subject_id') in direct_ids
                       or row.get('object_id') in direct_ids)]
        affected_records = [*direct, *linked]
        counts = {'entity': len(direct), 'relation': len(linked), 'attribute': 0}
        impact.update({
            'formal_records': len(affected_records), 'record_count': len(affected_records),
            'record_ids': [row['id'] for row in affected_records],
            'record_ids_truncated': False, 'kind_counts': counts,
            'linked_relation_count': len(linked),
            'record_preview': [{'id': row['id'], 'kind': row['kind'],
                                'via': 'type' if row['id'] in direct_ids else 'endpoint',
                                'text': (row.get('text') or '').strip()}
                               for row in affected_records[:50]],
            'referenced': bool(affected_records or impact['descendants']
                               or impact['constraints'] or impact['pending']),
            'classification_preview': {**impact['classification_preview'], 'records': counts},
        })
        affected = set(impact['record_ids'])
        # Include complete pending snapshots (with container membership/version),
        # so changing a mapping without changing its count invalidates confirmation.
        pending = []
        for row in records:
            metadata = row.get('metadata') or {}
            for key in ('review_candidates', 'discovery_candidates'):
                for candidate in metadata.get(key) or []:
                    if candidate.get('status') == 'pending':
                        pending.append({'record_id': row['id'], 'version': row.get('version'),
                                        'candidate': candidate})
        fingerprint = _fingerprint({
            'project_id': project_id, 'draft_id': draft['id'], 'revision': draft['revision'],
            'base_ontology_id': draft['base_ontology_id'],
            'current_ontology_id': self._latest_id(project_id),
            'graph': canonical_graph_snapshot(ontology.graph),
            'source_iri': source_iri, 'target_iri': target_iri,
            'records': sorted((row['id'], row.get('version')) for row in records if row['id'] in affected),
            'pending': sorted(pending, key=_canonical_json),
        })
        warnings = [{'code': 'controlled_migration_required',
                     'message': '加入草案后须审核发布，再预演迁移旧知识；实例不会自动迁移。'},
                    {'code': 'pending_mapping_review_required',
                     'message': '未发布候选须按最终获批结构重新核对映射。'}]
        try:
            operations, _ = plan_class_merge(ontology, source_iri, target_iri,
                                             published=draft['base_ontology_id'] is not None)
        except ValueError as exc:
            return {'source_iri': source_iri, 'target_iri': target_iri,
                    'expected_revision': draft['revision'], 'preview_fingerprint': fingerprint,
                    'can_merge': False, 'operations': [], 'impact': impact,
                    'warnings': [*warnings, {'code': 'merge_blocked', 'message': str(exc)}]}
        return {'source_iri': source_iri, 'target_iri': target_iri,
                'expected_revision': draft['revision'], 'preview_fingerprint': fingerprint,
                'can_merge': True, 'operations': operations, 'impact': impact, 'warnings': warnings}

    def type_merge_preview(self, project_id, draft_id, expected_revision, source_iri, target_iri):
        with self.repository.unit_of_work():
            draft = self.store.get(project_id, draft_id)
            self._assert_revision(draft, expected_revision)
            _status_guard(draft, EDITABLE_STATES, '预览类型合并')
            self._check_current(project_id, draft, expected_revision)
            turtle, _ = self._ontology_for_draft(project_id, draft)
            return self._type_merge_plan(project_id, draft, Ontology(turtle), source_iri, target_iri)

    @llm_operation
    def discover_parents(self, project_id, draft_id, expected_revision):
        """Append fresh hierarchy suggestions without replacing existing draft edits."""
        from collections import Counter
        from . import hierarchy_suggestion

        draft = self.store.get(project_id, draft_id)
        self._assert_revision(draft, expected_revision)
        _status_guard(draft, EDITABLE_STATES, '发现父类')
        self._check_current(project_id, draft, expected_revision)
        turtle, _ = self._ontology_for_draft(project_id, draft)
        ontology = Ontology(turtle)
        classes = [item for item in ontology.summary()['classes'] if item.get('active') is not False]
        labels = [str(item.get('label_zh') or item.get('label') or item.get('name')
                      or local_name(item['id'])).strip() or str(item['id']) for item in classes]
        frequencies = Counter(label.casefold() for label in labels)
        lookup = {(label if frequencies[label.casefold()] == 1
                   else f"{label} [class {index + 1}: {item['id']}]"): URIRef(item['id'])
                  for index, (label, item) in enumerate(zip(labels, classes))}
        if len(lookup) < 2:
            return {'draft': self.get(project_id, draft_id), 'added_count': 0, 'skipped_count': 0}
        try:
            raw = hierarchy_suggestion.suggest_hierarchy(
                list(lookup), request_name=draft.get('title') or '', refresh=True)
        except hierarchy_suggestion.HierarchySuggestionsUnavailable:
            raise
        except Exception as exc:
            raise hierarchy_suggestion.HierarchySuggestionsUnavailable(
                '父类发现模型请求失败，请稍后重试') from exc
        suggestions = hierarchy_suggestion.parse_suggestions(raw, list(lookup))
        graph = ontology.graph
        added = skipped = 0
        for child_name, parents in suggestions.items():
            child = lookup[child_name]
            for parent_name in parents:
                parent = lookup[parent_name]
                if ((child, RDFS.subClassOf, parent) in graph
                        or child in graph.transitive_objects(parent, RDFS.subClassOf)):
                    skipped += 1
                    continue
                graph.add((child, RDFS.subClassOf, parent))
                added += 1
        # No database transaction is held across the model request. A concurrent
        # edit or publication must reject the stale result, including no-op runs.
        fresh = self.store.get(project_id, draft_id)
        self._assert_revision(fresh, expected_revision)
        _status_guard(fresh, EDITABLE_STATES, '发现父类')
        self._check_current(project_id, fresh, expected_revision)
        updated = (self.command(project_id, draft_id, expected_revision, {
            'action': 'diff_turtle', 'edited_turtle': graph.serialize(format='turtle'),
            'reason': '根据当前结构重新发现父类，待人工审核',
        }) if added else self.get(project_id, draft_id))
        return {'draft': updated, 'added_count': added, 'skipped_count': skipped}

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

    def _rebuild_operations(self, project_id, draft, operations, ontology, command,
                            *, _intake_records=None, _intake_in_place=False):
        evidence = self._authoritative_evidence(draft, command)
        confidence = command.get('confidence')
        rebuilt = []
        working = (ontology if _intake_in_place
                   else Ontology(ontology.graph.serialize(format='turtle')))
        for raw in operations:
            action = raw['action']
            target = raw['target_iri']
            impact_context = {'ontology': working}
            if _intake_records is not None:
                impact_context['records'] = _intake_records
            if _intake_in_place:
                impact_context['_turtle_order'] = True
            impact = {
                **self._impact(project_id, target, **impact_context),
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
            if _intake_in_place:
                _apply_operations(working, [item])
                continue
            try:
                applied = apply_operations(
                    working.graph.serialize(format='turtle'), [item])
            except ValueError:
                continue
            working = Ontology(applied)
        return rebuilt

    def _compile_intake_commands(self, project_id, draft, commands, ontology):
        """Compile only confirmed additive intake suggestions in request-local state."""
        allowed = {'create_term', 'add_annotation', 'add_parent',
                   'add_domain', 'add_range', 'set_datatype'}
        if any(not isinstance(command, dict) or command.get('action') not in allowed
               or any(key in command for key in ('supersedes_operation_id', 'operation_id'))
               for command in commands):
            raise ValueError('intake suggestions must be additive atomic commands')
        # Preserve caller ownership even if a later command fails. All following
        # commands and definition checks reuse this single mutable working graph.
        working = Ontology(ontology.graph.serialize(format='turtle'))
        records = self.repository.current_records(project_id)
        compiled = []
        for command in commands:
            # Recursive Turtle emission assigns fresh blank-node identities on
            # the legacy path. Preserve that path only for impacts that expose
            # incoming blank nodes (e.g. SHACL paths or OWL union list cells).
            in_place = not any(isinstance(subject, BNode) for subject in
                working.graph.subjects(None, URIRef(command['target_iri'])))
            built = self._compile_command(
                project_id, draft, command, working,
                _intake_records=records, _intake_in_place=in_place)
            if not in_place:
                working = Ontology(apply_operations(
                    working.graph.serialize(format='turtle'), built))
            compiled.extend(built)
        # Also validate empty selections and retain a whole-graph boundary check.
        _apply_operations(working, [])
        return compiled

    def _compile_command(self, project_id, draft, command, ontology,
                         *, _intake_records=None, _intake_in_place=False):
        if not isinstance(command, dict):
            raise ValueError('command must be an object')
        action = command.get('action')
        if action == 'merge_class':
            source, target = command.get('target_iri'), command.get('replacement_iri')
            if not source or not target or not command.get('preview_fingerprint'):
                raise ValueError('类型合并须先预览并确认目标')
            plan = self._type_merge_plan(project_id, draft, ontology, source, target)
            if command['preview_fingerprint'] != plan['preview_fingerprint']:
                raise ValueError('合并预览已变化，请重新预览确认')
            if not plan['can_merge']:
                raise ValueError(plan['warnings'][-1]['message'])
            compiled = self._rebuild_operations(project_id, draft, plan['operations'], ontology, command)
            if any((item.get('validation') or {}).get('errors') for item in compiled):
                raise ValueError('合并操作存在阻塞，不能加入草案')
            # Ordinary commands may persist blocked atoms. A merge must instead
            # fail before append, including every sequential intermediate graph.
            working = ontology.graph.serialize(format='turtle')
            for item in compiled:
                working = apply_operations(working, [_semantic_operation(item)])
            from rdflib.compare import isomorphic
            intended = apply_operations(ontology.graph.serialize(format='turtle'), plan['operations'])
            if not isomorphic(Ontology(working).graph, Ontology(intended).graph):
                raise ValueError('合并操作与预览目标结构不一致')
            return compiled
        if action == 'batch':
            commands = command.get('commands')
            if not isinstance(commands, list) or not 1 <= len(commands) <= 100:
                raise ValueError('batch requires 1..100 commands')
            compiled, working = [], ontology
            for child in commands:
                if (not isinstance(child, dict) or child.get('action') in {'batch', 'withdraw_operation'}
                        or any(key in child for key in ('supersedes_operation_id', 'operation_id'))):
                    raise ValueError('batch children must be independent atomic commands')
                operations = self._compile_command(
                    project_id, draft, {**child, 'reason': child.get('reason', command.get('reason'))}, working)
                # Validate every child against its predecessors before persisting
                # any operations or advancing the draft revision.
                working = Ontology(apply_operations(
                    working.graph.serialize(format='turtle'), operations))
                compiled.extend(operations)
            return compiled
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
        if action == 'set_datatype' and before is None:
            # The command API accepts a requested datatype; freeze its reviewed
            # precondition from the current draft, including earlier edits.
            before = self._rebase_before(ontology, {
                'action': action, 'target_iri': target})
        # Risk, warnings and fingerprints are always recomputed here.  Client
        # supplied values with those names are intentionally ignored.
        return self._rebuild_operations(project_id, draft, [{
            'action': action, 'target_iri': target,
            'before': before, 'after': after,
        }], ontology, command, _intake_records=_intake_records,
            _intake_in_place=_intake_in_place)

    def _impact(self, project_id, target_iri, *, ontology=None, records=None,
                _turtle_order=False):
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
        references = term_impact(self, project_id, target_iri, ontology, records=records)
        records = records if records is not None else self.repository.current_records(project_id)
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
        incoming = list(ontology.graph.subject_predicates(target))
        if _turtle_order and len(incoming) > 1:
            # Match the named-subject/predicate order of the legacy Turtle
            # round trip without serializing or reparsing the working graph.
            serializer = TurtleSerializer(ontology.graph)
            serializer.preprocess()
            owners = {subject: {} for subject, _ in incoming}
            for subject, predicate in incoming:
                owners[subject][predicate] = []
            incoming = [(subject, predicate)
                        for subject in serializer.orderSubjects() if subject in owners
                        for predicate in serializer.sortProperties(owners[subject])]
        constraint_details = [{
            'subject': str(subject), 'predicate': str(predicate),
        } for subject, predicate in incoming]
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
        with self.repository.unit_of_work():
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
                if action == 'set_datatype' and command.get('before') is None and supersedes:
                    slot = next((index for index, row in enumerate(effective)
                                 if row['id'] == supersedes), None)
                    if slot is not None:
                        # A replacement replays at the original logical slot,
                        # so its precondition comes from that slot's input.
                        prefix = [row for row in effective[:slot]
                                  if (row.get('validation') or {}).get('rebase_status') != 'conflict'
                                  and not (row.get('validation') or {}).get('errors')]
                        ontology = Ontology(apply_operations(
                            self._base_turtle(project_id, draft['base_ontology_id']),
                            map(_semantic_operation, prefix)))
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
