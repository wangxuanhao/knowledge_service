"""Search a scoped neighborhood and project matching paths for the graph canvas."""
from ..repository.graph_search_evidence import GraphSearchEvidence


_MAX_SEARCH_NODES = 10000
_MAX_SEARCH_EDGES = 10000


def progressive_search(reads, project, scope, labels, *, seeds, hops, direction, relationship_types,
                       filter_steps, max_nodes, max_edges):
    # The small response budgets govern the canvas after matching. Traversal
    # has its own safety limits and reports whenever its coverage is partial.
    neighborhood = reads.neighbors(project, scope, seeds, hops=hops, direction=direction,
        relationship_types=relationship_types, max_nodes=_MAX_SEARCH_NODES, max_edges=_MAX_SEARCH_EDGES)
    nodes = {row['id']: row for row in neighborhood['records'] if row['kind'] == 'entity'}
    edges = {row['id']: row for row in neighborhood['records'] if row['kind'] == 'relation'}
    paths = {path['end']: path for path in neighborhood['reasoning_paths']}
    witnesses = GraphSearchEvidence(reads)
    candidates, attributes, sources = _matching_entities(
        witnesses, project, scope, labels, list(nodes.values()), filter_steps)

    for node in candidates:
        id = node['id']
        paths.setdefault(id, {'start': id, 'end': id, 'steps': [],
                              'start_label': node['text'], 'end_label': node['text']})
    candidates.sort(key=lambda node: (len(paths[node['id']]['steps']), node['id']))
    visible, visible_nodes, visible_edges = _select_paths(
        candidates, paths, [seed for seed in seeds if seed in nodes], max_nodes, max_edges)
    display_truncated = len(visible) < len(candidates)
    if not filter_steps:
        # Keep actual cross-links in the unfiltered preview as space allows.
        for id, edge in edges.items():
            if id in visible_edges or edge['subject_id'] not in visible_nodes or edge['object_id'] not in visible_nodes:
                continue
            if len(visible_edges) == max_edges:
                display_truncated = True
                break
            visible_edges.add(id)
    if not any(step['field'].startswith('source_') for step in filter_steps):
        sources = witnesses.sources(project, scope, visible, [])
    matches = []
    for row in visible:
        id = row['id']
        path = paths[id]
        matches.append({
            'id': id, 'version_id': row['version_id'], 'label': row['text'], 'type': row.get('type'),
            'type_label': labels.get(row.get('type'), row.get('type') or ''),
            'hops': len(path['steps']), 'path': path, 'source': sources.get(id),
            'evidence': [_evidence(step, id, attributes, sources) for step in filter_steps],
        })
    scan_truncated = neighborhood['truncated']
    partial = scan_truncated or display_truncated
    return {
        'records': [row for id, row in nodes.items() if id in visible_nodes] +
                   [row for id, row in edges.items() if id in visible_edges],
        'reasoning_paths': [match['path'] for match in matches if match['hops']],
        'matches': matches, 'total': len(candidates), 'partial': partial, 'truncated': partial,
        'scan_truncated': scan_truncated,
        'continuation': ({'seeds': seeds, 'hops': hops, 'direction': direction, 'max_nodes': max_nodes,
                          'max_edges': max_edges, 'reason': 'budget_exhausted'} if partial else None),
    }


def _matching_entities(witnesses, project, scope, labels, candidates, steps):
    attribute_values, source_steps = set(), []
    for step in steps:
        field, value = step['field'], step['value']
        if field == 'entity_type':
            candidates = [row for row in candidates if value in {row.get('type'), labels.get(row.get('type'))}]
        elif field == 'entity_name':
            needle = value.casefold()
            candidates = [row for row in candidates if needle in row['text'].casefold()]
        elif field == 'attribute_value':
            attribute_values.add(value)
        else:
            source_steps.append(step)
    attributes = witnesses.attributes(project, scope, [row['id'] for row in candidates], attribute_values)
    if attribute_values:
        candidates = [row for row in candidates if all((row['id'], value) in attributes for value in attribute_values)]
    sources = witnesses.sources(project, scope, candidates, source_steps) if source_steps else {}
    if source_steps:
        candidates = [row for row in candidates if row['id'] in sources]
    return candidates, attributes, sources


def _select_paths(candidates, paths, seeds, max_nodes, max_edges):
    visible, visible_nodes, visible_edges = [], set(), set()
    for node in candidates:
        path = paths[node['id']]
        nodes = {path['start'], *(step['to'] for step in path['steps'])}
        edges = {step['relation_id'] for step in path['steps']}
        if len(visible_nodes) + len(nodes - visible_nodes) > max_nodes:
            continue
        if len(visible_edges) + len(edges - visible_edges) > max_edges:
            continue
        visible.append(node)
        visible_nodes.update(nodes)
        visible_edges.update(edges)
    for seed in seeds:
        if len(visible_nodes) >= max_nodes:
            break
        visible_nodes.add(seed)
    return visible, visible_nodes, visible_edges


def _evidence(step, entity_id, attributes, sources):
    if step['field'] == 'attribute_value':
        row = attributes[entity_id, step['value']]
        return {**step, 'attribute_id': row['id'], 'attribute_version_id': row['version_id']}
    if step['field'].startswith('source_'):
        return {**step, 'source': sources[entity_id]}
    return step
