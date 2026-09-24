"""Compatibility adapters that translate legacy ontology writes to drafts."""

from __future__ import annotations

from .ontology import Ontology
from .ontology_drafts import OntologyDrafts, StaleBase


DEPRECATION = {
    'deprecated': True,
    'replacement': '/api/projects/{project_id}/ontology-drafts',
    'message': '该写接口仅生成受治理草案，不再直接发布本体版本。',
}


def add_deprecation_headers(response):
    response.headers['Deprecation'] = 'true'
    response.headers['Link'] = (
        '</api/projects/{project_id}/ontology-drafts>; rel="successor-version"')


def compatibility_payload(preview, **legacy):
    return {
        **preview,
        # Legacy ontology write callers expect ``summary`` to be the parsed
        # ontology summary.  Draft records use that field for a human note.
        'draft_summary': preview.get('summary', ''),
        'summary': preview['ontology'],
        **legacy,
        'draft_id': preview['id'],
        'deprecation': DEPRECATION,
    }


def turtle_draft(repository, project_id, edited_turtle, *, source_kind,
                 title, actor, expected_ontology_id=None,
                 source_context=None, summary=''):
    """Create a draft and compile a complete Turtle edit into atomic commands."""
    # Parse before persistence so malformed Turtle cannot leave an empty draft.
    Ontology(edited_turtle)
    versions = repository.list_ontologies(project_id)
    latest_id = versions[-1]['id'] if versions else None
    if expected_ontology_id is not None and expected_ontology_id != latest_id:
        raise StaleBase(
            'ontology changed; refresh before creating a draft', details={
                'expected_ontology_id': expected_ontology_id,
                'current_ontology_id': latest_id,
            })
    drafts = OntologyDrafts(repository, publisher=repository)
    draft = drafts.create(
        project_id, latest_id, source_kind, title, actor,
        source_context=source_context or {}, summary=summary)
    preview = drafts.command(project_id, draft['id'], draft['revision'], {
        'action': 'diff_turtle', 'edited_turtle': edited_turtle,
        'reason': title,
    })
    return compatibility_payload(preview)


def update_turtle_draft(repository, project_id, draft_id, expected_revision,
                        edited_turtle, *, reason):
    if expected_revision is None:
        raise ValueError('更新现有草案必须提供 expected_revision')
    Ontology(edited_turtle)
    drafts = OntologyDrafts(repository, publisher=repository)
    preview = drafts.command(project_id, draft_id, expected_revision, {
        'action': 'diff_turtle', 'edited_turtle': edited_turtle,
        'reason': reason,
    })
    return compatibility_payload(preview)


__all__ = [
    'DEPRECATION', 'add_deprecation_headers', 'compatibility_payload',
    'turtle_draft', 'update_turtle_draft',
]
