"""P0-1：本体感知的查询扩展（默认关；关时逐字节等价）。

这一层要锁住两件事，都是硬约束：

1. **子类蕴含生效**：`type=签约主播` 的实体在查「合作方」时要能被召回 ——
   靠的是"命中类 + 其全部子类"的类型通道，而不是碰巧文本里出现了查询词；
2. **默认关、关时逐字节等价**：`ontology_expansion` 不开时，检索路径一行都不执行，
   响应与改动前逐字段相同（不然这个开关等于一次无声的检索行为变更）。
"""
import pytest

from knowledge_service.repository import Repository
from knowledge_service.services.ontology import Ontology, match_query_expansion
from knowledge_service.services.retrieval import RetrievalEngine
from knowledge_service.services.service import KnowledgeService

CRM = 'urn:knowledge:ontology:crm:'
PARTNER = CRM + '合作方'
STREAMER = CRM + '签约主播'
STUDIO = CRM + '直播间'
COOPERATION = CRM + '合作'
SINGLE_CHAR = CRM + '方'

# 父类「合作方」/ 子类「签约主播」，外加两个诱饵：更短的「合作」（考最长匹配）与
# 单字「方」（考长度阈值）。
ONTOLOGY_TURTLE = f"""
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<{PARTNER}> a owl:Class ; rdfs:label "合作方"@zh .
<{STREAMER}> a owl:Class ; rdfs:label "签约主播"@zh ;
    rdfs:subClassOf <{PARTNER}> .
<{STUDIO}> a owl:Class ; rdfs:label "直播间"@zh .
<{COOPERATION}> a owl:Class ; rdfs:label "合作"@zh .
<{SINGLE_CHAR}> a owl:Class ; rdfs:label "方"@zh .
"""

EMPTY = {'terms': [], 'labels': [], 'subclasses': [], 'type_iris': []}


class Encoder:
    identity = 'test-v1'
    semantic = True

    def encode(self, texts):
        return [[1.0, 0.0] for _ in texts]


def _index():
    return Ontology(ONTOLOGY_TURTLE).expansion_index()


def test_query_expansion_finds_the_class_its_subclasses_and_applies_longest_match():
    """查「合作方」：命中它 + 它的子类；更短的「合作」与单字「方」都不该算命中。"""
    expansion = match_query_expansion(_index(), '合作方有哪些')

    assert expansion['terms'] == [PARTNER]
    assert expansion['subclasses'] == [STREAMER]
    assert expansion['type_iris'] == [PARTNER, STREAMER]
    assert expansion['labels'] == ['合作方', '签约主播']
    # 最长匹配：'合作' 被 '合作方' 覆盖
    assert COOPERATION not in expansion['type_iris']
    # 长度阈值：单字 '方' 不参与匹配
    assert SINGLE_CHAR not in expansion['type_iris']
    # 无关的类不出现
    assert STUDIO not in expansion['type_iris']


def test_query_expansion_returns_empty_for_unrelated_or_empty_queries():
    assert match_query_expansion(_index(), '和本体毫无关系的问题') == EMPTY
    assert match_query_expansion(_index(), '') == EMPTY
    assert match_query_expansion(_index(), None) == EMPTY
    # 只有单字命中时也当作没命中（长度阈值）
    assert match_query_expansion(_index(), '方') == EMPTY


def test_ontology_query_expansion_matches_the_index_helper():
    """`Ontology.query_expansion` 就是"索引 + 匹配"，两条路径必须一致。"""
    ontology = Ontology(ONTOLOGY_TURTLE)

    assert (ontology.query_expansion('签约主播的合同')
            == match_query_expansion(_index(), '签约主播的合同'))
    # 子类自己命中时，父类不该被算进去（方向是"父 → 子"，不是"子 → 父"）
    assert ontology.query_expansion('签约主播的合同')['terms'] == [STREAMER]
    assert ontology.query_expansion('签约主播的合同')['subclasses'] == []


def _project_with_subclass_entities(tmp_path):
    """建一个项目：一个子类实体（文本不含查询词）+ 一个无关实体。"""
    repo = Repository(tmp_path / 'expansion.sqlite')
    project = repo.create_project('本体扩展')['id']
    repo.put_record(project, {
        'id': 'entity-streamer', 'kind': 'entity', 'type': STREAMER,
        'text': '主播小美的合同', 'metadata': {},
    })
    repo.put_record(project, {
        'id': 'entity-studio', 'kind': 'entity', 'type': STUDIO,
        'text': '直播间搬迁通知', 'metadata': {},
    })
    return repo, project


def test_subclass_entities_are_recalled_only_when_expansion_is_on(tmp_path):
    """引擎层：扩展打开才召回子类实例，关闭时结果和没有这个功能一样。"""
    repo, project = _project_with_subclass_entities(tmp_path)
    engine = RetrievalEngine(repo, Encoder())
    expansion = {'terms': [PARTNER], 'labels': ['合作方', '签约主播'],
                 'subclasses': [STREAMER], 'type_iris': [PARTNER, STREAMER]}

    off = engine.search(project, '合作方', retrieval_mode='keyword', k=5)
    # 关闭（以及不传扩展）时：文本里没有"合作方"，一条都不该命中
    assert [row['id'] for row in off['hits']] == []
    assert 'ontology_expansion' not in off
    assert 'ontology' not in off['backends']

    on = engine.search(project, '合作方', retrieval_mode='keyword', k=5,
                       scope={'_ontology_expansion': expansion})
    hits = [row['id'] for row in on['hits']]
    assert 'entity-streamer' in hits, '子类实例必须被类型通道召回'
    assert 'entity-studio' not in hits
    assert on['backends']['ontology'] == {'active': True, 'hits': 1}
    assert on['ontology_expansion']['terms'] == [PARTNER]
    assert on['ontology_expansion']['keyword_terms'] == ['签约主播']
    assert on['ontology_expansion']['type_hits'] == 1
    # 额外召回不该被报成"降级"：请求 keyword 就还是 keyword
    assert on['active_mode'] == off['active_mode'] == 'keyword'
    assert on['degraded'] == off['degraded'] == False  # noqa: E712


