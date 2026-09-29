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
from rdflib.compare import isomorphic, to_canonical_graph
from rdflib.namespace import Namespace, OWL, SH, XSD

from .ontology import Ontology, term_kind
from .ontology_iri import valid_application_iri


DCTERMS = Namespace('http://purl.org/dc/terms/')

_DECLARATIONS = {
    'class': OWL.Class,
    'relation': OWL.ObjectProperty,
    'attribute': OWL.DatatypeProperty,
}
_CLASS_DECLARATIONS = frozenset({OWL.Class, RDFS.Class})
_STRUCTURAL_PREDICATES = {
    RDF.type, RDFS.subClassOf, RDFS.domain, RDFS.range, OWL.deprecated,
    DCTERMS.isReplacedBy,
}
_HIGH_ACTIONS = {
    'remove_parent', 'remove_domain', 'remove_range', 'set_datatype',
    'retire_term', 'restore_term', 'advanced_rdf_patch',
}
_MEDIUM_ACTIONS = {'add_parent', 'add_domain', 'add_range'}
_NON_MANUAL_SOURCES = {
    'discovery', 'ai', 'import', 'candidate', 'semantic_suggestion',
    'candidate_semantic_suggestion',
}
RESTORE_TEMPLATE_FIELDS = frozenset({
    'annotations', 'parents', 'domain', 'range', 'datatype',
})
_SELECTABLE_TEMPLATE_FIELDS = RESTORE_TEMPLATE_FIELDS
_ACTION_ALIASES = {'retire': 'retire_term', 'restore': 'restore_term'}
_SUPPORTED_DATATYPES = {
    XSD.string, XSD.boolean, XSD.integer, XSD.decimal, XSD.float, XSD.double,
    XSD.date, XSD.dateTime, RDFS.Literal,
}
_RDF_PAYLOAD_KEYS = {'turtle': 'turtle', 'canonical_ntriples': 'nt', 'subgraph': 'turtle'}


def _is_annotation_predicate(predicate) -> bool:
    return (predicate not in _STRUCTURAL_PREDICATES
            and not str(predicate).startswith(str(SH)))


def _is_graph_class(graph: Graph, node) -> bool:
    return any((node, RDF.type, declaration) in graph
               for declaration in _CLASS_DECLARATIONS)


def _graph_classes(graph: Graph) -> set:
    return {
        subject
        for declaration in _CLASS_DECLARATIONS
        for subject in graph.subjects(RDF.type, declaration)
    }


def _graph_term_kind(graph: Graph, node) -> str | None:
    if _is_graph_class(graph, node):
        return 'class'
    if (node, RDF.type, OWL.ObjectProperty) in graph:
        return 'relation'
    if (node, RDF.type, OWL.DatatypeProperty) in graph:
        return 'attribute'
    return None


def _canonical_json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(',', ':'))


def _canonical_rdf(value: str, rdf_format: str = 'turtle') -> str:
    graph = Graph().parse(data=value, format=rdf_format)
    serialized = to_canonical_graph(graph).serialize(format='nt')
    return ''.join(sorted(line for line in serialized.splitlines(True) if line.strip()))


def _canonical_fingerprint_value(value, key=None):
    if isinstance(value, dict):
        return {name: _canonical_fingerprint_value(item, name)
                for name, item in value.items()}
    if isinstance(value, list):
        return [_canonical_fingerprint_value(item) for item in value]
    if isinstance(value, str) and key in _RDF_PAYLOAD_KEYS:
        return {'canonical_rdf': _canonical_rdf(value, _RDF_PAYLOAD_KEYS[key])}
    return value


def operation_fingerprint(operation: dict) -> str:
    """Return a stable SHA-256 over the semantic operation payload."""
    if not isinstance(operation, dict):
        raise ValueError('本体操作必须是对象')
    payload = {key: value for key, value in operation.items()
               if key not in {'fingerprint', 'id', 'created_at', 'source', 'confidence'}}
    return hashlib.sha256(
        _canonical_json(_canonical_fingerprint_value(payload)).encode('utf-8')).hexdigest()


def operation_risk(action: str, *, source: str = 'manual', impact=None,
                   warnings=None, confidence=None) -> str:
    """Apply the fixed server-side risk floor; confidence can never lower it."""
    action = _ACTION_ALIASES.get(action, action)
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


def _requires_ontology_context(action: str, before=None, after=None) -> bool:
    action = _ACTION_ALIASES.get(action, action)
    if action not in {'add_annotation', 'remove_annotation'}:
        return False
    spec = (after if action == 'add_annotation' else before) or {}
    return spec.get('type') == 'iri'


def is_batch_eligible(operation: dict, *,
                      ontology: Ontology | str | None = None) -> bool:
    """Only low-risk operations may enter an unattended batch."""
    warnings = operation.get('warnings')
    if warnings is None:
        warnings = (operation.get('validation') or {}).get('warnings', [])
    validation = operation.get('validation') or {}
    if _requires_ontology_context(
            operation.get('action', ''), operation.get('before'), operation.get('after')):
        if ontology is None:
            return False
        warnings = [*warnings, *operation_dependency_warnings(
            ontology, operation.get('action', ''), operation.get('target_iri', ''),
            before=operation.get('before'), after=operation.get('after'))]
    floor = operation_risk(
        operation.get('action', ''),
        source=operation.get('source', validation.get('source', 'manual')),
        impact=operation.get('impact'), warnings=warnings,
        confidence=operation.get('confidence', validation.get('confidence')))
    return operation.get('risk') == 'low' and floor == 'low'


