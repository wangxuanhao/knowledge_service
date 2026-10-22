"""Present bounded neighborhoods without reopening a project-wide graph read."""
from collections import defaultdict
import hashlib
import json

from .service import public


def present_neighborhood(service, reads, project, scope, result, seeds, *, attribute_limit=200):
    records = list(result['records'])
    entities = [row for row in records if row['kind'] == 'entity']
    labels = service.ontology_labels(project)
    entity_ids = [row['id'] for row in entities]
    # A newly selected seed gets its attribute slice before other local nodes.
    subjects = [id for id in entity_ids if id in seeds] + [id for id in entity_ids if id not in seeds]
    attributes = reads.entity_attributes(project, scope, subjects, limit=attribute_limit)
    # Annotate the bounded set together so relation and attribute endpoint
    # warnings see the same local entities before public presentation strips data.
    service.annotate_structure_pending(project, [*records, *attributes['records']])
    records = [public(row) for row in records]
    grouped = defaultdict(dict)
    for row in attributes['records']:
        predicate = row['type']
        group = grouped[row['subject_id']].setdefault(predicate, {
            'predicate': predicate, 'type': predicate, 'label': labels.get(predicate, predicate), 'values': []})
        group['values'].append({**public(row), 'record_id': row['id'], 'predicate': predicate,
            'status': 'accepted', 'accepted_support_count_known': False})
    for row in records:
        row['type_label'] = labels.get(row.get('type'), row.get('type') or '')
        if row['kind'] == 'entity':
            family = service.record_family(project, row)
            row.update(class_label=family.get('label') or row['type_label'],
                class_parents=family.get('parents') or [], class_ancestors=family.get('ancestors') or [],
                class_ontology_id=family.get('ontology_id'), attributes=list(grouped[row['id']].values()),
                attributes_truncated=attributes['truncated'])
    used = {row['id'] for row in records}
    def virtual_id(kind, row):
        identity = json.dumps([row['subject_id'], row['id'], row.get('version_id')], separators=(',', ':'))
        base = '__ks_' + kind + '__:' + hashlib.sha256(identity.encode()).hexdigest()
        value, suffix = base, 0
        while value in used:
            suffix += 1
            value = base + ':' + str(suffix)
        used.add(value)
        return value
    for row in attributes['records']:
        if row['subject_id'] not in seeds:
            continue
        label = labels.get(row['type'], row['type'])
        node_id = virtual_id('attribute', row)
        records.append({**public(row), 'id': node_id, 'record_id': row['id'], 'kind': 'attribute_value',
            'type': 'KS_ATTRIBUTE_VALUE', 'virtual': True, 'status': 'accepted', 'predicate_label': label,
            'text': label + ': ' + json.dumps(row['value'], ensure_ascii=False)})
        records.append({'id': virtual_id('attribute_edge', row), 'kind': 'attribute_edge',
            'type': 'KS_ATTRIBUTE', 'type_label': label, 'text': label, 'virtual': True,
            'subject_id': row['subject_id'], 'object_id': node_id, 'predicate': row['type'], 'status': 'accepted'})
    return {**result, 'records': records, 'attribute_mode': 'expanded', 'type_labels': labels,
        'attribute_budget': attribute_limit, 'attribute_count': len(attributes['records']),
        'attributes_truncated': attributes['truncated'], 'attribute_candidates_loaded': False,
        'budget_semantics': 'entities_relations_plus_separate_attributes'}
