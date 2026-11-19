"""The inserted decision's database projection needs no per-row read-back."""
from collections import Counter

import pytest

from knowledge_service.repository import Repository
from knowledge_service.repository.connection import IntegrityError
from test_ontology_draft_repository import _decision, _draft, _operation


@pytest.mark.parametrize('size', [1, 48])
def test_append_decisions_returns_committed_rows_with_one_statement_per_decision(
        tmp_path, monkeypatch, size):
    path = tmp_path / 'decision-returning.sqlite'
    repo = Repository(path)
    project = repo.create_project('decision projection')['id']
    store = repo._ontology_drafts
    draft_id = store.create(project, _draft(project))['id']
    store.append_operations(project, draft_id, [_operation(f'op-{i}') for i in range(size)])
    decisions = [_decision(f'decision-{i}', f'op-{i}', f'sha256:op-{i}',
                           created_at='2026-11-19T12:00:00.123456+08:00') for i in range(size)]
    if size > 1:
        decisions[-1].update(action='reject', reason='not verified')
    real_execute = repo._db.execute
    counts = Counter()

    def execute(query, *args, **kwargs):
        counts[' '.join(query.split())] += 1
        return real_execute(query, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(repo._db, 'execute', execute)
        returned = store.append_decisions(project, draft_id, decisions)
    assert sum(n for q, n in counts.items() if 'ontology_review_decisions' in q) == size
    assert sum(n for q, n in counts.items() if q.startswith('INSERT INTO ontology_review_decisions')) == size
    assert [row['id'] for row in returned] == [item['id'] for item in decisions]
    assert [row['action'] for row in returned] == [item['action'] for item in decisions]
    assert all('seq' not in row for row in returned)
    reader = Repository(path)._ontology_drafts
    assert reader.history(project, draft_id)[1] == returned


def test_append_decisions_rolls_back_first_returned_row_on_later_binding_failure(tmp_path):
    path = tmp_path / 'decision-rollback.sqlite'
    repo = Repository(path)
    project = repo.create_project('decision rollback')['id']
    store = repo._ontology_drafts
    draft_id = store.create(project, _draft(project))['id']
    store.append_operations(project, draft_id, [_operation('first'), _operation('second')])
    first = _decision('first-ok', 'first', 'sha256:first')
    invalid = _decision('second-bad', 'second', 'sha256:invalid')
    reader = Repository(path)._ontology_drafts
    before = reader.export(project)
    with pytest.raises(IntegrityError, match='fingerprint mismatch'):
        store.append_decisions(project, draft_id, [first, invalid])
    assert reader.export(project) == before
    corrected = {**invalid, 'operation_fingerprint': 'sha256:second'}
    assert store.append_decisions(project, draft_id, [first, corrected]) == reader.history(project, draft_id)[1]