def build_operation(action: str, target_iri: str, *, before=None, after=None,
                    source: str = 'manual', impact=None, warnings=None,
                    confidence=None, evidence=None, validation=None,
                    reason=None, ontology: Ontology | str | None = None) -> dict:
    if not isinstance(action, str) or not action:
        raise ValueError('本体操作 action 不能为空')
    action = _ACTION_ALIASES.get(action, action)
    if not isinstance(target_iri, str) or not valid_application_iri(target_iri):
        raise ValueError('本体操作 target_iri 必须是绝对 IRI')
    impact = dict(impact or {})
    warnings = list(warnings or [])
    derived = operation_dependency_warnings(
        ontology, action, target_iri, before=before, after=after)
    existing = {_canonical_json(issue) for issue in warnings}
    warnings.extend(
        issue for issue in derived
        if _canonical_json(issue) not in existing)
    operation_validation = dict(validation or {})
    if _requires_ontology_context(action, before, after):
        operation_validation['ontology_context_validated'] = ontology is not None
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
            **operation_validation, 'warnings': warnings,
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
        if not _is_annotation_predicate(predicate):
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
    if (datatype
            and (not isinstance(datatype, str)
                 or not valid_application_iri(datatype))):
        raise ValueError('annotation datatype 必须是绝对 IRI')
    return Literal(
        spec.get('value', ''), lang=language,
        datatype=URIRef(datatype) if datatype else None)


def _require_absolute_iri(value, label: str) -> str:
    if not isinstance(value, str) or not valid_application_iri(value):
        raise ValueError(f'{label} 必须是绝对 IRI')
    return value


def _restore_iri_values(template: dict, field: str) -> list[str]:
    values = template.get(field, [])
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError(
            f'restore template {field} requires a collection of absolute IRIs')
    return [
        _require_absolute_iri(value, f'restore template {field} absolute IRI')
        for value in values
    ]


def _apply_template(graph: Graph, node: URIRef, template: dict,
                    selected_fields: Iterable[str] | None = None) -> None:
    fields = set(selected_fields or _SELECTABLE_TEMPLATE_FIELDS)
    prepared_iris = {
        field: _restore_iri_values(template, field)
        for field in ('parents', 'domain', 'range')
        if field in fields
    }
    datatype = template.get('datatype') if 'datatype' in fields else None
    if datatype is not None:
        datatype = _require_absolute_iri(
            datatype, 'restore template datatype absolute IRI')
    if 'annotations' in fields:
        prepared_annotations = []
        for annotation in template.get('annotations', []):
            predicate = annotation.get('predicate')
            if (not isinstance(predicate, str)
                    or not valid_application_iri(predicate)):
                raise ValueError('annotation predicate 必须是绝对 IRI')
            if not _is_annotation_predicate(URIRef(predicate)):
                raise ValueError(
                    'restore annotation template 不能包含结构或 SHACL predicate')
            if annotation.get('type') == 'iri':
                annotation_value = annotation.get('value')
                if (not isinstance(annotation_value, str)
                        or not valid_application_iri(annotation_value)):
                    raise ValueError('annotation IRI value 必须是绝对 IRI')
                value = URIRef(annotation_value)
            else:
                value = _literal(annotation)
            prepared_annotations.append((URIRef(predicate), value))
        for predicate, value in list(graph.predicate_objects(node)):
            if _is_annotation_predicate(predicate):
                graph.remove((node, predicate, value))
        for predicate, value in prepared_annotations:
            graph.add((node, predicate, value))
    if 'parents' in fields:
        graph.remove((node, RDFS.subClassOf, None))
        for parent in prepared_iris['parents']:
            graph.add((node, RDFS.subClassOf, URIRef(parent)))
    if 'domain' in fields:
        _set_constraint(graph, node, RDFS.domain, prepared_iris['domain'])
    if 'range' in fields:
        _set_constraint(graph, node, RDFS.range, prepared_iris['range'])
    if 'datatype' in fields and template.get('kind') == 'attribute':
        _set_constraint(graph, node, RDFS.range, [datatype] if datatype else [])


def _ensure_kind(graph: Graph, node: URIRef, kind: str | None = None) -> str:
    actual = _graph_term_kind(graph, node)
    if actual is None:
        raise ValueError(f'本体术语不存在：{node}')
    if kind is not None and actual != kind:
        raise ValueError('本体术语类型不一致')
    return actual


def _is_active_graph_term(graph: Graph, node: URIRef) -> bool:
    return not any(
        _truthy_literal(marker)
        for marker in graph.objects(node, OWL.deprecated))


def _assert_no_parent_cycle(graph: Graph, child: URIRef, parent: URIRef) -> None:
    if child == parent:
        raise ValueError('术语不能继承自身，否则会形成循环（cycle）')
    if not _is_graph_class(graph, parent):
        raise ValueError(f'父类不存在：{parent}')
    pending = [parent]
    seen = set()
    while pending:
        current = pending.pop()
        if current == child:
            raise ValueError('父类关系会形成多跳循环（cycle）')
        if current in seen:
            continue
        seen.add(current)
        pending.extend(graph.objects(current, RDFS.subClassOf))


