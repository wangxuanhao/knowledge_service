"""Small, bounded helpers for retrieval scope and neighboring source context."""
from __future__ import annotations

from ..domain.applicability import scope_identity


def adjacent_chunks(rows, hits, *, radius=1, max_total=12):
    """Return source neighbors without changing the ranked hit list.

    This works for fixed, paragraph and structural chunks.  A neighbor must be
    from the same document and applicability scope; when source version metadata
    exists it must match as well.
    """
    chunks = [row for row in rows if row.get('kind') == 'chunk']
    by_id = {row['id']: row for row in chunks}
    by_source = {}
    for row in chunks:
        source_id = row.get('source_id')
        if source_id:
            by_source.setdefault(source_id, []).append(row)
    for group in by_source.values():
        group.sort(key=lambda row: ((row.get('metadata') or {}).get('start_char', 0), row['id']))

    selected = []
    seen = {row['id'] for row in hits}
    for hit in hits:
        anchor_id = hit['id'] if hit.get('kind') == 'chunk' else (
            (hit.get('metadata') or {}).get('chunk_id'))
        anchor = by_id.get(anchor_id)
        if anchor is None:
            continue
        group = by_source.get(anchor.get('source_id'), [])
        try:
            index = next(i for i, row in enumerate(group) if row['id'] == anchor_id)
        except StopIteration:
            continue
        anchor_metadata = anchor.get('metadata') or {}
        anchor_scope = scope_identity(anchor_metadata.get('applicability'))
        anchor_version = anchor_metadata.get('source_version_id')
        if not anchor.get('source_id') or not anchor_version:
            continue
        for offset in range(-radius, radius + 1):
            if offset == 0 or not 0 <= index + offset < len(group):
                continue
            row = group[index + offset]
            metadata = row.get('metadata') or {}
            if scope_identity(metadata.get('applicability')) != anchor_scope:
                continue
            if metadata.get('source_version_id') != anchor_version:
                continue
            if row['id'] in seen:
                continue
            selected.append({**row, 'context_reason': 'adjacent_chunk',
                             'anchor_chunk_id': anchor_id})
            seen.add(row['id'])
            if len(selected) >= max_total:
                return selected
    return selected
