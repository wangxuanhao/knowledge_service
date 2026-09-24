"""Pure compiler and invariant checks for governed ontology operations.

This module deliberately knows nothing about drafts, routes, or publication.  It
turns reviewable JSON operations into one RDF graph and provides the repository
adapter needed to freeze restore templates from immutable ontology versions.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from rdflib import BNode, Graph, Literal, RDF, RDFS, URIRef
from rdflib.collection import Collection
from rdflib.compare import isomorphic
from rdflib.namespace import Namespace, OWL, SH, XSD

from .ontology import Ontology, absolute_iri, term_kind


DCTERMS = Namespace('http://purl.org/dc/terms/')

_DECLARATIONS = {
    'class': OWL.Class,
    'relation': OWL.ObjectProperty,
    'attribute': OWL.DatatypeProperty,
}
_STRUCTURAL_PREDICATES = {
    RDF.type, RDFS.subClassOf, RDFS.domain, RDFS.range, OWL.deprecated,
    DCTERMS.isReplacedBy,
}
_LIFECYCLE_PREDICATES = {RDF.type, OWL.deprecated, DCTERMS.isReplacedBy}
_HIGH_ACTIONS = {
    'remove_parent', 'remove_domain', 'remove_range', 'set_datatype',
    'retire', 'restore', 'advanced_rdf_patch',
}
_MEDIUM_ACTIONS = {'add_parent', 'add_domain', 'add_range'}
_NON_MANUAL_SOURCES = {
    'discovery', 'ai', 'import', 'candidate', 'semantic_suggestion',
    'candidate_semantic_suggestion',
}
_SELECTABLE_TEMPLATE_FIELDS = {
    'annotations', 'parents', 'domain', 'range', 'datatype',
}


def _canonical_json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(',', ':'))


def operation_fingerprint(operation: dict) -> str:
    """Return a stable SHA-256 over the semantic operation payload."""
    if not isinstance(operation, dict):
        raise ValueError('本体操作必须是对象')
    payload = {key: value for key, value in operation.items()
               if key not in {'fingerprint', 'id', 'created_at', 'source', 'confidence'}}
    return hashlib.sha256(_canonical_json(payload).encode('utf-8')).hexdigest()


def operation_risk(action: str, *, source: str = 'manual', impact=None,
                   warnings=None, confidence=None) -> str:
    """Apply the fixed server-side risk floor; confidence can never lower it."""
    impact = impact or {}
    warnings = warnings or []
    count = lambda *keys: max(int(impact.get(key, 0) or 0) for key in keys)
    if (action in _HIGH_ACTIONS
            or count('formal_records', 'formal_record_count', 'record_count') > 0
            or count('descendants', 'descendant_count') > 50
            or count('constraints', 'constraint_count') > 10
            or count('pending', 'pending_count', 'pending_review_count') > 20):
        return 'high'
    low_create = (action == 'create_term'
                  and impact.get('leaf') is True
                  and impact.get('referenced') is False)
    low_manual = action in {'add_annotation', 'remove_annotation'} or low_create
    risk = 'low' if source == 'manual' and low_manual else 'medium'
    if action in _MEDIUM_ACTIONS or source in _NON_MANUAL_SOURCES or warnings:
        risk = 'medium'
    return risk


def is_batch_eligible(operation: dict) -> bool:
    """Only low-risk operations may enter an unattended batch."""
    warnings = operation.get('warnings')
    if warnings is None:
        warnings = (operation.get('validation') or {}).get('warnings', [])
    validation = operation.get('validation') or {}
    floor = operation_risk(
        operation.get('action', ''),
        source=operation.get('source', validation.get('source', 'manual')),
        impact=operation.get('impact'), warnings=warnings,
        confidence=operation.get('confidence', validation.get('confidence')))
    return operation.get('risk') == 'low' and floor == 'low'


def build_operation(action: str, target_iri: str, *, before=None, after=None,
                    source: str = 'manual', impact=None, warnings=None,
                    confidence=None, evidence=None, validation=None,
                    reason=None) -> dict:
    if not isinstance(action, str) or not action:
        raise ValueError('本体操作 action 不能为空')
    if not isinstance(target_iri, str) or not absolute_iri(target_iri):
        raise ValueError('本体操作 target_iri 必须是绝对 IRI')
    impact = dict(impact or {})
    warnings = list(warnings or [])
    operation = {
        'action': action,
        'target_iri': target_iri,
        'before': before,
        'after': after,
        'source': source,
        'confidence': confidence,
        'evidence': list(evidence or []),
        'impact': impact,
        'validation': {
            **dict(validation or {}), 'warnings': warnings,
            'source': source, 'confidence': confidence,
        },
        'risk': operation_risk(
            action, source=source, impact=impact, warnings=warnings,
            confidence=confidence),
        'reason': reason,
    }
    operation['fingerprint'] = operation_fingerprint(operation)
    return operation


def _graph(turtle: str) -> Graph:
    return Ontology(turtle).graph


def _remove_bnode_tree(graph: Graph, node) -> None:
    if not isinstance(node, BNode):
        return
    objects = list(graph.objects(node, None))
    graph.remove((node, None, None))
    for value in objects:
        if isinstance(value, BNode) and not any(graph.triples((None, None, value))):
            _remove_bnode_tree(graph, value)


def _constraint_values(graph: Graph, subject, predicate) -> list[URIRef]:
    values: list[URIRef] = []
    for value in graph.objects(subject, predicate):
        head = graph.value(value, OWL.unionOf)
        values.extend(Collection(graph, head) if head is not None else [value])
    return list(values)


def _set_constraint(graph: Graph, subject, predicate, values: Iterable[str | URIRef]) -> None:
    values = list(values)
    if len(values) != len(set(map(str, values))):
        raise ValueError('约束值不能重复')
    resolved = sorted((URIRef(str(value)) for value in values), key=str)
    old = list(graph.objects(subject, predicate))
    graph.remove((subject, predicate, None))
    for value in old:
        _remove_bnode_tree(graph, value)
    if len(resolved) == 1:
        graph.add((subject, predicate, resolved[0]))
    elif len(resolved) > 1:
        union = BNode()
        head = BNode()
        graph.add((subject, predicate, union))
        graph.add((union, OWL.unionOf, head))
        Collection(graph, head, resolved)


def _term_template(ontology: Ontology, iri: str) -> dict:
    node = ontology.resolve(iri)
    kind = term_kind(ontology, node)
    annotations = []
    for predicate, value in ontology.graph.predicate_objects(node):
        if predicate in _STRUCTURAL_PREDICATES:
            continue
        if isinstance(value, Literal):
            annotations.append({
                'predicate': str(predicate), 'value': str(value),
                'language': value.language,
                'datatype': str(value.datatype) if value.datatype else None,
            })
        elif isinstance(value, URIRef):
            annotations.append({
                'predicate': str(predicate), 'value': str(value), 'type': 'iri'})
    template = {
        'kind': kind,
        'annotations': sorted(annotations, key=_canonical_json),
        'parents': sorted(str(value) for value in
                          ontology.graph.objects(node, RDFS.subClassOf)),
        'domain': sorted(str(value) for value in
                         ontology.constraint_types(node, RDFS.domain)),
        'range': sorted(str(value) for value in
                        ontology.constraint_types(node, RDFS.range)),
        'datatype': None,
    }
    if kind == 'attribute':
        ranges = template['range']
        template['datatype'] = ranges[0] if len(ranges) == 1 else None
    return template


def _literal(spec: dict) -> Literal:
    language = spec.get('language')
    datatype = spec.get('datatype')
    if language and datatype:
        raise ValueError('RDF 文本不能同时指定语言和 datatype')
    return Literal(
        spec.get('value', ''), lang=language,
        datatype=URIRef(datatype) if datatype else None)


def _apply_template(graph: Graph, node: URIRef, template: dict,
                    selected_fields: Iterable[str] | None = None) -> None:
    fields = set(selected_fields or _SELECTABLE_TEMPLATE_FIELDS)
    if 'annotations' in fields:
        for predicate, value in list(graph.predicate_objects(node)):
            if predicate not in _STRUCTURAL_PREDICATES:
                graph.remove((node, predicate, value))
        for annotation in template.get('annotations', []):
            value = (URIRef(annotation['value']) if annotation.get('type') == 'iri'
                     else _literal(annotation))
            graph.add((node, URIRef(annotation['predicate']), value))
    if 'parents' in fields:
        graph.remove((node, RDFS.subClassOf, None))
        for parent in template.get('parents', []):
            graph.add((node, RDFS.subClassOf, URIRef(parent)))
    if 'domain' in fields:
        _set_constraint(graph, node, RDFS.domain, template.get('domain', []))
    if 'range' in fields:
        _set_constraint(graph, node, RDFS.range, template.get('range', []))
    if 'datatype' in fields and template.get('datatype'):
        _set_constraint(graph, node, RDFS.range, [template['datatype']])


def _ensure_kind(graph: Graph, node: URIRef, kind: str | None = None) -> str:
    ontology = Ontology(graph.serialize(format='turtle'))
    actual = term_kind(ontology, node)
    if actual is None:
        raise ValueError(f'本体术语不存在：{node}')
    if kind is not None and actual != kind:
        raise ValueError('本体术语类型不一致')
    return actual


def _assert_no_parent_cycle(graph: Graph, child: URIRef, parent: URIRef) -> None:
    if child == parent:
        raise ValueError('术语不能继承自身，否则会形成循环（cycle）')
    ontology = Ontology(graph.serialize(format='turtle'))
    if parent not in ontology.classes:
        raise ValueError(f'父类不存在：{parent}')
    if child in ontology.parents(parent):
        raise ValueError('父类关系会形成多跳循环（cycle）')


def _patch_changes_lifecycle(base: Graph, target: Graph) -> bool:
    if _declarations(base) != _declarations(target):
        return True
    return any(
        set(base.triples((None, predicate, None)))
        != set(target.triples((None, predicate, None)))
        for predicate in (OWL.deprecated, DCTERMS.isReplacedBy))


def apply_operations(base_turtle: str, operations: Iterable[dict]) -> str:
    """The only mutation entry point: apply ordered operations to one RDF graph."""
    graph = _graph(base_turtle)
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError('本体操作必须是对象')
        fingerprint = operation.get('fingerprint')
        if fingerprint and fingerprint != operation_fingerprint(operation):
            raise ValueError('本体操作 fingerprint 指纹与内容不一致')
        action = operation.get('action')
        target_iri = operation.get('target_iri', '')
        if not isinstance(target_iri, str) or not absolute_iri(target_iri):
            raise ValueError('本体操作 target_iri 必须是绝对 IRI')
        target = URIRef(target_iri)
        before = operation.get('before') or {}
        after = operation.get('after') or {}
        if action == 'create_term':
            kind = after.get('kind')
            if kind not in _DECLARATIONS:
                raise ValueError('不支持的本体术语类型')
            if any((target, RDF.type, value) in graph for value in _DECLARATIONS.values()):
                raise ValueError('本体术语已存在')
            graph.add((target, RDF.type, _DECLARATIONS[kind]))
            parents = after.get('parents', [])
            if len(parents) != len(set(parents)):
                raise ValueError('父类不能重复')
            for parent in parents:
                _assert_no_parent_cycle(graph, target, URIRef(parent))
                graph.add((target, RDFS.subClassOf, URIRef(parent)))
            _set_constraint(graph, target, RDFS.domain, after.get('domain', []))
            ranges = ([after['datatype']] if after.get('datatype') else after.get('range', []))
            _set_constraint(graph, target, RDFS.range, ranges)
            _apply_template(graph, target, after, ['annotations'])
        elif action in {'add_parent', 'remove_parent'}:
            _ensure_kind(graph, target, 'class')
            value = URIRef((after if action == 'add_parent' else before).get('value', ''))
            if action == 'add_parent':
                if (target, RDFS.subClassOf, value) in graph:
                    raise ValueError('父类不能重复')
                _assert_no_parent_cycle(graph, target, value)
                graph.add((target, RDFS.subClassOf, value))
            else:
                graph.remove((target, RDFS.subClassOf, value))
        elif action in {'add_domain', 'remove_domain', 'replace_domain',
                        'add_range', 'remove_range', 'replace_range'}:
            _ensure_kind(graph, target)
            predicate = RDFS.domain if action.endswith('domain') else RDFS.range
            if action.startswith('replace_'):
                values = after.get('values', [])
            else:
                values = _constraint_values(graph, target, predicate)
                value = URIRef((after if action.startswith('add_') else before).get('value', ''))
                if action.startswith('add_'):
                    if value in values:
                        raise ValueError('约束值不能重复')
                    values.append(value)
                else:
                    values = [item for item in values if item != value]
            ontology = Ontology(graph.serialize(format='turtle'))
            if predicate == RDFS.domain or term_kind(ontology, target) == 'relation':
                for value in values:
                    if URIRef(str(value)) not in ontology.classes:
                        raise ValueError(f'domain/range 类不存在：{value}')
            _set_constraint(graph, target, predicate, values)
        elif action == 'set_datatype':
            _ensure_kind(graph, target, 'attribute')
            datatype = after.get('datatype')
            _set_constraint(graph, target, RDFS.range, [datatype] if datatype else [])
        elif action in {'add_annotation', 'remove_annotation'}:
            _ensure_kind(graph, target)
            spec = after if action == 'add_annotation' else before
            predicate = URIRef(spec.get('predicate', ''))
            if predicate in _STRUCTURAL_PREDICATES:
                raise ValueError('结构谓词不能作为普通 annotation 修改')
            value = URIRef(spec['value']) if spec.get('type') == 'iri' else _literal(spec)
            if action == 'add_annotation':
                graph.add((target, predicate, value))
            else:
                graph.remove((target, predicate, value))
        elif action == 'retire':
            kind = _ensure_kind(graph, target)
            graph.set((target, OWL.deprecated, Literal(True)))
            replacement = after.get('replacement')
            if replacement:
                iri = replacement.get('iri') if isinstance(replacement, dict) else replacement
                if not iri or not absolute_iri(iri):
                    raise ValueError('isReplacedBy replacement 必须包含绝对 IRI')
                candidate = URIRef(iri)
                replacement_kind = _ensure_kind(graph, candidate)
                if candidate == target or replacement_kind != kind:
                    raise ValueError('isReplacedBy 必须指向同类型的其他活动术语')
                if not Ontology(graph.serialize(format='turtle')).is_active_term(candidate):
                    raise ValueError('isReplacedBy 不能指向已停用术语')
                graph.set((target, DCTERMS.isReplacedBy, URIRef(iri)))
        elif action == 'restore':
            if not after.get('source_ontology_id') or not isinstance(after.get('template'), dict):
                raise ValueError('restore 必须携带 source_ontology_id 和不可变 template')
            selected = after.get('selected_fields')
            if not selected:
                raise ValueError('restore 必须显式选择恢复字段')
            _ensure_kind(graph, target, after['template'].get('kind'))
            _apply_template(graph, target, after['template'], selected)
            graph.remove((target, OWL.deprecated, None))
        elif action in {'advanced_rdf_patch', 'replace_shacl_shape'}:
            target_turtle = after.get('turtle')
            if action == 'advanced_rdf_patch' and not target_turtle:
                touched = {URIRef(item.get('predicate', ''))
                           for key in ('add', 'remove') for item in after.get(key, [])}
                if touched & _LIFECYCLE_PREDICATES:
                    raise ValueError('advanced RDF patch 不允许改变类型或生命周期')
                raise ValueError('advanced RDF patch 需要完整的 canonical turtle')
            candidate = _graph(target_turtle) if target_turtle else graph
            if _patch_changes_lifecycle(graph, candidate):
                raise ValueError('advanced RDF patch 不允许改变类型、停用或替换关系')
            graph = candidate
        else:
            raise ValueError(f'不支持的本体操作：{action}')
    invariant_report = validate_ontology_invariants(
        Ontology(graph.serialize(format='turtle')))
    if not invariant_report['conforms']:
        raise ValueError(
            '活动术语不能依赖已停用术语（active dependency）：'
            + _canonical_json(invariant_report['errors']))
    return graph.serialize(format='turtle')


def retirement_dependencies(ontology: Ontology, target_iri: str, *,
                            active_records=None, historical_records=None) -> dict:
    """Return the fixed dependency matrix used by retirement previews."""
    target = ontology.resolve(target_iri)
    errors = []
    for child in ontology.graph.subjects(RDFS.subClassOf, target):
        if ontology.is_active_term(child):
            errors.append({'kind': 'active_child', 'term': str(child)})
    for predicate, kind in ((RDFS.domain, 'active_domain'), (RDFS.range, 'active_range')):
        for owner in sorted(ontology.relations | ontology.attributes, key=str):
            if (ontology.is_active_term(owner)
                    and target in ontology.constraint_types(owner, predicate)):
                errors.append({'kind': kind, 'term': str(owner)})
    for subject, predicate in ontology.graph.subject_predicates(target):
        if str(predicate).startswith(str(SH)):
            errors.append({'kind': 'active_shacl', 'term': str(subject)})
        elif predicate == DCTERMS.isReplacedBy and ontology.is_active_term(subject):
            errors.append({'kind': 'active_replacement', 'term': str(subject)})
    warnings = []
    custom = sorted({str(predicate) for predicate in ontology.graph.predicates(target, None)
                     if predicate not in _STRUCTURAL_PREDICATES
                     and predicate not in {RDFS.label, RDFS.comment}})
    if custom:
        warnings.append({'kind': 'custom_annotation', 'predicates': custom})
    return {
        'errors': errors,
        'warnings': warnings,
        'retained_definition': _term_template(ontology, str(target)),
        'impact': {
            'active_records': len(active_records or []),
            'historical_records': len(historical_records or []),
        },
    }


def validate_ontology_invariants(ontology: Ontology) -> dict:
    """Check that active semantics never depend on deprecated terms."""
    errors = []
    declared = ontology.classes | ontology.relations | ontology.attributes
    for child in sorted(ontology.classes, key=str):
        if not ontology.is_active_term(child):
            continue
        for parent in ontology.graph.objects(child, RDFS.subClassOf):
            if parent in declared and not ontology.is_active_term(parent):
                errors.append({'kind': 'active_child', 'term': str(child),
                               'deprecated_term': str(parent)})
    for owner in sorted(ontology.relations | ontology.attributes, key=str):
        if not ontology.is_active_term(owner):
            continue
        for predicate, kind in ((RDFS.domain, 'active_domain'),
                                (RDFS.range, 'active_range')):
            for value in ontology.constraint_types(owner, predicate):
                if value in declared and not ontology.is_active_term(value):
                    errors.append({'kind': kind, 'term': str(owner),
                                   'deprecated_term': str(value)})
    for subject, predicate, value in ontology.graph:
        if (str(predicate).startswith(str(SH)) and value in declared
                and not ontology.is_active_term(value)):
            errors.append({'kind': 'active_shacl', 'term': str(subject),
                           'deprecated_term': str(value)})
        if (predicate == DCTERMS.isReplacedBy and subject in declared
                and ontology.is_active_term(subject) and value in declared
                and not ontology.is_active_term(value)):
            errors.append({'kind': 'active_replacement', 'term': str(subject),
                           'deprecated_term': str(value)})
    return {'conforms': not errors, 'errors': errors}


def build_restore_operation(repository, project_id: str, target_iri: str,
                            source_ontology_id: str | None, *,
                            selected_fields: Iterable[str]) -> dict:
    """Freeze an explicitly selected definition from a same-project version."""
    if not source_ontology_id:
        raise ValueError('restore 需要 source_ontology_id')
    selected = list(selected_fields or [])
    if (not selected or len(selected) != len(set(selected))
            or set(selected) - _SELECTABLE_TEMPLATE_FIELDS):
        raise ValueError('restore 必须显式选择有效且不重复的定义字段')
    try:
        source = repository.get_ontology(project_id, source_ontology_id)
    except KeyError as exc:
        raise ValueError('source_ontology_id 不属于此项目或不存在') from exc
    template = _term_template(Ontology(source['turtle']), target_iri)
    captured = {'kind': template['kind']}
    for field in selected:
        captured[field] = template[field]
    after = {
        'source_ontology_id': source_ontology_id,
        'selected_fields': selected,
        'template': captured,
    }
    current_template = _term_template(
        Ontology(repository.get_ontology(project_id)['turtle']), target_iri)
    for field in selected:
        current_template[field] = template[field]
    impact = {'preview': {**current_template, 'active': True}}
    return build_operation('restore', target_iri, after=after, impact=impact)


def _shacl_nodes(graph: Graph) -> set:
    roots = {subject for subject, predicate, obj in graph
             if (str(predicate).startswith(str(SH))
                 or obj in {SH.NodeShape, SH.PropertyShape})}
    nodes = set(roots)
    pending = list(roots)
    while pending:
        node = pending.pop()
        for value in graph.objects(node, None):
            if isinstance(value, BNode) and value not in nodes:
                nodes.add(value)
                pending.append(value)
    return nodes


def _allowed_bnodes(graph: Graph) -> set:
    allowed = _shacl_nodes(graph)
    pending = [head for union in graph.objects(None, OWL.unionOf)
               for head in [union] if isinstance(head, BNode)]
    allowed.update(subject for subject in graph.subjects(OWL.unionOf, None)
                   if isinstance(subject, BNode))
    while pending:
        node = pending.pop()
        if node in allowed:
            continue
        allowed.add(node)
        for value in graph.objects(node, None):
            if isinstance(value, BNode):
                pending.append(value)
    return allowed


def _validate_supported_bnodes(graph: Graph) -> None:
    if any(graph.subjects(RDF.type, OWL.Restriction)):
        raise ValueError('不支持 owl:Restriction 或其他复杂 OWL 空白节点')
    all_nodes = {value for triple in graph for value in triple if isinstance(value, BNode)}
    unsupported = all_nodes - _allowed_bnodes(graph)
    if unsupported:
        raise ValueError('不支持复杂 RDF 空白节点；请使用命名 IRI')


def _canonicalize_supported_unions(source: Graph) -> Graph:
    canonical = Graph()
    canonical += source
    subjects = {
        subject for subject, predicate, value in source
        if predicate in {RDFS.domain, RDFS.range}
        and source.value(value, OWL.unionOf) is not None
    }
    for subject in subjects:
        for predicate in (RDFS.domain, RDFS.range):
            values = _constraint_values(canonical, subject, predicate)
            if values:
                _set_constraint(canonical, subject, predicate, values)
    return canonical


def _declarations(graph: Graph) -> dict[URIRef, str]:
    found = {}
    for kind, declaration in _DECLARATIONS.items():
        for subject in graph.subjects(RDF.type, declaration):
            if isinstance(subject, URIRef):
                found[subject] = kind
    return found


def canonical_turtle_diff(base_turtle: str, edited_turtle: str, *,
                          published: bool = True) -> list[dict]:
    """Compile a graph-semantic Turtle diff into reviewable atomic operations."""
    base = _graph(base_turtle)
    edited = _graph(edited_turtle)
    if isomorphic(base, edited):
        return []
    _validate_supported_bnodes(base)
    _validate_supported_bnodes(edited)
    if isomorphic(
            _canonicalize_supported_unions(base),
            _canonicalize_supported_unions(edited)):
        return []
    base_declarations = _declarations(base)
    edited_declarations = _declarations(edited)
    removed = set(base_declarations) - set(edited_declarations)
    if removed and published:
        raise ValueError('已发布术语声明不能物理删除；请使用 retire 停用操作')
    for term in set(base_declarations) & set(edited_declarations):
        if base_declarations[term] != edited_declarations[term]:
            raise ValueError('术语类型不能通过 Turtle diff 改变')
    for predicate in (OWL.deprecated, DCTERMS.isReplacedBy):
        if set(base.triples((None, predicate, None))) != set(edited.triples((None, predicate, None))):
            raise ValueError('生命周期与替换关系必须使用 retire/restore 操作')

    base_shacl = Graph()
    edited_shacl = Graph()
    for source, target in ((base, base_shacl), (edited, edited_shacl)):
        nodes = _shacl_nodes(source)
        for triple in source:
            if triple[0] in nodes or (isinstance(triple[2], BNode) and triple[2] in nodes):
                target.add(triple)
    if not isomorphic(base_shacl, edited_shacl):
        return [build_operation(
            'replace_shacl_shape', 'urn:knowledge:ontology:shacl',
            after={'turtle': edited_turtle})]

    operations = []
    for term in sorted(set(edited_declarations) - set(base_declarations), key=str):
        template = _term_template(Ontology(edited_turtle), str(term))
        operations.append(build_operation('create_term', str(term), after=template,
                                          impact={'leaf': True, 'referenced': False}))
    common = sorted(set(base_declarations) & set(edited_declarations), key=str)
    for term in common:
        kind = edited_declarations[term]
        old_parents = sorted(str(value) for value in base.objects(term, RDFS.subClassOf))
        new_parents = sorted(str(value) for value in edited.objects(term, RDFS.subClassOf))
        for value in sorted(set(new_parents) - set(old_parents)):
            operations.append(build_operation('add_parent', str(term), after={'value': value}))
        for value in sorted(set(old_parents) - set(new_parents)):
            operations.append(build_operation('remove_parent', str(term), before={'value': value}))
        for predicate, suffix in ((RDFS.domain, 'domain'), (RDFS.range, 'range')):
            old = sorted(str(value) for value in _constraint_values(base, term, predicate))
            new = sorted(str(value) for value in _constraint_values(edited, term, predicate))
            if old == new:
                continue
            if kind == 'attribute' and suffix == 'range' and len(new) <= 1:
                operations.append(build_operation(
                    'set_datatype', str(term), before={'datatype': old[0] if old else None},
                    after={'datatype': new[0] if new else None}))
            else:
                operations.append(build_operation(
                    f'replace_{suffix}', str(term), before={'values': old},
                    after={'values': new}))

    if operations:
        candidate = apply_operations(base_turtle, operations)
        if isomorphic(_graph(candidate), edited):
            return operations
    return [build_operation(
        'advanced_rdf_patch', 'urn:knowledge:ontology:graph',
        after={'turtle': edited_turtle})]


# Clear, discoverable aliases for callers that prefer verb phrases.
compile_operation = build_operation
diff_turtle = canonical_turtle_diff
batch_eligible = is_batch_eligible

