"""Resolve scoped entity mentions to explicit, published ontology classes."""
from __future__ import annotations

from .ontology import match_query_expansion


def expand_entity_query(index, query, entities, *, max_terms=8):
    """Combine literal class hits with confirmed entity types and descendants.

    ``entities`` must come from the request's authoritative scoped read. Type
    resolution is exact: an indexed class IRI, or one unambiguous class surface.
    Literal seeds retain their existing priority and expansion behavior.
    """
    if type(max_terms) is not int or not 1 <= max_terms <= 8:
        raise ValueError('本体扩展术语上限必须在1到8之间')
    literal = match_query_expansion(index, query, max_terms=max_terms)
    labels = index.get('labels', {})
    ancestors = index.get('ancestors', {})
    known = set(labels) | set(ancestors)
    surfaces = {}
    for surface, class_id in [*index.get('surfaces', ()), *[(value, key) for key, value in labels.items()]]:
        known.add(class_id)
        if isinstance(surface, str) and surface.strip():
            surfaces.setdefault(surface.strip().casefold(), set()).add(class_id)

    matches = []
    for row in entities:
        value = row.get('type')
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        candidates = {value} if value in known else surfaces.get(value.casefold(), set())
        if len(candidates) != 1:
            continue
        class_id = next(iter(candidates))
        matches.append({
            'id': row['id'], 'version_id': row['version_id'], 'text': row.get('text') or '',
            'matched_name': row['matched_name'], 'match_kind': row['match_kind'],
            'type': class_id, 'type_label': labels.get(class_id) or class_id,
        })
    matches.sort(key=lambda row: (-len(row['matched_name']), row['matched_name'].casefold(),
                                 row['matched_name'], row['match_kind'] != 'name',
                                 row['id'], row['version_id']))
    seen = set()
    unique = []
    for row in matches:
        if row['version_id'] not in seen:
            unique.append(row)
            seen.add(row['version_id'])
        if len(unique) == 8:
            break

    literal_terms = list(literal['terms'])
    specificity = {}
    for row in unique:
        specificity[row['type']] = max(specificity.get(row['type'], 0), len(row['matched_name']))
    ordered_entity_terms = sorted(specificity, key=lambda value: (-specificity[value], value))
    selected = list(dict.fromkeys([*literal_terms, *ordered_entity_terms]))[:max_terms]
    term_set = set(selected)
    entity_terms = sorted(term_set & set(ordered_entity_terms))
    if not entity_terms:
        expansion = literal
    else:
        terms = sorted(term_set)
        subclasses = sorted(class_id for class_id, parents in ancestors.items()
                            if class_id not in term_set and set(parents) & term_set)
        type_iris = [*terms, *subclasses]
        expansion = {
            'terms': terms,
            'labels': sorted({labels[class_id] for class_id in type_iris if labels.get(class_id)})[:max_terms],
            'subclasses': subclasses,
            'type_iris': type_iris,
        }
    return {**expansion, 'literal_terms': literal_terms, 'entity_terms': entity_terms,
            'entity_matches': [row for row in unique if row['type'] in entity_terms],
            'has_hierarchy': any(ancestors.values()),
            'matched': bool(expansion['terms'])}
