# -*- coding: utf-8 -*-
"""关系两端（domain / range）的推断与回填 —— 用户反馈「关系没办法显示」的服务端契约。

背景（真机读数）：某个真实项目里 9 个关系类型 **domain / range 全空**，而画布
**只在两端都在图里时才画得出关系线** → 界面上只画出 1 条继承线、0 条关系线，
左栏却写着「关系类型 9」。用户的第一反应就是"关系丢了"。

这里钉住四件事：
  ① 推断口径只有一处出处（services/ontology.py 的 relation_usage）：从**已入库实例**
     反推两端，数字来自 current_records —— 界面不许自己再算一遍，否则两个口径必然打架；
  ② 没有实例依据时如实说"没有依据"，不猜、不编；
  ③ 回填**只能**走草案命令通道（add_domain / add_range），生效本体一个字节都不动；
  ④ 非关系类型的 IRI 一律 404，不要对类/属性也吐一份"两端推断"来误导人。
"""
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder

# :knows 刻意不写 rdfs:domain / rdfs:range —— 复现"抽出来的关系没有两端"这个真实成因
TTL = '''@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
:Thing a owl:Class ; rdfs:label "事物" .
:Person a owl:Class ; rdfs:label "人员" ; rdfs:subClassOf :Thing .
:Organization a owl:Class ; rdfs:label "组织" ; rdfs:subClassOf :Thing .
:knows a owl:ObjectProperty ; rdfs:label "认识" .'''

KNOWS = 'https://test/knows'
PERSON = 'https://test/Person'
ORG = 'https://test/Organization'


def setup(tmp_path):
    app = create_app(tmp_path / 'relation-endpoints.sqlite', HashingEncoder())
    client = TestClient(app)
    client.__enter__()
    p = client.post('/api/projects',
                    json={'name': 'endpoints', 'use_default_ontology': False}).json()['id']
    published = client.post(f'/api/projects/{p}/ontologies', json={'turtle': TTL}).json()
    return client, app, p, f'/api/projects/{p}', published


def seed_knowledge(client, base):
    """播种两条实体 + 一条关系：张三（人员）认识 机构（组织）。"""
    response = client.post(base + '/records', json={'records': [
        {'id': 'person', 'kind': 'entity', 'type': PERSON, 'text': '张三'},
        {'id': 'org', 'kind': 'entity', 'type': ORG, 'text': '机构'},
        {'id': 'edge', 'kind': 'relation', 'type': KNOWS, 'text': '认识',
         'subject_id': 'person', 'object_id': 'org'},
    ]})
    assert response.status_code == 201, response.text


def open_draft(client, base, published, title='验证草案'):
    response = client.post(base + '/ontology-drafts', json={
        'base_ontology_id': published['id'], 'source_kind': 'manual',
        'title': title, 'actor': 'test'})
    assert response.status_code == 201, response.text
    return response.json()


def test_relation_usage_infers_both_endpoints_from_stored_knowledge(tmp_path):
    """有实例依据时：两端都能反推出来，并带上次数、中文名与可核对的原文样例。"""
    c, _app, _p, base, _published = setup(tmp_path)
    try:
        seed_knowledge(c, base)
        response = c.get(base + '/ontology/relation-usage', params={'uri': KNOWS})
        assert response.status_code == 200, response.text
        body = response.json()

        assert body['uri'] == KNOWS and body['kind'] == 'relation'
        # 本体没声明两端 —— 这正是画布画不出线的原因，接口必须如实报告
        assert body['declared'] == {'domain': [], 'range': []}
        assert body['missing'] == {'domain': True, 'range': True}
        # 从已入库实例反推：主体=人员、客体=组织，各 1 次
        assert body['used'] == 1
        assert [(x['iri'], x['count']) for x in body['inferred']['domain']] == [(PERSON, 1)]
        assert [(x['iri'], x['count']) for x in body['inferred']['range']] == [(ORG, 1)]
        # 中文名与样例要能直接摆给用户核对（不能只给 IRI 让人自己猜）
        assert body['inferred']['domain'][0]['label'] == '人员'
        assert body['label'] == '认识'
        sample = body['samples'][0]
        assert (sample['subject'], sample['subject_type']) == ('张三', '人员')
        assert (sample['object'], sample['object_type']) == ('机构', '组织')
    finally:
        c.__exit__(None, None, None)