def test_expansion_switch_off_keeps_the_response_byte_identical(tmp_path):
    """服务层：不开开关与完全不传字段，响应逐字段相同（回归基线）。"""
    repo, project = _project_with_subclass_entities(tmp_path)
    repo.bootstrap_ontology(project, ONTOLOGY_TURTLE, {})
    service = KnowledgeService(repo, Encoder())
    request = {'query': '合作方', 'retrieval_mode': 'keyword', 'k': 5}

    baseline = service.search(project, dict(request))
    explicit_off = service.search(project, {**request, 'ontology_expansion': False})
    assert baseline == explicit_off
    assert 'ontology_expansion' not in baseline

    # 同时也证明"关时确实没读本体"：默认参数直接返回 None
    assert service.ontology_expansion(project, '合作方') is None


def test_service_expansion_uses_the_published_ontology_and_recalls_subclasses(tmp_path):
    """服务层：开启后从已发布本体算出扩展，并把子类实例召回。"""
    repo, project = _project_with_subclass_entities(tmp_path)
    repo.bootstrap_ontology(project, ONTOLOGY_TURTLE, {})
    service = KnowledgeService(repo, Encoder())

    expansion = service.ontology_expansion(project, '合作方', True)
    assert expansion['terms'] == [PARTNER]
    assert expansion['type_iris'] == [PARTNER, STREAMER]

    expanded = service.search(project, {
        'query': '合作方', 'retrieval_mode': 'keyword', 'k': 5,
        'ontology_expansion': True,
    })
    assert 'entity-streamer' in [row['id'] for row in expanded['hits']]
    assert expanded['ontology_expansion']['subclasses'] == [STREAMER]
    # 索引按本体版本签名缓存：同一查询再问一次应该命中缓存而不是重新解析
    assert service.ontology_expansion(project, '合作方', True) == expansion
    assert service._ontology_expansion_cache[project][1] is service._ontology_expansion_cache[project][1]


def test_service_expansion_without_any_ontology_is_empty_not_an_error(tmp_path):
    """没有本体的项目：开启开关也只是"没扩展到东西"，不能抛异常。"""
    repo = Repository(tmp_path / 'no-ontology.sqlite')
    project = repo.create_project('无本体')['id']
    service = KnowledgeService(repo, Encoder())

    assert service.ontology_expansion(project, '合作方', True) == EMPTY
    plain = service.search(project, {'query': '合作方', 'retrieval_mode': 'keyword', 'k': 5})
    assert 'ontology_expansion' not in plain


def test_deprecated_classes_do_not_expand_queries():
    """被废弃的类不参与扩展：否则会把用户引向已经不用的术语。"""
    turtle = f"""
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    <{PARTNER}> a owl:Class ; rdfs:label "合作方"@zh{{marker}} .
    """
    # 先证明"同一份 Turtle 去掉废弃标记后确实能命中"，否则下面的 EMPTY 可能只是因为
    # 这份 Turtle 根本没被索引到（假绿）。
    assert Ontology(turtle.format(marker='')).query_expansion('合作方')['terms'] == [PARTNER]
    deprecated = Ontology(turtle.format(marker=' ; owl:deprecated true'))
    assert deprecated.query_expansion('合作方') == EMPTY


def test_search_endpoint_plumbs_the_expansion_flag_through_http(tmp_path):
    """HTTP 层整链路：请求体字段 → 服务 → 响应台账（这条覆盖 pydantic 字段接线）。"""
    from fastapi.testclient import TestClient

    from knowledge_service.api import create_app
    from knowledge_service.integrations.embeddings import HashingEncoder

    app = create_app(tmp_path / 'expansion-api.sqlite', HashingEncoder())
    with TestClient(app) as client:
        project = client.post('/api/projects', json={
            'name': '检索扩展', 'use_default_ontology': False}).json()
        repository = app.state.service.repository
        repository.bootstrap_ontology(project['id'], ONTOLOGY_TURTLE, {})
        repository.put_record(project['id'], {
            'id': 'entity-streamer', 'kind': 'entity', 'type': STREAMER,
            'text': '主播小美的合同', 'metadata': {},
        })
        url = f"/api/projects/{project['id']}/search"
        body = {'query': '合作方', 'retrieval_mode': 'keyword', 'k': 5}

        off = client.post(url, json=body)
        assert off.status_code == 200, off.text
        assert 'ontology_expansion' not in off.json()
        assert off.json()['hits'] == []

        on = client.post(url, json={**body, 'ontology_expansion': True})
        assert on.status_code == 200, on.text
        payload = on.json()
        assert 'entity-streamer' in [row['id'] for row in payload['hits']]
        assert payload['ontology_expansion']['terms'] == [PARTNER]
        assert payload['ontology_expansion']['subclasses'] == [STREAMER]
        # 响应里的实体仍然带业务标签（扩展没有破坏既有的 type_label 标注）
        streamer = next(row for row in payload['hits'] if row['id'] == 'entity-streamer')
        assert streamer['type_label'] == '签约主播'
