"""P0-2：生成器契约标签与运行时版本对齐（含兼容读路径）。

背景：契约标签曾经写死成 ``semantica-0.6.7+materialization-context-v2``，而本机实际
安装的是 0.7.0 —— 标签在说谎，排查上游结构差异时会归因到错误的版本。而且它还是
source fingerprint 的输入之一，于是"换个标签"会把在飞的 run 全部判成 stale。

这里的四条断言分别锁住：
1. 曝光出去的标签 == 实际安装的 semantica 版本；
2. 指纹的身份用**稳定的适配层契约**（ADAPTER_CONTRACT），不是会随上游升级变的标签；
3. 兼容读路径：升级前创建的 run（历史契约 + 历史运行时版本）仍能通过收尾指纹校验；
4. run 的 provenance 同时记录"标签"和"适配层契约"。
"""
import importlib.metadata
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.api import ontology_discovery as discovery_api
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.discovery_vocabulary import discovery_source_fingerprint

LEGACY_LABEL = 'semantica-0.6.7+materialization-context-v2'
CANDIDATES = [{'id': 'c1', 'kind': 'class', 'name': 'Term'}]
GENERATION_OPTIONS = {'request': {}, 'build_hierarchy': True, 'min_occurrences': 1,
                      'baseline_turtle_sha256': 'x'}


def _run(**overrides):
    run = {'generation_options': dict(GENERATION_OPTIONS),
           'request_name': '发现本体', 'runtime_version': '0.6.7'}
    run.update(overrides)
    return run


def test_generator_contract_label_follows_the_installed_runtime_version():
    """标签必须等于实际安装的 semantica 版本（0.7.0 就写 0.7.0）。"""
    installed = importlib.metadata.version('semantica')

    assert discovery_api._semantica_runtime_version() == installed
    assert discovery_api.generator_contract() == (
        f'semantica-{installed}+materialization-context-v2')
    assert discovery_api.generator_contract('9.9.9') == (
        'semantica-9.9.9+materialization-context-v2')
    # 老标签只作为"历史值"保留在兼容名单里，不再作为当前契约出现
    assert LEGACY_LABEL in discovery_api.LEGACY_ADAPTER_CONTRACTS
    assert discovery_api.generator_contract() != LEGACY_LABEL


def test_source_fingerprint_uses_the_stable_adapter_contract():
    """指纹的身份来自适配层契约，而不是会随上游升级变化的版本标签。"""
    fingerprint = discovery_api._source_fingerprint(
        'p', None, CANDIDATES, SimpleNamespace(name='发现本体'),
        runtime_version='0.7.0', generation_options=dict(GENERATION_OPTIONS))
    expected = discovery_source_fingerprint(
        'p', None, CANDIDATES,
        normalizer_version=discovery_api.NORMALIZER_VERSION,
        generator_contract=discovery_api.ADAPTER_CONTRACT,
        runtime_version='0.7.0',
        attribute_threshold=discovery_api.ATTRIBUTE_THRESHOLD,
        generation_options=dict(GENERATION_OPTIONS),
        request_name='发现本体')

    assert fingerprint == expected
    assert discovery_api.ADAPTER_CONTRACT.startswith('ontology-discovery/')


def test_finalize_accepts_legacy_contract_fingerprints_but_not_changed_payloads():
    """兼容读路径：只有"版本标签不同"才算等价，数据变了必须照样判 stale。"""
    run = _run()
    legacy = discovery_api._finalize_fingerprint(
        'p', run, None, CANDIDATES,
        adapter_contract=LEGACY_LABEL, runtime_version='0.6.7')
    current = discovery_api._finalize_fingerprint(
        'p', run, None, CANDIDATES,
        adapter_contract=discovery_api.ADAPTER_CONTRACT,
        runtime_version=discovery_api._semantica_runtime_version())
    accepted = discovery_api._finalize_fingerprint_candidates('p', run, None, CANDIDATES)

    assert legacy in accepted, '升级前的 run 应该还能收尾'
    assert current in accepted
    # 换成别的候选集 / 改了请求名 —— 载荷变了，历史契约的指纹也不能再算等价
    assert legacy not in discovery_api._finalize_fingerprint_candidates(
        'p', run, None, [{'id': 'c2', 'kind': 'class', 'name': 'Other'}])
    assert legacy not in discovery_api._finalize_fingerprint_candidates(
        'p', _run(request_name='改名了'), None, CANDIDATES)