def test_relation_usage_reports_declared_endpoints_and_clears_missing(tmp_path):
    """两端已声明时：declared 有值、missing 清零，界面据此不再给回填入口。

    同时钉住 draft_id 的语义：画布画的是草案时必须按**草案**报。回填刚写进草案，
    若这里还按已发布版本回一句"未声明"，界面就成了"改了没反应"。
    """
    c, _app, _p, base, published = setup(tmp_path)
    try:
        draft = open_draft(c, base, published)
        response = c.put(base + '/ontology/term', params={'uri': KNOWS}, json={
            'label': '认识', 'domain': PERSON, 'range': ORG,
            'expected_ontology_id': published['id'],
            'draft_id': draft['id'], 'expected_revision': draft['revision']})
        assert response.status_code == 200, response.text

        # 不带 draft_id = 已发布本体：还没声明（这解释了"另一个页面看起来没改"）
        live = c.get(base + '/ontology/relation-usage', params={'uri': KNOWS}).json()
        assert live['source'] == 'published'
        assert live['declared'] == {'domain': [], 'range': []}
        assert live['missing'] == {'domain': True, 'range': True}

        # 带 draft_id = 草案：两端已声明、missing 清零
        usage = c.get(base + '/ontology/relation-usage',
                      params={'uri': KNOWS, 'draft_id': draft['id']}).json()
        assert usage['source'] == 'draft'
        assert usage['declared'] == {'domain': [PERSON], 'range': [ORG]}
        assert usage['missing'] == {'domain': False, 'range': False}
    finally:
        c.__exit__(None, None, None)


def test_relation_usage_rejects_unknown_draft_instead_of_falling_back(tmp_path):
    """draft_id 给了但读不到草案：必须报错，不许静默回落到已发布本体。

    静默回落会让界面显示"未声明"，而用户明明刚在草案里补过两端 —— 又是一个"改了没反应"。
    """
    c, _app, _p, base, _published = setup(tmp_path)
    try:
        response = c.get(base + '/ontology/relation-usage',
                         params={'uri': KNOWS, 'draft_id': 'no-such-draft'})
        assert response.status_code == 404, response.text
    finally:
        c.__exit__(None, None, None)


def test_relation_usage_reports_no_basis_when_relation_is_unused(tmp_path):
    """没有实例用到这条关系时：如实说没有依据，inferred 为空，不猜任何两端。"""
    c, _app, _p, base, _published = setup(tmp_path)
    try:
        body = c.get(base + '/ontology/relation-usage', params={'uri': KNOWS}).json()
        assert body['used'] == 0
        assert body['inferred'] == {'domain': [], 'range': []}
        assert body['samples'] == []
        # 缺两端的事实仍然要报（用户需要知道它为什么画不出线）
        assert body['missing'] == {'domain': True, 'range': True}
    finally:
        c.__exit__(None, None, None)


def test_relation_usage_rejects_non_relation_uri(tmp_path):
    """类 / 属性 / 不存在的 IRI 都不该吐"两端推断"，否则界面上会出现无意义的回填入口。"""
    c, _app, _p, base, _published = setup(tmp_path)
    try:
        for uri in (PERSON, 'https://test/does-not-exist'):
            response = c.get(base + '/ontology/relation-usage', params={'uri': uri})
            assert response.status_code == 404, f'{uri} 不该返回 200：{response.text}'
    finally:
        c.__exit__(None, None, None)


