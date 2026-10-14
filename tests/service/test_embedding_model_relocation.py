import json
from knowledge_service.integrations.embeddings import configured_encoder
from knowledge_service.services.retrieval import rank_candidates


def test_relocated_same_model_keeps_existing_vector_compatibility(tmp_path, monkeypatch):
    (tmp_path / 'config.json').write_text(json.dumps({'hidden_size': 2}))
    monkeypatch.setenv('KG_EMBEDDING_BACKEND', 'local')
    monkeypatch.setenv('KG_EMBEDDING_PATH', str(tmp_path))
    monkeypatch.setenv('KG_EMBEDDING_IDENTITY', 'existing-verified-model')
    encoder = configured_encoder()
    monkeypatch.setattr(encoder, 'encode', lambda texts: [[1.0, 0.0] for text in texts])
    ranked = rank_candidates([{'id': 'old', 'embedding': [1.0, 0.0],
                              'embedding_model': 'existing-verified-model'}], 'query', 1, encoder)
    assert ranked[0]['id'] == 'old' and ranked[0]['score'] == 1