def _shape_subgraph(graph: Graph, root: URIRef) -> Graph:
    fragment = Graph()
    pending = [(root, True)]
    seen = set()
    while pending:
        subject, is_root = pending.pop()
        if subject in seen:
            continue
        seen.add(subject)
        for triple in graph.triples((subject, None, None)):
            if is_root and not (
                    str(triple[1]).startswith(str(SH))
                    or (triple[1] == RDF.type
                        and triple[2] in {SH.NodeShape, SH.PropertyShape})):
                continue
            fragment.add(triple)
            if isinstance(triple[2], BNode):
                pending.append((triple[2], False))
    return fragment


def _remove_shape_subgraph(graph: Graph, root: URIRef) -> None:
    fragment = _shape_subgraph(graph, root)
    for triple in fragment:
        graph.remove(triple)


def _apply_scoped_shacl_patch(graph: Graph, target: URIRef, before: dict,
                              after: dict) -> None:
    if not (after.get('canonical_ntriples') is not None
            and before.get('canonical_ntriples') is not None):
        raise ValueError('advanced RDF patch 必须携带规范化的 SHACL 子图')
    expected = Graph().parse(data=before['canonical_ntriples'], format='nt')
    if not isomorphic(_shape_subgraph(graph, target), expected):
        raise ValueError('advanced RDF patch 的 SHACL before 子图已变更')
    fragment = Graph().parse(data=after['canonical_ntriples'], format='nt')
    subjects = {target}
    pending = [target]
    while pending:
        subject = pending.pop()
        for value in fragment.objects(subject, None):
            if isinstance(value, BNode) and value not in subjects:
                subjects.add(value)
                pending.append(value)
    if any(subject not in subjects for subject in set(fragment.subjects())):
        raise ValueError('advanced RDF patch 只能包含目标 SHACL shape 子图')
    for subject, predicate, value in fragment:
        allowed = (
            str(predicate).startswith(str(SH))
            or (predicate == RDF.type and value in {SH.NodeShape, SH.PropertyShape})
            or (isinstance(subject, BNode) and predicate in {RDF.first, RDF.rest})
        )
        if not allowed:
            raise ValueError('advanced RDF patch 包含与目标 SHACL shape 无关的三元组')
    if any(predicate in {OWL.deprecated, DCTERMS.isReplacedBy}
           or (predicate == RDF.type and value in _DECLARATIONS.values())
           for _, predicate, value in fragment):
        raise ValueError('advanced RDF patch 不允许改变本体生命周期或术语类型')
    _remove_shape_subgraph(graph, target)
    for triple in fragment:
        graph.add(triple)


def _validate_definition_graph(graph: Graph) -> None:
    class_nodes = _graph_classes(graph)
    hierarchy_state = {}

    def validate_hierarchy(node) -> None:
        state = hierarchy_state.get(node)
        if state == 'visiting':
            raise ValueError(f'类继承会形成循环（cycle）：{node}')
        if state == 'validated':
            return
        hierarchy_state[node] = 'visiting'
        for parent in graph.objects(node, RDFS.subClassOf):
            if parent not in class_nodes:
                raise ValueError(f'父类不存在：{parent}')
            validate_hierarchy(parent)
        hierarchy_state[node] = 'validated'

    for class_node in class_nodes:
        validate_hierarchy(class_node)

    ontology = Ontology(graph.serialize(format='turtle'))
    constraint_owners = ontology.relations | ontology.attributes
    for predicate in (RDFS.domain, RDFS.range):
        for owner in graph.subjects(predicate, None):
            if owner not in constraint_owners:
                raise ValueError(
                    f'{predicate.split("#")[-1]} 只能附加到 relation/attribute 术语：{owner}')
    for owner in constraint_owners:
        kind = term_kind(ontology, owner)
        for value in ontology.constraint_types(owner, RDFS.domain):
            if value not in ontology.classes:
                raise ValueError(f'domain 类不存在：{value}')
        range_values = ontology.constraint_types(owner, RDFS.range)
        if kind == 'attribute' and len(range_values) > 1:
            raise ValueError(
                f'DatatypeProperty 只能声明一个 datatype：{owner}')
        for value in range_values:
            if kind == 'relation' and value not in ontology.classes:
                raise ValueError(f'range 类不存在：{value}')
            if kind == 'attribute' and value not in _SUPPORTED_DATATYPES:
                raise ValueError(f'不支持的 datatype 数据类型：{value}')
    for subject, target_class in graph.subject_objects(SH.targetClass):
        if (_shape_deactivated(graph, subject)
                or target_class in ontology.classes):
            continue
        raise ValueError(f'SHACL targetClass 必须引用已声明的 class：{target_class}')
    declared_properties = ontology.relations | ontology.attributes
    for subject, path in graph.subject_objects(SH.path):
        if (_shape_deactivated(graph, subject) or isinstance(path, BNode)):
            continue
        if path not in declared_properties:
            raise ValueError(f'SHACL path 必须引用已声明的 relation/attribute：{path}')
    report = validate_ontology_invariants(ontology)
    if not report['conforms']:
        raise ValueError(
            '活动术语不能依赖已停用术语（active dependency）：'
            + _canonical_json(report['errors']))


