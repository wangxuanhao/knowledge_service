import importlib.util
import json

import pytest

from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository
from knowledge_service.services.service import KnowledgeService


def importer_type():
    assert importlib.util.find_spec('knowledge_service.services.legacy_import'), 'Legacy importer is not implemented'
    from knowledge_service.services.legacy_import import LegacyImporter
    return LegacyImporter


@pytest.fixture
def legacy(tmp_path):
    folder = tmp_path / 'data/projects/原名'
    folder.mkdir(parents=True)
    payloads = {
        'manifest.json': {'name': '原名', 'model': 'old', 'sources': {'规则.md': '规则原文'},'aliases':{'商户':['卖家']}},
        'graph.json': {'nodes': [{'id': '商户', 'label': 'Merchant', 'aliases': ['店家'], 'source_file': '规则.md'},
                                 {'id': '规则', 'label': 'RuleDocument'}],
                       'edges': [{'source': '商户', 'target': '规则', 'predicate': 'publishes', 'confidence': .7}]},
        'entities.json': [{'text': '商户', 'label': 'Merchant', 'passage': '另一证据'}],
        'segments.json': [{'seg_id': 0, 'text': '原文片段', 'source_file': '规则.md'}],
    }
    for name, payload in payloads.items():
        (folder / name).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    service = KnowledgeService(Repository(tmp_path / 'new.sqlite'), HashingEncoder())
    yield tmp_path, folder, payloads, service
    service.repository.close()


def test_import_preserves_graph_sources_and_raw_data_and_is_idempotent(legacy):
    root, folder, payloads, service = legacy
    importer = importer_type()(service, root)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    assert importer.list_projects()[0]['nodes'] == 2
    result = importer.import_project('原名')
    assert result['project']['name'] == '原名'
    assert result['counts']['nodes'] == 2 and result['counts']['edges'] == 1
    assert result['counts']['segments'] == 1
    rows = service.repository.current_records(result['project']['id'])
    merchant = next(r for r in rows if r['kind'] == 'entity' and r['text'] == '商户')
    assert merchant['metadata']['legacy']['raw']['aliases'] == ['店家']
    assert merchant['metadata']['aliases'] == ['店家','卖家']
    assert merchant['valid_from'] is None
    assert merchant['embedding_model'] == service.encoder.identity
    assert any(r['kind'] == 'document' and r['text'] == '规则原文' for r in rows)
    audit = next(r for r in rows if r['metadata'].get('legacy_import_complete'))
    assert audit['metadata'].get('_audit') is True
    assert audit['metadata']['legacy_files']['entities.json'] == payloads['entities.json']
    assert 'validation' in audit['metadata']
    assert audit['metadata']['validation']['default_ontology']['conforms'] is False
    assert importer.import_project('原名')['already_imported'] is True
    assert len(service.repository.list_projects()) == 1
    assert importer.list_projects()[0]['imported_project_id'] == result['project']['id']
    assert before == {p.name: p.read_bytes() for p in folder.iterdir()}


def test_embedding_failure_does_not_complete_import_and_retry_works(legacy):
    root, _, _, service = legacy
    class BrokenEncoder:
        identity = 'broken'
        def encode(self, texts):
            raise RuntimeError('offline')
    service.encoder = BrokenEncoder()
    importer = importer_type()(service, root)
    with pytest.raises(RuntimeError, match='offline'):
        importer.import_project('原名')
    assert not service.repository.list_projects()
    service.encoder = HashingEncoder()
    assert importer.import_project('原名')['already_imported'] is False


@pytest.mark.parametrize('name,kind', [('../原名', 'project'), ('C:/Windows', 'project'), ('原名', 'other')])
def test_traversal_and_invalid_kind_rejected(legacy, name, kind):
    root, _, _, service = legacy
    with pytest.raises(ValueError):
        importer_type()(service, root).import_project(name, kind)


def test_graphml_output_import_keeps_parallel_edges_and_unknown_sources(legacy):
    root, _, _, service = legacy
    folder = root / 'data/rule_demo_output/run_1'
    folder.mkdir(parents=True)
    (folder / 'graph.graphml').write_text('''<graphml xmlns="http://graphml.graphdrawing.org/xmlns">
      <key id="label" for="node" attr.name="label" attr.type="string"/>
      <key id="p" for="edge" attr.name="predicate" attr.type="string"/>
      <graph edgedefault="directed"><node id="a"><data key="label">Thing</data></node><node id="b"/>
      <edge source="a" target="b"><data key="p">relates</data></edge>
      <edge source="a" target="b"><data key="p">relates</data></edge></graph></graphml>''', encoding='utf-8')
    result = importer_type()(service, root).import_project('run_1', 'output')
    assert result['counts']['nodes'] == 2 and result['counts']['edges'] == 2
    rows = service.repository.current_records(result['project']['id'])
    assert len([r for r in rows if r['kind'] == 'relation']) == 2


def test_symlink_file_escape_rejected(legacy, tmp_path):
    root, folder, _, service = legacy
    outside = tmp_path / 'outside.json'
    outside.write_text('{}', encoding='utf-8')
    (folder / 'graph.json').unlink()
    try:
        (folder / 'graph.json').symlink_to(outside)
    except OSError:
        pytest.skip('Symlink creation unavailable on this Windows host')
    with pytest.raises(ValueError, match='越界'):
        importer_type()(service, root).import_project('原名')


def test_missing_endpoints_preserve_edges_as_explicit_unresolved_entities(legacy):
    root, folder, payloads, service = legacy
    payloads['graph.json']['edges'].append({'source': '商户', 'target': '旧缺失节点', 'predicate': 'mentions'})
    (folder / 'graph.json').write_text(json.dumps(payloads['graph.json']), encoding='utf-8')
    result = importer_type()(service, root).import_project('原名')
    rows = service.repository.current_records(result['project']['id'])
    assert len([r for r in rows if r['kind'] == 'relation']) == 2
    placeholder = next(r for r in rows if r['text'] == '旧缺失节点')
    assert placeholder['metadata']['legacy']['raw']['unresolved_endpoint'] is True
    assert any('旧缺失节点' in warning for warning in result['warnings'])


def test_failed_storage_is_not_reported_complete_and_reuses_pending_project(legacy, monkeypatch):
    root, _, _, service = legacy
    importer = importer_type()(service, root)
    original = service.repository.put_batch
    def unavailable(*args, **kwargs):
        raise RuntimeError('storage unavailable')
    monkeypatch.setattr(service.repository, 'put_batch', unavailable)
    with pytest.raises(RuntimeError, match='storage unavailable'):
        importer.import_project('原名')
    assert importer.list_projects()[0]['imported_project_id'] is None
    monkeypatch.setattr(service.repository, 'put_batch', original)
    assert importer.import_project('原名')['already_imported'] is False
    assert len(service.repository.list_projects()) == 1


def test_concurrent_importers_share_service_lock(legacy):
    from concurrent.futures import ThreadPoolExecutor
    root, _, _, service = legacy
    importer_a, importer_b = importer_type()(service, root), importer_type()(service, root)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda importer: importer.import_project('原名'), [importer_a, importer_b]))
    assert sorted(r['already_imported'] for r in results) == [False, True]
    assert len(service.repository.list_projects()) == 1