def test_discovery_run_records_both_the_label_and_the_adapter_contract(tmp_path, monkeypatch):
    """run 的 provenance 同时留下"标签"与"适配层契约"，排查时两笔账都在。"""
    monkeypatch.setattr(discovery_api, '_candidates', lambda *_args, **_kwargs: [
        {'id': 'one', 'kind': 'entity', 'text': '甲', 'proposed_type': '主体'}])
    monkeypatch.setattr(discovery_api, '_induce', lambda *_args, **_kwargs: (
        '@prefix owl: <http://www.w3.org/2002/07/owl#> . '
        '<urn:test:Subject> a owl:Class .',
        {'entity_types': {'主体': 'urn:test:Subject'},
         'relation_types': {}, 'attributes': {}},
        {'metadata': {}, 'validation': {}}))
    app = create_app(tmp_path / 'generator-contract.sqlite', HashingEncoder())
    with TestClient(app) as client:
        project = client.post('/api/projects', json={
            'name': '契约', 'use_default_ontology': False,
            'ontology_mode': 'discovery'}).json()
        response = client.post(
            f"/api/projects/{project['id']}/ontology-discovery/drafts",
            json={'name': '发现本体'})
        assert response.status_code == 201, response.text
        run = response.json()['run']

    installed = importlib.metadata.version('semantica')
    assert run['generator_version'] == (
        f'semantica-{installed}+materialization-context-v2')
    assert run['adapter_contract'] == discovery_api.ADAPTER_CONTRACT
    assert run['runtime_version'] == installed


# --------------------------------------------------------------------------
# 上游结构锁定：用**本机真实安装的 semantica 0.7.0** 跑一遍，把它的真实返回结构与
# 项目的读取逻辑一起锁住。这些断言失败就说明上游结构变了（而不是我们的代码错了）。
# --------------------------------------------------------------------------

def _real_result(candidates):
    """真跑 `_induce`（内部调用真实 OntologyGenerator），返回 (turtle, mappings)。"""
    pytest.importorskip('semantica.ontology')
    from knowledge_service.services.ontology_discovery import _induce

    turtle, mappings, _inferred = _induce('probe', '探针本体', candidates)
    return turtle, mappings


def test_real_semantica_returns_parent_and_subclassof_for_hierarchical_names():
    """0.7.0 的真实返回结构：`parent` / `subClassOf` 同值，且通用父类会自环。"""
    pytest.importorskip('semantica.ontology')
    from semantica.ontology import OntologyGenerator

    def entity(name):
        return {'type': name, 'entity_type': name, 'name': 'x' + name,
                'text': 'x' + name, 'confidence': 1, 'properties': {}}

    result = OntologyGenerator(
        base_uri='urn:knowledge:ontology:probe:', min_occurrences=1).generate_ontology(
        {'entities': [entity('Entity'), entity('Manager')], 'relationships': []},
        name='探针本体', build_hierarchy=True)

    classes = {item['name']: item for item in result['classes']}
    assert set(classes) == {'Entity', 'Manager'}, '上游类清单的结构变了'
    # 顶层键也要锁：读取逻辑依赖 classes / properties / metadata / validation
    assert {'classes', 'properties', 'metadata', 'validation'} <= set(result)
    # 0.7.0 的层级推断基于"通用父类"名单，Manager 于是拿到 Entity 作为父类
    assert classes['Manager']['parent'] == 'Entity'
    assert classes['Manager']['subClassOf'] == 'Entity'
    assert classes['Manager']['metadata']['inferred_from'] == 'Manager'
    # 上游的怪癖：通用父类 Entity 被它自己当成父类（自环）。
    # 读取逻辑里 `parent != child` 那行守卫就是为它准备的 —— 见下一条用例。
    assert classes['Entity']['parent'] == 'Entity'
    assert classes['Entity']['subClassOf'] == 'Entity'


def test_induce_drops_the_upstream_self_parent_loop():
    """上游自环必须被丢掉：否则会往本体里写 `C rdfs:subClassOf C`。"""
    turtle, _mappings = _real_result([
        {'id': 'e1', 'kind': 'entity', 'text': '甲', 'proposed_type': 'Entity', 'confidence': 1},
        {'id': 'e2', 'kind': 'entity', 'text': '乙', 'proposed_type': 'Manager', 'confidence': 1},
    ])

    from knowledge_service.services.ontology import Ontology

    ontology = Ontology(turtle)
    root = ontology.resolve('urn:knowledge:ontology:probe:Entity', ontology.classes)
    child = ontology.resolve('urn:knowledge:ontology:probe:Manager', ontology.classes)
    # `parents()` 返回"自己 + 全部祖先"，所以自环的表现就是 root 的祖先集里只有它自己
    assert ontology.parents(root) == {root}, '自环没有拦住'
    assert ontology.parents(child) == {child, root}
    # 文本层面也确认一次：Entity 的块里不该出现 subClassOf
    assert 'rdfs:subClassOf disc:Entity' in turtle
    entity_block = turtle.split('disc:Entity a owl:Class')[1]
    assert 'subClassOf' not in entity_block