def apply_operations(base_turtle: str, operations: Iterable[dict]) -> str:
    """The only mutation entry point: apply ordered operations to one RDF graph."""
    graph = _graph(base_turtle)
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError('本体操作必须是对象')
        fingerprint = operation.get('fingerprint')
        if fingerprint and fingerprint != operation_fingerprint(operation):
            raise ValueError('本体操作 fingerprint 指纹与内容不一致')
        action = _ACTION_ALIASES.get(operation.get('action'), operation.get('action'))
        target_iri = operation.get('target_iri', '')
        if (not isinstance(target_iri, str)
                or not valid_application_iri(target_iri)):
            raise ValueError('本体操作 target_iri 必须是绝对 IRI')
        target = URIRef(target_iri)
        before = operation.get('before') or {}
        after = operation.get('after') or {}
        if action == 'create_term':
            kind = after.get('kind')
            if kind not in _DECLARATIONS:
                raise ValueError('不支持的本体术语类型')
            if _graph_term_kind(graph, target) is not None:
                raise ValueError('本体术语已存在')
            if set(after) - {'kind'}:
                raise ValueError('create_term 只声明术语身份；annotation 和结构边必须使用独立操作')
            graph.add((target, RDF.type, _DECLARATIONS[kind]))
        elif action in {'add_parent', 'remove_parent'}:
            _ensure_kind(graph, target, 'class')
            value_iri = (after if action == 'add_parent' else before).get('value')
            _require_absolute_iri(value_iri, 'parent value')
            value = URIRef(value_iri)
            if action == 'add_parent':
                if (target, RDFS.subClassOf, value) in graph:
                    raise ValueError('父类不能重复')
                _assert_no_parent_cycle(graph, target, value)
                graph.add((target, RDFS.subClassOf, value))
            else:
                if (target, RDFS.subClassOf, value) not in graph:
                    raise ValueError('remove_parent before 冲突：被审阅的父类边不存在或已变更')
                graph.remove((target, RDFS.subClassOf, value))
        elif action in {'add_domain', 'remove_domain', 'add_range', 'remove_range'}:
            kind = _ensure_kind(graph, target)
            if kind not in {'relation', 'attribute'}:
                raise ValueError('domain/range 只能用于 relation/attribute（关系/属性）术语')
            if kind == 'attribute' and action.endswith('range'):
                raise ValueError('DatatypeProperty 数据类型必须使用 set_datatype 修改')
            predicate = RDFS.domain if action.endswith('domain') else RDFS.range
            values = _constraint_values(graph, target, predicate)
            value_iri = (after if action.startswith('add_') else before).get('value')
            _require_absolute_iri(value_iri, f'{action} value')
            value = URIRef(value_iri)
            if action.startswith('add_'):
                if value in values:
                    raise ValueError('约束值不能重复')
                if predicate == RDFS.domain or kind == 'relation':
                    if not _is_graph_class(graph, value):
                        raise ValueError(f'domain/range 类不存在：{value}')
                elif value not in _SUPPORTED_DATATYPES:
                    raise ValueError(f'不支持的 datatype 数据类型：{value}')
                values.append(value)
            else:
                if value not in values:
                    raise ValueError(f'{action} before 冲突：被审阅的约束不存在或已变更')
                values = [item for item in values if item != value]
            if predicate == RDFS.domain or kind == 'relation':
                for value in values:
                    if not _is_graph_class(graph, URIRef(str(value))):
                        raise ValueError(f'domain/range 类不存在：{value}')
            _set_constraint(graph, target, predicate, values)
        elif action == 'set_datatype':
            _ensure_kind(graph, target, 'attribute')
            if 'datatype' not in after:
                raise ValueError('set_datatype 必须携带 after datatype')
            datatype = after['datatype']
            if datatype is not None:
                _require_absolute_iri(datatype, 'datatype')
            if datatype and URIRef(datatype) not in _SUPPORTED_DATATYPES:
                raise ValueError(f'不支持的 datatype 数据类型：{datatype}')
            if 'datatype' not in before:
                raise ValueError('set_datatype 缺少被审阅的 before datatype')
            reviewed_datatype = before['datatype']
            if reviewed_datatype is not None:
                _require_absolute_iri(reviewed_datatype, 'before datatype')
            current_values = _constraint_values(graph, target, RDFS.range)
            current = str(current_values[0]) if len(current_values) == 1 else None
            if len(current_values) > 1 or reviewed_datatype != current:
                raise ValueError('set_datatype before 冲突：当前数据类型已变更')
            _set_constraint(graph, target, RDFS.range, [datatype] if datatype else [])
        elif action in {'add_annotation', 'remove_annotation'}:
            _ensure_kind(graph, target)
            spec = after if action == 'add_annotation' else before
            predicate_iri = _require_absolute_iri(
                spec.get('predicate'), 'annotation predicate')
            predicate = URIRef(predicate_iri)
            if predicate in _STRUCTURAL_PREDICATES - {DCTERMS.isReplacedBy}:
                raise ValueError('结构谓词不能作为普通 annotation 修改')
            if (predicate != DCTERMS.isReplacedBy
                    and not _is_annotation_predicate(predicate)):
                raise ValueError('SHACL predicate 必须使用 scoped shape patch 修改')
            if spec.get('type') == 'iri':
                value_iri = _require_absolute_iri(
                    spec.get('value'), 'annotation IRI value')
                value = URIRef(value_iri)
            else:
                value = _literal(spec)
            if action == 'add_annotation':
                if predicate == DCTERMS.isReplacedBy:
                    if not isinstance(value, URIRef):
                        raise ValueError('isReplacedBy 必须使用 IRI')
                    source_kind = _ensure_kind(graph, target)
                    replacement_kind = _ensure_kind(graph, value)
                    if value == target or replacement_kind != source_kind:
                        raise ValueError('isReplacedBy 必须指向同类型的其他术语')
                    if not _is_active_graph_term(graph, value):
                        raise ValueError('isReplacedBy 不能指向已停用术语')
                graph.add((target, predicate, value))
            else:
                if (target, predicate, value) not in graph:
                    raise ValueError(
                        'remove_annotation before 冲突：被审阅的 annotation 不存在或已变更')
                graph.remove((target, predicate, value))
        elif action == 'retire_term':
            kind = _ensure_kind(graph, target)
            graph.set((target, OWL.deprecated, Literal(True)))
            replacement = after.get('replacement')
            if replacement:
                iri = replacement.get('iri') if isinstance(replacement, dict) else replacement
                if not iri or not valid_application_iri(iri):
                    raise ValueError('isReplacedBy replacement 必须包含绝对 IRI')
                candidate = URIRef(iri)
                replacement_kind = _ensure_kind(graph, candidate)
                if candidate == target or replacement_kind != kind:
                    raise ValueError('isReplacedBy 必须指向同类型的其他活动术语')
                if not _is_active_graph_term(graph, candidate):
                    raise ValueError('isReplacedBy 不能指向已停用术语')
                graph.set((target, DCTERMS.isReplacedBy, URIRef(iri)))
        elif action == 'restore_term':
            if not after.get('source_ontology_id') or not isinstance(after.get('template'), dict):
                raise ValueError('restore_term 必须携带 source_ontology_id 和不可变 template')
            selected = list(after.get('selected_fields') or [])
            if (not selected or len(selected) != len(set(selected))
                    or set(selected) - RESTORE_TEMPLATE_FIELDS):
                raise ValueError('restore_term 必须显式选择有效且不重复的定义字段')
            missing = [field for field in selected if field not in after['template']]
            if missing:
                raise ValueError(
                    'restore_term template 缺少显式选择的字段：' + ', '.join(missing))
            if _is_active_graph_term(graph, target):
                raise ValueError('restore_term 的当前术语必须已停用（deprecated）')
            _ensure_kind(graph, target, after['template'].get('kind'))
            if not after.get('activation_only'):
                _apply_template(graph, target, after['template'], selected)
            graph.remove((target, OWL.deprecated, None))
        elif action == 'advanced_rdf_patch':
            if 'turtle' in after:
                raise ValueError('advanced RDF patch 不允许携带或替换整个 Turtle 图')
            _apply_scoped_shacl_patch(graph, target, before, after)
        else:
            raise ValueError(f'不支持的本体操作：{action}')
    _validate_definition_graph(graph)
    return graph.serialize(format='turtle')


