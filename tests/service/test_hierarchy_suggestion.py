"""P0-3：候选类的 LLM 层级建议（只产出建议，绝不自动改本体）。

要点：

1. 开放发现以前**完全不抽父子层级**（`SemanticaExtractor.discover()` 只出实体/关系/属性，
   而 Semantica 自己的层级推断只看类名，我们的机器名是哈希串 → 一条都推不出来）；
2. LLM 输出是**不可信输入**：只认清单里的类名、去自环、去成环的边、限量、确定性排序；
3. LLM 不可用/超时/返回垃圾 → **降级为空建议**，发现流程照常完成；
4. 建议写进生成的 Turtle，走既有的 `add_parent` 操作 → 审核 → 发布（无旁路）。
"""
import json
import sys
from types import ModuleType

import pytest

from knowledge_service.api import ontology_discovery as discovery_api
from knowledge_service.services.hierarchy_suggestion import (
    HierarchySuggestionsUnavailable,
    build_prompt,
    parse_suggestions,
)

CLASSES = ['合作方', '签约主播', '电商平台', '直播间']


def test_parse_keeps_only_known_classes_and_sorts_deterministically():
    payload = json.dumps({
        '签约主播': ['合作方'],
        '合作方': [],
        '不存在的类': ['合作方'],
        '直播间': ['电商平台', '电商平台'],          # 重复父类
        '电商平台': ['电商平台'],                  # 自环
    }, ensure_ascii=False)

    suggestions = parse_suggestions(payload, CLASSES)

    assert suggestions == {'签约主播': ['合作方'], '直播间': ['电商平台']}
    # 同一份输入两次解析必须完全一样（确定性）
    assert suggestions == parse_suggestions(payload, CLASSES)


def test_parse_strips_code_fences_and_rejects_garbage():
    fenced = '```json\n{"签约主播": ["合作方"]}\n```'
    assert parse_suggestions(fenced, CLASSES) == {'签约主播': ['合作方']}
    assert parse_suggestions('这不是 JSON', CLASSES) == {}
    assert parse_suggestions('{"签约主播": "合作方"}', CLASSES) == {'签约主播': ['合作方']}
    assert parse_suggestions(None, CLASSES) == {}


def test_parse_drops_edges_that_would_create_a_cycle():
    """环会让本体自相矛盾（A ⊑ B ⊑ A），必须丢掉其中一条边。"""
    payload = json.dumps({'甲': ['乙'], '乙': ['甲']}, ensure_ascii=False)

    suggestions = parse_suggestions(payload, ['甲', '乙'])

    edges = [(child, parent) for child, parents in suggestions.items() for parent in parents]
    assert len(edges) == 1, f'环没有拦住：{edges}'
    # 锁的是"只留一条 + 每次一样"，不是运气：按码点 '乙'(U+4E59) 先于 '甲'(U+7532)，
    # 所以先接受 乙 ⊑ 甲，再丢弃会成环的 甲 ⊑ 乙。
    assert suggestions == {'乙': ['甲']}
    assert suggestions == parse_suggestions(payload, ['甲', '乙'])


def test_parse_drops_the_edge_that_closes_a_longer_cycle():
    """两条边的环靠"反向边"就能拦；三条以上只有真正的环检测能拦（A ⊑ B ⊑ C ⊑ A）。"""
    suggestions = parse_suggestions(
        json.dumps({'A': ['B'], 'B': ['C'], 'C': ['A']}), ['A', 'B', 'C'])

    assert suggestions == {'A': ['B'], 'B': ['C']}


def test_parse_keeps_a_legitimate_diamond_dag():
    """菱形（两个父类共享一个祖先）不是环，必须完整保留——避免"去环"误伤多父类。"""
    payload = json.dumps({
        '签约主播': ['合作方', '商家'],
        '合作方': ['商业角色'],
        '商家': ['商业角色'],
    }, ensure_ascii=False)

    assert parse_suggestions(payload, ['签约主播', '合作方', '商家', '商业角色']) == {
        '签约主播': ['合作方', '商家'],
        '商家': ['商业角色'],
        '合作方': ['商业角色'],
    }