def test_induce_maps_real_parent_into_rdfs_subclassof():
    """真实 0.7.0 的 parent → 项目读出的 Turtle 必须带 rdfs:subClassOf。"""
    turtle, mappings = _real_result([
        {'id': 'e1', 'kind': 'entity', 'text': '甲', 'proposed_type': 'Entity', 'confidence': 1},
        {'id': 'e2', 'kind': 'entity', 'text': '乙', 'proposed_type': 'Manager', 'confidence': 1},
    ])

    assert mappings['entity_types'] == {
        'Entity': 'urn:knowledge:ontology:probe:Entity',
        'Manager': 'urn:knowledge:ontology:probe:Manager',
    }
    assert 'rdfs:subClassOf disc:Entity' in turtle
    # 落图后语义层也要认得出这层父子关系（不是只写进文本）
    from knowledge_service.services.ontology import Ontology

    ontology = Ontology(turtle)
    child = ontology.resolve('urn:knowledge:ontology:probe:Manager', ontology.classes)
    parent = ontology.resolve('urn:knowledge:ontology:probe:Entity', ontology.classes)
    assert parent in ontology.parents(child)


def test_induce_survives_semantica_name_mangling_for_non_ascii_types():
    """0.7.0 会把类名规范成 'Entitytype<sha>'（下划线被吃掉、首字母小写）。

    读取逻辑因此不能靠类名回推来源，必须走 `metadata.inferred_from` ——
    这条断言就是那个前提的哨兵：中文类型的中文标签必须原样落到 Turtle 上。
    """
    turtle, mappings = _real_result([
        {'id': 'e1', 'kind': 'entity', 'text': '小美', 'proposed_type': '签约主播', 'confidence': 1},
        {'id': 'e2', 'kind': 'entity', 'text': '阿强', 'proposed_type': '签约主播', 'confidence': 1},
    ])

    iri = mappings['entity_types']['签约主播']
    assert 'rdfs:label "签约主播"@zh' in turtle, (
        '0.7.0 的类名规范化改变了，或被读取逻辑漏掉了 inferred_from')
    from knowledge_service.services.ontology import Ontology

    labels = {item['id']: item for item in Ontology(turtle).summary()['classes']}
    assert labels[iri]['label_zh'] == '签约主播'


def test_subclassof_only_alias_still_produces_the_parent_edge(monkeypatch):
    """兼容别名：上游只给 `subClassOf`（不给 `parent`）时，落图结果必须一致。

    这里替换的是**生成器**（`semantica.ontology.OntologyGenerator`），而不是
    `_induce` —— 要验的正是 `_induce` 里的读取逻辑怎么处理这个别名。
    """
    import sys
    from types import ModuleType

    from knowledge_service.services.ontology_discovery import _induce

    def generate(*_args, **_kwargs):
        return {
            'classes': [
                {'name': 'Entity', 'uri': 'Entity', 'label': 'Entity',
                 'metadata': {'inferred_from': 'Entity'}, '@type': 'owl:Class'},
                {'name': 'Manager', 'uri': 'Manager', 'label': 'Manager',
                 'subClassOf': 'Entity',
                 'metadata': {'inferred_from': 'Manager'}, '@type': 'owl:Class'},
            ],
            'properties': [], 'metadata': {}, 'validation': {},
        }

    module = ModuleType('semantica.ontology')

    class FakeOntologyGenerator:
        def __init__(self, **_kwargs):
            pass

        def generate_ontology(self, *_args, **_kwargs):
            return generate()

    module.OntologyGenerator = FakeOntologyGenerator
    monkeypatch.setitem(sys.modules, 'semantica.ontology', module)

    turtle, mappings, _inferred = _induce('probe', '探针本体', [
        {'id': 'e1', 'kind': 'entity', 'text': '甲', 'proposed_type': 'Entity', 'confidence': 1},
        {'id': 'e2', 'kind': 'entity', 'text': '乙', 'proposed_type': 'Manager', 'confidence': 1},
    ])

    assert mappings['entity_types']['Manager'] == 'urn:knowledge:ontology:probe:Manager'
    assert 'rdfs:subClassOf disc:Entity' in turtle
    # 语义层再确认一次：父子边存在，且没有自环
    from knowledge_service.services.ontology import Ontology

    ontology = Ontology(turtle)
    root = ontology.resolve('urn:knowledge:ontology:probe:Entity', ontology.classes)
    child = ontology.resolve('urn:knowledge:ontology:probe:Manager', ontology.classes)
    assert root in ontology.parents(child)
    assert ontology.parents(root) == {root}