def test_backfill_commands_write_endpoints_into_draft_only(tmp_path):
    """回填只进草案：草案里两端补齐（画布随即能画线），生效本体一个字节不动。

    这条是治理底线 —— 界面上那个「按实际用法写入草案」按钮发出去的就是这两条命令，
    如果哪天下面的断言里"生效本体"也变了，说明有人绕过了草案直写生产本体。
    """
    c, app, p, base, published = setup(tmp_path)
    try:
        seed_knowledge(c, base)
        draft = open_draft(c, base, published)
        # 逐条发，与界面一致（每次改动在草案里各留一条可审的痕）
        for action, value, side in (('add_domain', PERSON, '主体类'), ('add_range', ORG, '客体类')):
            response = c.post(f"{base}/ontology-drafts/{draft['id']}/commands", json={
                'expected_revision': draft['revision'],
                'command': {'action': action, 'target_iri': KNOWS, 'value': value,
                            'reason': f'按实际用法回填关系两端（{side}）'}})
            assert response.status_code == 200, response.text
            draft = c.get(f"{base}/ontology-drafts/{draft['id']}").json()

        term = next(x for x in draft['ontology']['relations'] if x['id'] == KNOWS)
        assert term['domain'] == [PERSON] and term['range'] == [ORG]

        # 草案补齐后接口改口：missing 清零 —— 这正对应"画布能画出这条关系线了"
        # （画布画的是草案，所以这里也要带 draft_id 问）
        usage = c.get(base + '/ontology/relation-usage',
                      params={'uri': KNOWS, 'draft_id': draft['id']}).json()
        assert usage['source'] == 'draft'
        assert usage['missing'] == {'domain': False, 'range': False}

        # 生效本体不受影响：回填没有绕过草案审核
        assert app.state.service.repository.get_ontology(p)['id'] == published['id']
        live = c.get(base + '/ontology').json()
        live_term = next(x for x in live['summary']['relations'] if x['id'] == KNOWS)
        assert live_term['domain'] == [] and live_term['range'] == []
    finally:
        c.__exit__(None, None, None)


def test_new_class_with_parent_lands_in_the_draft_canvas_can_render(tmp_path):
    """「父类加不了」的解法：新建类 + 直接带父类，写进草案，且草案结构可被画布渲染。

    读的是草案自身（GET /ontology-drafts/{id} 的 ontology 字段）—— 画布现在画的就是它，
    所以这条断言同时钉住了"改完立刻看得见"（不必发布、不必刷新）。
    """
    c, _app, _p, base, published = setup(tmp_path)
    try:
        draft = open_draft(c, base, published, title='新建类')
        response = c.post(base + '/ontology/terms', json={
            'kind': 'class', 'uri': '', 'label': 'coupon', 'label_zh': '优惠券',
            'parent': PERSON,
            'expected_ontology_id': published['id'],
            'draft_id': draft['id'], 'expected_revision': draft['revision']})
        assert response.status_code == 201, response.text

        after = c.get(f"{base}/ontology-drafts/{draft['id']}").json()
        created = next(x for x in after['ontology']['classes'] if x['label_zh'] == '优惠券')
        assert created['parents'] == [PERSON], '新建类必须带上父类（否则"父类还是加不了"）'

        # 生效本体不变：新建同样先入草案
        live = c.get(base + '/ontology').json()
        assert all(x['label_zh'] != '优惠券' for x in live['summary']['classes'])
    finally:
        c.__exit__(None, None, None)


def test_terms_route_rejects_stale_expected_ontology_id(tmp_path):
    """界面用 expected_ontology_id 做乐观锁：传的不是当前最新本体 id 就必须 409。

    这条防的是"发布完接着改、改动却基于旧版本"——用户反馈里"发布了就不能再改"的另一面。
    """
    c, _app, _p, base, published = setup(tmp_path)
    try:
        draft = open_draft(c, base, published)
        response = c.post(base + '/ontology/terms', json={
            'kind': 'class', 'uri': '', 'label': 'stale', 'label_zh': '过期',
            'expected_ontology_id': 'not-the-latest-ontology-id',
            'draft_id': draft['id'], 'expected_revision': draft['revision']})
        assert response.status_code in (409, 422), response.text
    finally:
        c.__exit__(None, None, None)