def test_parse_limits_width_and_ignores_blank_names():
    names = [f'类{index}' for index in range(10)]
    payload = json.dumps({'类0': names[1:]}, ensure_ascii=False)

    suggestions = parse_suggestions(payload, names)

    assert len(suggestions['类0']) == 3, '每个子类的父类数量要有上限'
    assert parse_suggestions(json.dumps({'  ': ['甲']}, ensure_ascii=False), ['甲']) == {}


def test_build_prompt_is_chinese_json_and_lists_only_real_classes():
    payload = json.loads(build_prompt(CLASSES, request_name='发现本体'))

    assert payload['候选类清单'] == sorted(CLASSES)
    assert '父子' in payload['任务']
    assert payload['草案名'] == '发现本体'


class _FakeResponse:
    """假装是 httpx 的响应：只需要 raise_for_status / json 两个方法。"""

    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {'choices': [{'message': {'content': self._content}}]}


class _FakeClient:
    def __init__(self, calls, content):
        self._calls = calls
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def post(self, url, headers=None, json=None):
        self._calls.append({'url': url, 'payload': json})
        return _FakeResponse(self._content)


def test_repeated_suggestion_for_the_same_classes_only_calls_the_llm_once(monkeypatch):
    """同一批候选类 + 同一草案名只问一次 LLM。

    生成草案会被重复触发（同一 fingerprint 复用已有 run，但结果要先算出来），
    没有缓存就要为同一次发现白花一次外部调用。同时验证缓存键含类名与草案名。
    """
    from knowledge_service.services import hierarchy_suggestion as module

    calls = []
    monkeypatch.setenv('KG_LLM_BASE_URL', 'https://llm.example/v1')
    monkeypatch.setenv('KG_LLM_API_KEY', 'unit-test-placeholder')
    monkeypatch.setenv('KG_LLM_MODEL', 'unit-test-model')
    monkeypatch.setattr(module, 'external_client',
                        lambda *_a, **_k: _FakeClient(calls, '{"签约主播": ["合作方"]}'))
    module.clear_cache()

    first = module.suggest_hierarchy(['合作方', '签约主播'], request_name='发现本体')
    second = module.suggest_hierarchy(['签约主播', '合作方'], request_name='发现本体')
    assert first == second == {'签约主播': ['合作方']}
    assert len(calls) == 1, '同样的输入不该重复调用 LLM'

    # 草案名不同 → 换键（提示词里带了草案名，结果不能混用）
    module.suggest_hierarchy(['合作方', '签约主播'], request_name='另一个草案')
    assert len(calls) == 2

    # 缓存里的对象被改坏也不会污染后续调用（返回的是副本）
    first['签约主播'].append('人工塞进去的父类')
    assert module.suggest_hierarchy(['合作方', '签约主播'],
                                    request_name='发现本体') == {'签约主播': ['合作方']}


def test_suggest_hierarchy_requires_the_llm_channel(monkeypatch):
    """没配 KG_LLM_* 时抛专用异常，让调用方能"降级"而不是崩。"""
    for key in ('KG_LLM_BASE_URL', 'KG_LLM_API_KEY', 'KG_LLM_MODEL'):
        monkeypatch.delenv(key, raising=False)
    from knowledge_service.services.hierarchy_suggestion import suggest_hierarchy

    with pytest.raises(HierarchySuggestionsUnavailable, match='KG_LLM'):
        suggest_hierarchy(CLASSES)


def test_discovery_falls_back_to_flat_ontology_when_llm_is_unavailable(monkeypatch, tmp_path):
    """降级路径：LLM 没配置时，发现照常产出草案（只是没有层级）。"""
    from fastapi.testclient import TestClient

    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder

    monkeypatch.setattr(discovery_api, '_candidates', lambda *_args, **_kwargs: [
        {'id': 'c1', 'kind': 'entity', 'text': '小美', 'proposed_type': '签约主播'},
        {'id': 'c2', 'kind': 'entity', 'text': '头条', 'proposed_type': '电商平台'},
    ])
    for key in ('KG_LLM_BASE_URL', 'KG_LLM_API_KEY', 'KG_LLM_MODEL'):
        monkeypatch.delenv(key, raising=False)
    app = create_app(tmp_path / 'no-llm.sqlite', HashingEncoder())
    with TestClient(app) as client:
        project = client.post('/api/projects', json={
            'name': '无 LLM', 'use_default_ontology': False,
            'ontology_mode': 'discovery'}).json()
        response = client.post(
            f"/api/projects/{project['id']}/ontology-discovery/drafts",
            json={'name': '发现本体'})
        assert response.status_code == 201, response.text
        run = response.json()['run']

    assert run['hierarchy_suggestions'] == {}
    assert 'subClassOf' not in response.json().get('turtle', '')