def _issue(code: str, severity: str, message: str, term_iris,
           operation_ids=None) -> dict:
    return {
        'code': code,
        'severity': severity,
        'message': message,
        'operation_ids': list(operation_ids or []),
        'term_iris': [str(term) for term in term_iris],
    }


def operation_dependency_warnings(ontology: Ontology | str | None, action: str,
                                  target_iri: str, *, before=None,
                                  after=None) -> list[dict]:
    """Derive server-owned warnings for one structured operation."""
    action = _ACTION_ALIASES.get(action, action)
    if action not in {'add_annotation', 'remove_annotation'}:
        return []
    spec = (after if action == 'add_annotation' else before) or {}
    if spec.get('type') != 'iri':
        return []
    if ontology is None:
        return [_issue(
            'ontology_context_required', 'warning',
            'IRI annotation 风险分类需要本体上下文重新验证',
            [target_iri, spec.get('value', '')])]
    if isinstance(ontology, str):
        ontology = Ontology(ontology)
    if action == 'remove_annotation':
        return []
    predicate = URIRef(spec.get('predicate', ''))
    if predicate in _STRUCTURAL_PREDICATES or str(predicate).startswith(str(SH)):
        return []
    source = URIRef(target_iri)
    referenced = URIRef(spec.get('value', ''))
    declared = ontology.classes | ontology.relations | ontology.attributes
    if (source not in declared or referenced not in declared
            or not ontology.is_active_term(source)
            or ontology.is_active_term(referenced)):
        return []
    return [_issue(
        'active_custom_annotation_dependency', 'warning',
        f'活动术语通过自定义 annotation {predicate} 引用已停用术语',
        [source, referenced])]


def _owning_shape(graph: Graph, node) -> URIRef | BNode:
    pending = [node]
    seen = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        if (current, RDF.type, SH.NodeShape) in graph:
            return current
        pending.extend(graph.subjects(SH.property, current))
    return node


def _truthy_literal(value) -> bool:
    if not isinstance(value, Literal):
        return False
    converted = value.toPython()
    return converted is True or str(converted).strip().lower() in {'true', '1'}


def _shape_deactivated(graph: Graph, node) -> bool:
    owner = _owning_shape(graph, node)
    candidates = {node, owner}
    return any(
        _truthy_literal(value)
        for candidate in candidates
        for value in graph.objects(candidate, SH.deactivated)
    )


