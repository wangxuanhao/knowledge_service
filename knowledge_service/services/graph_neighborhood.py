"""Present bounded neighborhoods without reopening a project-wide graph read."""
from .service import public


def present_neighborhood(service, reads, project, scope, result, seeds, *, attribute_limit=200):
    # Properties are loaded by the shared entity sidebar after selection. This
    # response and its budgets describe only the real relationship network.
    records = [row for row in result['records'] if row['kind'] in {'entity', 'relation'}]
    labels = service.ontology_labels(project)
    service.annotate_structure_pending(project, records)
    records = [public(row) for row in records]
    families = {}
    for row in records:
        record_type = row.get('type')
        row['type_label'] = labels.get(record_type, record_type or '')
        if row['kind'] == 'entity':
            key = row.get('ontology_id') or None, record_type
            if key not in families:
                families[key] = service.record_family(project, row)
            family = families[key]
            row.update(class_label=family.get('label') or row['type_label'],
                class_parents=family.get('parents') or [], class_ancestors=family.get('ancestors') or [],
                class_ontology_id=family.get('ontology_id'))
    return {**result, 'records': records, 'attribute_mode': 'summary',
        'attribute_display': 'sidebar', 'type_labels': labels,
        'attribute_budget': 0, 'attribute_count': 0,
        'attributes_truncated': False, 'attribute_candidates_loaded': False,
        'budget_semantics': 'entities_relations_only'}