def test_suggested_hierarchy_lands_as_subclassof_in_the_draft_and_its_operations(
        monkeypatch, tmp_path):
    """主路径：LLM 给出父子关系 → 草案 Turtle 带 subClassOf → 生成 add_parent 操作。"""
    from fastapi.testclient import TestClient

    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder

    calls = []

    def fake_suggest(class_names, *, examples=(), request_name='', timeout=120):
        calls.append(sorted(class_names))
        return {'签约主播': ['合作方']}

    monkeypatch.setattr(discovery_api, 'suggest_hierarchy', fake_suggest)
    monkeypatch.setattr(discovery_api, '_candidates', lambda *_args, **_kwargs: [
        {'id': 'c1', 'kind': 'entity', 'text': '小美', 'proposed_type': '签约主播'},
        {'id': 'c2', 'kind': 'entity', 'text': '头条', 'proposed_type': '合作方'},
    ])
    app = create_app(tmp_path / 'with-llm.sqlite', HashingEncoder())
    with TestClient(app) as client:
        project = client.post('/api/projects', json={
            'name': '有 LLM', 'use_default_ontology': False,
            'ontology_mode': 'discovery'}).json()
        response = client.post(
            f"/api/projects/{project['id']}/ontology-discovery/drafts",
            json={'name': '发现本体'})
        assert response.status_code == 201, response.text
        payload = response.json()
        run = payload['run']

    # 一次调用、类清单正确（只含实体类）
    assert calls == [['合作方', '签约主播']]
    # 台账落库
    assert run['hierarchy_suggestions'] == {'签约主播': ['合作方']}
    # 层级进了 Turtle：子类带 subClassOf 指向父类
    turtle = payload['turtle']
    child = run['mappings']['entity_types']['签约主播']
    parent = run['mappings']['entity_types']['合作方']
    from rdflib import Graph, RDFS, URIRef

    graph = Graph()
    graph.parse(data=turtle, format='turtle')
    assert (URIRef(child), RDFS.subClassOf, URIRef(parent)) in graph
    # 既有的 add_parent 操作被生成（发布门禁走的就是它）
    actions = [item['action'] for item in payload['operations']]
    assert 'add_parent' in actions


def test_hierarchy_suggestions_are_dropped_when_the_llm_returns_junk(monkeypatch, tmp_path):
    """LLM 返回垃圾不能污染本体：解析后为空 → 草案没有层级、也不报错。"""
    from fastapi.testclient import TestClient

    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder

    monkeypatch.setattr(discovery_api, 'suggest_hierarchy',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            RuntimeError('上游 500')))
    monkeypatch.setattr(discovery_api, '_candidates', lambda *_args, **_kwargs: [
        {'id': 'c1', 'kind': 'entity', 'text': '小美', 'proposed_type': '签约主播'},
        {'id': 'c2', 'kind': 'entity', 'text': '头条', 'proposed_type': '合作方'},
    ])
    app = create_app(tmp_path / 'junk-llm.sqlite', HashingEncoder())
    with TestClient(app) as client:
        project = client.post('/api/projects', json={
            'name': '垃圾输出', 'use_default_ontology': False,
            'ontology_mode': 'discovery'}).json()
        response = client.post(
            f"/api/projects/{project['id']}/ontology-discovery/drafts",
            json={'name': '发现本体'})
        assert response.status_code == 201, response.text
        run = response.json()['run']

    assert run['hierarchy_suggestions'] == {}