def retirement_dependencies(ontology: Ontology, target_iri: str, *,
                            active_records=None, historical_records=None,
                            operation_ids=None) -> dict:
    """Return the fixed dependency matrix used by retirement previews."""
    target = ontology.resolve(target_iri)
    errors = []
    deactivated_info = []
    for child in ontology.graph.subjects(RDFS.subClassOf, target):
        if ontology.is_active_term(child):
            errors.append(_issue(
                'active_child_dependency', 'error',
                '活动子类依赖将被停用的父类', [child, target], operation_ids))
    for predicate, kind in ((RDFS.domain, 'active_domain'), (RDFS.range, 'active_range')):
        for owner in sorted(ontology.relations | ontology.attributes, key=str):
            if (ontology.is_active_term(owner)
                    and target in ontology.constraint_types(owner, predicate)):
                errors.append(_issue(
                    f'{kind}_dependency', 'error',
                    '活动 domain/range 约束依赖将被停用的术语',
                    [owner, target], operation_ids))
    for subject in ontology.graph.subjects(SH.targetClass, target):
        if _shape_deactivated(ontology.graph, subject):
            deactivated_info.append(_issue(
                'deactivated_shacl_dependency', 'info',
                '已停用的 SHACL shape 引用该术语，不阻止停用',
                [_owning_shape(ontology.graph, subject), target], operation_ids))
            continue
        errors.append(_issue(
            'active_shacl_target_class_dependency', 'error',
            'SHACL targetClass 依赖将被停用的类',
            [subject, target], operation_ids))
    for subject in ontology.graph.subjects(SH.path, target):
        owner = _owning_shape(ontology.graph, subject)
        if _shape_deactivated(ontology.graph, subject):
            deactivated_info.append(_issue(
                'deactivated_shacl_dependency', 'info',
                '已停用的 SHACL shape 引用该术语，不阻止停用',
                [owner, target], operation_ids))
            continue
        errors.append(_issue(
            'active_shacl_path_dependency', 'error',
            'SHACL path 依赖将被停用的属性',
            [owner, target], operation_ids))
    for subject in ontology.graph.subjects(DCTERMS.isReplacedBy, target):
        if ontology.is_active_term(subject):
            errors.append(_issue(
                'active_replacement_dependency', 'error',
                '活动术语的替换关系指向将被停用的术语',
                [subject, target], operation_ids))
    warnings = []
    excluded = _STRUCTURAL_PREDICATES | {SH.targetClass, SH.path, SH.property}
    for subject, predicate in ontology.graph.subject_predicates(target):
        if (isinstance(subject, URIRef) and subject != target
                and subject in (ontology.classes | ontology.relations | ontology.attributes)
                and ontology.is_active_term(subject) and predicate not in excluded):
            warnings.append(_issue(
                'active_custom_annotation_dependency', 'warning',
                f'活动术语通过自定义 annotation {predicate} 引用将被停用的术语',
                [subject, target], operation_ids))
    info = [*deactivated_info, _issue(
        'deprecated_term_structure_retained', 'info',
        '停用仅增加 owl:deprecated；术语自身声明、标签和结构将保留',
        [target], operation_ids)]
    return {
        'errors': errors,
        'warnings': warnings,
        'info': info,
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
                errors.append(_issue(
                    'active_child_dependency', 'error',
                    '活动子类依赖已停用父类', [child, parent]))
    for owner in sorted(ontology.relations | ontology.attributes, key=str):
        if not ontology.is_active_term(owner):
            continue
        for predicate, kind in ((RDFS.domain, 'active_domain'),
                                (RDFS.range, 'active_range')):
            for value in ontology.constraint_types(owner, predicate):
                if value in declared and not ontology.is_active_term(value):
                    errors.append(_issue(
                        f'{kind}_dependency', 'error',
                        '活动约束依赖已停用术语', [owner, value]))
    for subject, predicate, value in ontology.graph:
        if (predicate in {SH.targetClass, SH.path} and value in declared
                and not ontology.is_active_term(value)
                and not _shape_deactivated(ontology.graph, subject)):
            code = ('active_shacl_target_class_dependency'
                    if predicate == SH.targetClass else 'active_shacl_path_dependency')
            errors.append(_issue(
                code, 'error', 'SHACL 约束依赖已停用术语',
                [_owning_shape(ontology.graph, subject), value]))
        if (predicate == DCTERMS.isReplacedBy and subject in declared
                and ontology.is_active_term(subject) and value in declared
                and not ontology.is_active_term(value)):
            errors.append(_issue(
                'active_replacement_dependency', 'error',
                '活动替换关系依赖已停用术语', [subject, value]))
    return {'conforms': not errors, 'errors': errors}


def build_restore_operation(repository, project_id: str, target_iri: str,
                            source_ontology_id: str | None, *,
                            selected_fields: Iterable[str],
                            impact_provider=None) -> dict:
    """Freeze an explicitly selected definition from a same-project version."""
    if not source_ontology_id:
        raise ValueError('restore_term 需要 source_ontology_id')
    selected = list(selected_fields or [])
    if (not selected or len(selected) != len(set(selected))
            or set(selected) - _SELECTABLE_TEMPLATE_FIELDS):
        raise ValueError('restore_term 必须显式选择有效且不重复的定义字段')
    try:
        source = repository.get_ontology(project_id, source_ontology_id)
    except KeyError as exc:
        raise ValueError('source_ontology_id 不属于此项目或不存在') from exc
    template = _term_template(Ontology(source['turtle']), target_iri)
    captured = {'kind': template['kind']}
    for field in selected:
        if field not in template:
            raise ValueError(
                f'restore_term 的冻结 template 缺少所选字段：{field}')
        captured[field] = template[field]
    after = {
        'source_ontology_id': source_ontology_id,
        'selected_fields': selected,
        'template': captured,
    }
    current_ontology = Ontology(repository.get_ontology(project_id)['turtle'])
    current_node = current_ontology.resolve(target_iri)
    if current_ontology.is_active_term(current_node):
        raise ValueError('restore_term 的当前术语必须已停用（deprecated）')
    current_template = _term_template(current_ontology, target_iri)
    for field in selected:
        current_template[field] = template[field]
    descendants = set()
    pending = [current_node]
    while pending:
        parent = pending.pop()
        for child in current_ontology.graph.subjects(RDFS.subClassOf, parent):
            if child not in descendants:
                descendants.add(child)
                pending.append(child)
    constraint_values = (
        current_template.get('parents', [])
        + current_template.get('domain', [])
        + current_template.get('range', []))
    impact = {
        'preview': {**current_template, 'active': True},
        'reactivated_constraints': len(constraint_values),
        'reactivated_descendants': len(descendants),
    }
    if impact_provider is not None:
        provided = impact_provider(project_id, target_iri)
        if not isinstance(provided, dict):
            raise ValueError('restore_term impact_provider 必须返回对象')
        impact.update(provided)
    return build_operation('restore_term', target_iri, after=after, impact=impact)


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
    anonymous_classes = {
        node for node in _graph_classes(graph) if isinstance(node, BNode)
    }
    allowed.update(anonymous_classes)
    class_pending = list(anonymous_classes)
    while class_pending:
        node = class_pending.pop()
        for parent in graph.objects(node, RDFS.subClassOf):
            if (isinstance(parent, BNode) and _is_graph_class(graph, parent)
                    and parent not in allowed):
                allowed.add(parent)
                class_pending.append(parent)
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
    for declaration in _CLASS_DECLARATIONS:
        for subject in graph.subjects(RDF.type, declaration):
            if isinstance(subject, URIRef):
                found[subject] = 'class'
    for kind in ('relation', 'attribute'):
        declaration = _DECLARATIONS[kind]
        for subject in graph.subjects(RDF.type, declaration):
            if isinstance(subject, URIRef) and subject not in found:
                found[subject] = kind
    return found


def _annotation_spec(predicate, value) -> dict:
    spec = {'predicate': str(predicate), 'value': str(value)}
    if isinstance(value, URIRef):
        spec['type'] = 'iri'
    elif isinstance(value, Literal):
        spec['language'] = value.language
        spec['datatype'] = str(value.datatype) if value.datatype else None
    return spec


def _term_annotations(graph: Graph, term: URIRef) -> set[tuple]:
    annotations = set()
    for predicate, value in graph.predicate_objects(term):
        if (predicate == DCTERMS.isReplacedBy
                or _is_annotation_predicate(predicate)):
            annotations.add((predicate, value))
        else:
            continue
    return annotations


def _ordered_new_terms(graph: Graph, added: set[URIRef]) -> list[URIRef]:
    remaining = set(added)
    ordered = []
    while remaining:
        ready = []
        for term in remaining:
            dependencies = {
                parent for parent in graph.objects(term, RDFS.subClassOf)
                if parent in remaining
            }
            if not dependencies:
                ready.append(term)
        if not ready:
            raise ValueError('新增类声明包含循环（cycle）')
        ready.sort(key=str)
        for term in ready:
            ordered.append(term)
            remaining.remove(term)
    return ordered


def _new_term_impact(graph: Graph, term: URIRef) -> dict:
    """Compute create risk facts from the complete edited semantic graph."""
    has_child = any(True for _ in graph.subjects(RDFS.subClassOf, term))
    referenced = any(
        subject != term
        for subject, _, _ in graph.triples((None, None, term))
    )
    return {'leaf': not has_child, 'referenced': referenced}


def _deprecated(graph: Graph, term: URIRef) -> bool:
    return any(
        value.toPython() is True
        or str(value).strip().lower() in {'true', '1'}
        for value in graph.objects(term, OWL.deprecated))


def _shape_roots(graph: Graph) -> set[URIRef]:
    return {
        subject for shape_type in (SH.NodeShape, SH.PropertyShape)
        for subject in graph.subjects(RDF.type, shape_type)
        if isinstance(subject, URIRef)
    }


def canonical_turtle_diff(base_turtle: str, edited_turtle: str, *,
                          published: bool = True,
                          base_ontology_id: str | None = None,
                          source_ontology_id: str | None = None,
                          restore_operation_builder=None) -> list[dict]:
    """Compile a graph-semantic Turtle diff into reviewable atomic operations."""
    immutable_source_id = source_ontology_id
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
        raise ValueError('已发布术语声明不能物理删除；请使用 retire_term 停用操作')
    for term in set(base_declarations) & set(edited_declarations):
        if base_declarations[term] != edited_declarations[term]:
            raise ValueError('术语类型不能通过 Turtle diff 改变')
    operations = []
    added_terms = set(edited_declarations) - set(base_declarations)
    term_order = _ordered_new_terms(edited, added_terms)
    for term in term_order:
        operations.append(build_operation(
            'create_term', str(term), after={'kind': edited_declarations[term]},
            impact=_new_term_impact(edited, term)))

    restored_terms = set()
    for term in sorted(set(base_declarations) & set(edited_declarations), key=str):
        was_deprecated = _deprecated(base, term)
        now_deprecated = _deprecated(edited, term)
        if was_deprecated == now_deprecated:
            continue
        if now_deprecated:
            operations.append(build_operation('retire_term', str(term)))
        else:
            if not immutable_source_id:
                raise ValueError(
                    '移除 owl:deprecated 必须指定非空 source_ontology_id')
            if restore_operation_builder is None:
                raise ValueError(
                    '移除 owl:deprecated 需要不可变版本 resolver/builder 执行结构化恢复'
                    '（structured restore）')
            operation = restore_operation_builder(
                target_iri=str(term), source_ontology_id=immutable_source_id)
            operation_after = operation.get('after') if isinstance(operation, dict) else None
            if (not isinstance(operation, dict)
                    or operation.get('action') != 'restore_term'
                    or operation.get('target_iri') != str(term)
                    or not isinstance(operation_after, dict)):
                raise ValueError(
                    'restore_operation_builder 必须返回从不可变版本解析的完整 restore_term 操作')
            if operation_after.get('source_ontology_id') != immutable_source_id:
                raise ValueError(
                    'restore_operation_builder 返回的 source_ontology_id 必须与请求完全一致')
            if (not isinstance(operation_after.get('template'), dict)
                    or not isinstance(operation.get('impact'), dict)
                    or 'preview' not in operation['impact']):
                raise ValueError(
                    'restore_operation_builder 必须返回从不可变版本解析的完整 restore_term 操作')
            operations.append(operation)
            restored_terms.add(term)

    edited_ontology = Ontology(edited.serialize(format='turtle'))
    annotation_removals = []
    annotation_additions = []
    for term in [*term_order, *sorted(set(base_declarations) & set(edited_declarations), key=str)]:
        if term in restored_terms:
            continue
        old_annotations = _term_annotations(base, term) if term in base_declarations else set()
        new_annotations = _term_annotations(edited, term)
        for predicate, value in sorted(old_annotations - new_annotations,
                                       key=lambda item: _canonical_json(_annotation_spec(*item))):
            annotation_removals.append(build_operation(
                'remove_annotation', str(term), before=_annotation_spec(predicate, value),
                ontology=edited_ontology))
        for predicate, value in sorted(new_annotations - old_annotations,
                                       key=lambda item: _canonical_json(_annotation_spec(*item))):
            annotation_additions.append(build_operation(
                'add_annotation', str(term), after=_annotation_spec(predicate, value),
                ontology=edited_ontology))
    operations.extend(annotation_removals)
    operations.extend(annotation_additions)

    structural_terms = [
        *term_order,
        *sorted(set(base_declarations) & set(edited_declarations), key=str),
    ]
    for term in structural_terms:
        if term in restored_terms:
            continue
        kind = edited_declarations[term]
        old_parents = sorted(
            str(value) for value in base.objects(term, RDFS.subClassOf)
            if isinstance(value, URIRef))
        new_parents = sorted(
            str(value) for value in edited.objects(term, RDFS.subClassOf)
            if isinstance(value, URIRef))
        for value in sorted(set(new_parents) - set(old_parents)):
            operations.append(build_operation('add_parent', str(term), after={'value': value}))
        for value in sorted(set(old_parents) - set(new_parents)):
            operations.append(build_operation('remove_parent', str(term), before={'value': value}))
        for predicate, suffix in ((RDFS.domain, 'domain'), (RDFS.range, 'range')):
            old = sorted(str(value) for value in _constraint_values(base, term, predicate))
            new = sorted(str(value) for value in _constraint_values(edited, term, predicate))
            if old == new:
                continue
            if kind == 'attribute' and suffix == 'range':
                if len(new) > 1:
                    raise ValueError(
                        'DatatypeProperty 只能通过 set_datatype 使用一个受支持 datatype')
                operations.append(build_operation(
                    'set_datatype', str(term), before={'datatype': old[0] if old else None},
                    after={'datatype': new[0] if new else None}))
            else:
                for value in sorted(set(old) - set(new)):
                    operations.append(build_operation(
                        f'remove_{suffix}', str(term), before={'value': value}))
                for value in sorted(set(new) - set(old)):
                    operations.append(build_operation(
                        f'add_{suffix}', str(term), after={'value': value}))

    for root in sorted(_shape_roots(base) | _shape_roots(edited), key=str):
        old_fragment = _shape_subgraph(base, root)
        new_fragment = _shape_subgraph(edited, root)
        if isomorphic(old_fragment, new_fragment):
            continue
        operations.append(build_operation(
            'advanced_rdf_patch', str(root),
            before={'canonical_ntriples': _canonical_rdf(
                old_fragment.serialize(format='turtle'))},
            after={'canonical_ntriples': _canonical_rdf(
                new_fragment.serialize(format='turtle'))}))

    if not operations:
        raise ValueError('Turtle diff 包含不支持的 OWL/RDF 变更')
    candidate = apply_operations(base_turtle, operations)
    if not isomorphic(_graph(candidate), edited):
        raise ValueError('Turtle diff 包含无法安全编译的复杂 RDF 变更')
    return operations


# Clear, discoverable aliases for callers that prefer verb phrases.
compile_operation = build_operation
diff_turtle = canonical_turtle_diff
batch_eligible = is_batch_eligible
