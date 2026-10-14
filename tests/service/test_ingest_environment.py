from knowledge_service.core.config import load_environment


def test_batch_limits_load_from_dotenv_without_owner_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv('KG_SKIP_DOTENV', raising=False)
    for key in ('KNOWLEDGE_INGEST_MAX_ITEMS', 'KNOWLEDGE_LLM_CONCURRENCY', 'POSTGRES_PASSWORD'):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / '.env').write_text('KNOWLEDGE_INGEST_MAX_ITEMS=500\nKNOWLEDGE_LLM_CONCURRENCY=1\nPOSTGRES_PASSWORD=private\n')
    loaded = load_environment(tmp_path)
    assert 'KNOWLEDGE_INGEST_MAX_ITEMS' in loaded
    assert 'KNOWLEDGE_LLM_CONCURRENCY' in loaded
    assert 'POSTGRES_PASSWORD' not in loaded
