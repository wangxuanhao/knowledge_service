"""Bind record-selection leaves before applying an AST to source artifacts."""
from ..utils.filters import matches_filter, validate_filter


_RECORD_SELECTION = frozenset({'id', 'kind', 'type', 'source_id'})
_FALSE = {'field': 'id', 'op': 'in', 'value': []}


def source_filter(filters, record):
    """Keep business predicates and exact Boolean branches for this hit.

    The original AST already qualifies the hit. Identity/kind/type/owner leaves
    describe that hit, whereas business metadata still constrains its source.
    Substituting actual truth values avoids deleting an arbitrary OR branch.
    """
    validate_filter(filters)
    def bind(node):
        if node is None:
            return True
        for group in ('and', 'or'):
            if group in node:
                children = [bind(child) for child in node[group]]
                decisive = False if group == 'and' else True
                neutral = not decisive
                if any(child is decisive for child in children):
                    return decisive
                children = [child for child in children if child is not neutral]
                if not children:
                    return neutral
                return children[0] if len(children) == 1 else {group: children}
        return matches_filter(record, node) if node['field'] in _RECORD_SELECTION else node
    result = bind(filters)
    return None if result is True else dict(_FALSE) if result is False else result


def source_scope(scope, record):
    result = {**scope, 'filters': source_filter(scope.get('filters'), record)}
    # Ontology ownership selects the qualified formal record. Immutable source
    # documents/chunks need not themselves belong to that publication.
    if result.get('ontology_scope') in {'ids', 'unknown'}:
        result.update(ontology_scope='all', ontology_ids=None)
    return result
