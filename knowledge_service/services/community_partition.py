"""Deterministic, hierarchical Louvain partitions of the approved graph."""
import networkx as nx


ALGORITHM = 'louvain-hierarchical-v1'


def partition(entity_ids, endpoints):
    graph = nx.Graph()
    graph.add_nodes_from(sorted(entity_ids))
    for first, second in sorted(tuple(sorted(edge)) for edge in endpoints):
        weight = graph[first][second]['weight'] if graph.has_edge(first, second) else 0
        graph.add_edge(first, second, weight=weight + 1)
    graph.remove_nodes_from(list(nx.isolates(graph)))
    seen = set()
    result = []
    if graph:
        levels = nx.community.louvain_partitions(graph, seed=0)
        for level, groups in enumerate(reversed(list(levels))):
            for members in sorted(tuple(sorted(group)) for group in groups):
                if members not in seen:
                    result.append((level, members))
                    seen.add(members)
    return result
