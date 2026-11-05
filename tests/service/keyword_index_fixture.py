"""Explicit keyword-index fixtures with real PostgreSQL readiness guards."""
from types import SimpleNamespace

from knowledge_service.repository.scoped_reads import ScopedReads
from test_production_projection_receipts import activate_legacy


def install_keyword_store(service, monkeypatch):
    """Replace only external ranking I/O; authority and index guards stay real."""
    repo = service.repository

    def search(project, query, *, kinds, k, scope, platform_match=None,
               eligible_version_ids=None, **options):
        assert options['retrieval_mode'] == 'keyword'
        rows = ScopedReads(repo).ranked_keyword(
            project, {**scope, '_platform_match': platform_match}, query,
            kinds=kinds, limit=100)
        if eligible_version_ids is not None:
            eligible = set(eligible_version_ids)
            rows = [row for row in rows if row['version_id'] in eligible]
        return [{'record_id': row['id'], 'version_id': row['version_id'], 'score': row['score']}
                for row in rows[:k]]

    monkeypatch.setattr(service, 'milvus_store', SimpleNamespace(available=True, search=search))


def ready_keyword_index(service, project, monkeypatch):
    """Activate this test's written data at its actual current watermark."""
    repo = service.repository
    activate_legacy(repo, project, {}, watermark=repo.projections.project_watermark(project))
    install_keyword_store(service, monkeypatch)
