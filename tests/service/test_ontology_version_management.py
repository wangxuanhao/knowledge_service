# -*- coding: utf-8 -*-
"""版本管理（发布说明必填 / 版本列表 / 查看历史版本 / 回到某一版）的服务端契约。

用户原话：「你是不是应该有个版本回退的能力，然后可以切换不同的版本的能力……
版本也是需要人手动发布后确认成版本。版本管理发布要写对应的描述，进行确认。」

版本管理的四条语义在这里钉住，任何一条被改掉，界面上的「版本管理」就会变成
一个没人敢用的开关：

  ① **发布说明必填**（服务端裁决，不是界面的软提示）：缺少 note → 422；
     note 只有空白 → 400（strip 后为空，一样不算说明）。
     只用界面校验挡不住的调用方，会在版本列表里留下一排没人看得懂的版本。
  ② **版本列表回答四件事**：第几版 / 什么时候发的 / 谁发的 / 为什么发。
     版本号按发布顺序**现算**（库里不存序号，存了就会与顺序脱节）。
  ③ **历史版本可读**：GET /ontology?ontology_id=<v1> 拿得到那一版的结构 ——
     这是界面上「查看这一版（只读）」的数据来源，读不到就只能给一张空画布。
  ④ **回到某一版 = 再发一版**：历史版本不可变。以 v1 为基础开一份新草案并发布，
     得到的是**新版本**，v1/v2 原样保留；新版本的 base_ontology_id 指向 v1。
     不允许"把生产切回旧结构"这种没有发布记录的写法。
"""
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder

TTL = '''@prefix : <https://test/> . @prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
:Thing a owl:Class ; rdfs:label "事物" .
:Person a owl:Class ; rdfs:label "人员" ; rdfs:subClassOf :Thing .'''

THING = 'https://test/Thing'
PERSON = 'https://test/Person'


def setup(tmp_path):
    app = create_app(tmp_path / 'version-mgmt.sqlite', HashingEncoder())
    client = TestClient(app)
    client.__enter__()
    p = client.post('/api/projects',
                    json={'name': '版本管理', 'use_default_ontology': False}).json()['id']
    base = f'/api/projects/{p}'
    first = client.post(base + '/ontologies', json={'turtle': TTL})
    assert first.status_code == 201, first.text
    return client, app, p, base, first.json()


def new_draft(client, base, ontology_id, title):
    response = client.post(base + '/ontology-drafts', json={
        'base_ontology_id': ontology_id, 'source_kind': 'manual',
        'title': title, 'actor': 'tester'})
    assert response.status_code == 201, response.text
    return response.json()


def add_class(client, base, draft, iri, label):
    response = client.post(base + f"/ontology-drafts/{draft['id']}/commands", json={
        'expected_revision': draft['revision'],
        'command': {'action': 'create_term', 'target_iri': iri, 'kind': 'class',
                    'label_zh': label}})
    assert response.status_code == 200, response.text
    return response.json()


def review(client, base, draft):
    """提交审核 + 一键收下（集合由服务端 review_plan 决定，测试不挑）。"""
    submitted = client.post(base + f"/ontology-drafts/{draft['id']}/submit",
                            json={'expected_revision': draft['revision']})
    assert submitted.status_code == 200, submitted.text
    current = submitted.json()
    approved = client.post(base + f"/ontology-drafts/{draft['id']}/decisions/batch-approve",
                           json={
                               'expected_revision': current['revision'],
                               'expected_ontology_id': current.get('base_ontology_id'),
                               'validation_fingerprint': current['validation_fingerprint'],
                               'acknowledged_warning_codes': warning_codes(current),
                               'actor': 'reviewer'})
    assert approved.status_code == 200, approved.text
    body = approved.json()
    # 一键审核返回 {draft, batch}：批次统计是给界面显示"收下几条"的，
    # 草案本体才是后续发布要用的那份。
    return body['draft'] if isinstance(body, dict) and 'draft' in body else body


def warning_codes(draft):
    report = draft.get('validation_report') or {}
    return [item.get('code') for item in (report.get('warnings') or []) if item.get('code')]


def publish(client, base, draft, note, key='vm-publish', extra_codes=()):
    return client.post(base + f"/ontology-drafts/{draft['id']}/publish", json={
        'expected_revision': draft['revision'],
        'expected_ontology_id': draft.get('base_ontology_id'),
        'validation_fingerprint': draft['validation_fingerprint'],
        'acknowledged_warning_codes': sorted(set(warning_codes(draft)) | set(extra_codes)),
        'idempotency_key': f'{key}-{draft["id"]}',
        'actor': 'publisher',
        'note': note})


def versions(client, base):
    return client.get(base + '/ontologies').json()['versions']


# ---------------------------------------------------------------- ① 发布说明必填

def test_publish_without_note_is_rejected(tmp_path):
    """没写发布说明 → 422。界面拦一道是体验，服务端拦才是裁决。"""
    client, _, _, base, first = setup(tmp_path)
    draft = add_class(client, base, new_draft(client, base, first['id'], '漏写说明'),
                      'https://test/Widget', '部件')
    draft = review(client, base, draft)
    body = {
        'expected_revision': draft['revision'],
        'expected_ontology_id': draft['base_ontology_id'],
        'validation_fingerprint': draft['validation_fingerprint'],
        'acknowledged_warning_codes': warning_codes(draft),
        'idempotency_key': 'no-note-1', 'actor': 'publisher'}
    response = client.post(base + f"/ontology-drafts/{draft['id']}/publish", json=body)
    assert response.status_code == 422, response.text
    # 缺字段被拒 = 没有产生版本，历史必须原样
    assert len(versions(client, base)) == 1


REVERT = 'revert_drops_later_versions'


def revert_draft(client, base, ontology_id, title='回到历史版本'):
    """以历史版本为基线开草案 = 界面上的「回到这一版」。source_kind 必须是 revert：
    服务端只对这个来源放行"基线不是最新版"，并要求发布时勾选回退警告。"""
    response = client.post(base + '/ontology-drafts', json={
        'base_ontology_id': ontology_id, 'source_kind': 'revert',
        'title': title, 'actor': 'tester',
        'source_context': {'revert_from': ontology_id}})
    assert response.status_code == 201, response.text
    return response.json()


def test_blank_note_is_rejected(tmp_path):
    """只有空白的说明不算说明 —— 否则版本列表会出现一排空白。

    两层各拦一部分：空串在请求校验层被拒（422，min_length=1），纯空白在服务端
    被拒（400，strip 后为空）。两边都算"拒了"，所以这里只要求是 4xx。
    """
    client, _, _, base, first = setup(tmp_path)
    draft = add_class(client, base, new_draft(client, base, first['id'], '空白说明'),
                      'https://test/Widget', '部件')
    draft = review(client, base, draft)
    for blank in ('', '   ', '\t\n '):
        response = publish(client, base, draft, blank, key='blank')
        assert response.status_code in (400, 422), f'{blank!r} → {response.status_code}'
    assert len(versions(client, base)) == 1


def test_empty_min_length_is_rejected(tmp_path):
    """空串在请求校验层就被拒（min_length=1），不会走到服务端逻辑。"""
    client, _, _, base, first = setup(tmp_path)
    draft = add_class(client, base, new_draft(client, base, first['id'], '空串说明'),
                      'https://test/Widget', '部件')
    draft = review(client, base, draft)
    response = client.post(base + f"/ontology-drafts/{draft['id']}/publish", json={
        'expected_revision': draft['revision'],
        'expected_ontology_id': draft['base_ontology_id'],
        'validation_fingerprint': draft['validation_fingerprint'],
        'acknowledged_warning_codes': warning_codes(draft),
        'idempotency_key': 'empty-note-1', 'actor': 'publisher', 'note': ''})
    assert response.status_code == 422, response.text


def test_overlong_note_is_rejected(tmp_path):
    """说明限长 500：它是要在版本列表里读的，不设上限就变成了日志转储。"""
    client, _, _, base, first = setup(tmp_path)
    draft = add_class(client, base, new_draft(client, base, first['id'], '超长说明'),
                      'https://test/Widget', '部件')
    draft = review(client, base, draft)
    response = publish(client, base, draft, '说' * 501, key='long')
    assert response.status_code == 422, response.text

    response = publish(client, base, draft, '说' * 500, key='long-ok')
    assert response.status_code == 200, response.text


# ------------------------------------------- ② 版本列表：第几版/何时/谁/为什么

def test_version_list_carries_number_note_actor_and_time(tmp_path):
    client, _, _, base, first = setup(tmp_path)
    draft = add_class(client, base, new_draft(client, base, first['id'], '第一版'),
                      'https://test/Widget', '部件')
    draft = review(client, base, draft)
    published = publish(client, base, draft, '新增「部件」，补齐设备域的对象')
    assert published.status_code == 200, published.text

    listed = versions(client, base)
    assert len(listed) == 2
    # 版本号是 1 起算的发布顺序（第一条是建项目时的初始本体）
    assert [item['version'] for item in listed] == [1, 2]
    newest = listed[-1]
    assert newest['note'] == '新增「部件」，补齐设备域的对象'
    assert newest['actor'] == 'publisher'
    assert newest['created_at']
    assert newest['id'] == published.json()['id']
    assert newest['draft_id'] == draft['id']
    # 初始版本没有发布说明（发布说明上线前就存在）——如实给空串，不编一句话
    assert listed[0]['note'] == ''


def test_history_is_immutable_across_versions(tmp_path):
    """发第二版不能改写第一版的说明与结构 —— 这条是"版本"二字的地基。"""
    client, _, _, base, first = setup(tmp_path)
    draft = add_class(client, base, new_draft(client, base, first['id'], '第一版'),
                      'https://test/Widget', '部件')
    draft = review(client, base, draft)
    assert publish(client, base, draft, '第一版：新增部件').status_code == 200

    history_a = versions(client, base)
    v2 = history_a[-1]

    second = add_class(client, base, new_draft(client, base, v2['id'], '第二版'),
                       'https://test/Label', '标签')
    second = review(client, base, second)
    assert publish(client, base, second, '第二版：新增标签').status_code == 200

    history_b = versions(client, base)
    assert [item['version'] for item in history_b] == [1, 2, 3]
    assert history_b[1]['id'] == v2['id']
    assert history_b[1]['note'] == '第一版：新增部件'
    assert history_b[1]['summary'] == v2['summary']
    assert history_b[2]['note'] == '第二版：新增标签'
    # 新版本的基础指向上一版：版本链可回溯
    assert history_b[2]['base_ontology_id'] == v2['id']


# -------------------------------------------------- ③ 查看历史版本（只读）可读

def test_historical_version_is_readable_by_id(tmp_path):
    """「查看这一版（只读）」要真的读得到那一版：旧版结构不被后来者覆盖。"""
    client, _, _, base, first = setup(tmp_path)
    draft = add_class(client, base, new_draft(client, base, first['id'], '第一版'),
                      'https://test/Widget', '部件')
    draft = review(client, base, draft)
    v2 = publish(client, base, draft, '第一版：新增部件')
    assert v2.status_code == 200, v2.text

    # 第二版必须以**当前版本**（刚发的 v2）为基线：普通草案不许基于旧版
    second = add_class(client, base, new_draft(client, base, v2.json()['id'], '第二版'),
                       'https://test/Label', '标签')
    second = review(client, base, second)
    assert publish(client, base, second, '第二版：新增标签').status_code == 200

    listed = versions(client, base)
    v1, v3 = listed[0]['id'], listed[2]['id']

    old = client.get(base + f'/ontology?ontology_id={v1}')
    assert old.status_code == 200, old.text
    old_classes = {row['id'] for row in old.json()['summary']['classes']}
    new = client.get(base + f'/ontology?ontology_id={v3}').json()
    new_classes = {row['id'] for row in new['summary']['classes']}
    assert PERSON in old_classes
    assert 'https://test/Widget' not in old_classes
    assert 'https://test/Widget' in new_classes
    assert old_classes < new_classes


# ------------------------------------------------ ④ 回到某一版 = 以它为基础再发一版

def test_fork_from_historical_version_publishes_a_new_version(tmp_path):
    """回到 v2 的正确形态：v2 不动，新草案以 v2 为基线，发布成 v3。

    走的是界面上「回到这一版」的那条路：source_kind='revert' + 勾选回退警告。
    """
    client, _, _, base, first = setup(tmp_path)
    draft = add_class(client, base, new_draft(client, base, first['id'], '第一版'),
                      'https://test/Widget', '部件')
    draft = review(client, base, draft)
    assert publish(client, base, draft, '第一版：新增部件').status_code == 200

    listed = versions(client, base)
    v2 = listed[-1]
    target_classes = {row['id'] for row in v2['summary']['classes']}

    # 回退动作 = 以 v2 为 base 开一份新草案（历史不改写）
    forked = revert_draft(client, base, v2['id'])
    assert forked['base_ontology_id'] == v2['id']
    assert forked['source_kind'] == 'revert'
    # 回退的代价要先写清楚，再让人确认
    readiness = client.get(base + f"/ontology-drafts/{forked['id']}/publish-readiness").json()
    revert_items = [item for item in readiness['items'] if item['code'] == REVERT]
    assert revert_items, readiness
    assert revert_items[0]['severity'] == 'warning'
    assert 'v2' in revert_items[0]['message']

    forked = add_class(client, base, forked, 'https://test/Sensor', '传感器')
    forked = review(client, base, forked)
    result = publish(client, base, forked,
                     '回到 v2 修复：在 v2 基础上补「传感器」', extra_codes=[REVERT])
    assert result.status_code == 200, result.text

    final = versions(client, base)
    assert [item['version'] for item in final] == [1, 2, 3]
    # v2 一字未动（结构 + 说明都没变）
    assert final[1]['id'] == v2['id']
    assert final[1]['note'] == v2['note']
    assert {row['id'] for row in final[1]['summary']['classes']} == target_classes
    # 第三版是"回到 v2 之后再发一版"，基线指向 v2
    assert final[2]['base_ontology_id'] == v2['id']
    assert 'https://test/Sensor' in {row['id'] for row in final[2]['summary']['classes']}


def test_revert_without_acknowledgement_is_rejected(tmp_path):
    """回退不能不吭声地发：那条警告是**门禁**，不是列表里的一行字。"""
    client, _, _, base, first = setup(tmp_path)
    draft = add_class(client, base, new_draft(client, base, first['id'], '第一版'),
                      'https://test/Widget', '部件')
    draft = review(client, base, draft)
    assert publish(client, base, draft, '第一版：新增部件').status_code == 200
    v2 = versions(client, base)[-1]

    forked = revert_draft(client, base, v2['id'])
    forked = add_class(client, base, forked, 'https://test/Sensor', '传感器')
    forked = review(client, base, forked)
    refused = publish(client, base, forked, '回到 v2')
    assert refused.status_code == 422, refused.text
    assert refused.json()['code'] == 'validation_failed'
    assert refused.json()['details']['warning_codes'] == [REVERT]
    assert len(versions(client, base)) == 2, '没勾确认却发出了版本'
    # 勾了就能发：门禁是"要确认"，不是"不许回退"
    ok = publish(client, base, forked, '回到 v2', extra_codes=[REVERT])
    assert ok.status_code == 200, ok.text
    assert len(versions(client, base)) == 3


def test_plain_draft_still_may_not_use_a_historical_base(tmp_path):
    """普通草案（source_kind='manual'）仍不许基于旧版 —— 回退的口子只对显式意图开。

    那条保护防的是"在旧基线上做普通编辑会静默丢掉别人后来的改动"，与回退是两件事。
    """
    client, _, _, base, first = setup(tmp_path)
    draft = add_class(client, base, new_draft(client, base, first['id'], '第一版'),
                      'https://test/Widget', '部件')
    draft = review(client, base, draft)
    assert publish(client, base, draft, '第一版：新增部件').status_code == 200

    stale = client.post(base + '/ontology-drafts', json={
        'base_ontology_id': first['id'], 'source_kind': 'manual',
        'title': '拿旧基线做普通编辑', 'actor': 'tester'})
    assert stale.status_code == 409, stale.text
    assert stale.json()['code'] == 'stale_base'

    # revert 但指向一个不存在的版本：也不行（回退不是"随便指一个 id"的旁路）
    ghost = client.post(base + '/ontology-drafts', json={
        'base_ontology_id': 'not-a-real-version', 'source_kind': 'revert',
        'title': '回到不存在的版本', 'actor': 'tester'})
    assert ghost.status_code == 409, ghost.text
    assert ghost.json()['code'] == 'stale_base'


def test_publish_replay_is_idempotent(tmp_path):
    """同一把幂等键重放同一个发布：不产生第二个版本（重试不该重复发版）。"""
    client, _, _, base, first = setup(tmp_path)
    draft = add_class(client, base, new_draft(client, base, first['id'], '重放'),
                      'https://test/Widget', '部件')
    draft = review(client, base, draft)
    payload = {
        'expected_revision': draft['revision'],
        'expected_ontology_id': draft['base_ontology_id'],
        'validation_fingerprint': draft['validation_fingerprint'],
        'acknowledged_warning_codes': warning_codes(draft),
        'idempotency_key': 'replay-key-1', 'actor': 'publisher',
        'note': '幂等重放'}
    first_call = client.post(base + f"/ontology-drafts/{draft['id']}/publish", json=payload)
    assert first_call.status_code == 200, first_call.text
    again = client.post(base + f"/ontology-drafts/{draft['id']}/publish", json=payload)
    assert again.status_code in (200, 409), again.text
    assert len(versions(client, base)) == 2, '重放发布了第二个版本'


def test_pure_revert_publishes_the_old_structure_as_a_new_version(tmp_path):
    """纯回退（零变更）也要能发布 —— 这正是「回退」二字的意思。

    v1 → v2 加了「部件」；回到 v1 就是让当前版本重新变成 v1 的结构。
    回退草案的内容由它的基线（v1）决定，所以**不需要**任何操作；
    代价由回退警告的勾选确认担起来（与 git revert 同构：新版本，树等于旧版本）。
    """
    client, _, _, base, first = setup(tmp_path)
    draft = add_class(client, base, new_draft(client, base, first['id'], '第一版'),
                      'https://test/Widget', '部件')
    draft = review(client, base, draft)
    assert publish(client, base, draft, '第一版：新增部件').status_code == 200

    listed = versions(client, base)
    v1, v2 = listed[0], listed[1]
    v1_classes = {row['id'] for row in v1['summary']['classes']}

    forked = revert_draft(client, base, v1['id'])
    # 零变更：不动画布、不加命令。先提交（回退草案零变更也允许提交）。
    submitted = client.post(base + f"/ontology-drafts/{forked['id']}/submit",
                            json={'expected_revision': forked['revision']})
    assert submitted.status_code == 200, submitted.text
    current = submitted.json()

    readiness = client.get(base + f"/ontology-drafts/{forked['id']}/publish-readiness").json()
    assert readiness['revert'] == v1['id'], '回退草案要自报身份（前端门禁靠它放行）'
    # 零变更不再报 no_operation：那是给"什么都没做的普通草案"准备的门禁
    assert 'no_operation' not in [item['code'] for item in readiness['items']], readiness
    assert 'base_outdated' not in [item['code'] for item in readiness['items']], readiness
    assert REVERT in [item['code'] for item in readiness['items']]
    assert readiness['ready'] is True, readiness

    result = client.post(base + f"/ontology-drafts/{forked['id']}/publish", json={
        'expected_revision': current['revision'],
        # 界面上真正的取值：主控用「项目当前最新本体 id」当乐观锁前置条件
        # （服务端也接受"回到的那一版"这个 id，见下一条用例）。
        'expected_ontology_id': v2['id'],
        'validation_fingerprint': current['validation_fingerprint'],
        'acknowledged_warning_codes': [REVERT],
        'idempotency_key': 'pure-revert-1', 'actor': 'publisher',
        'note': '回退到 v1：设备域方案作废，结构回到 v1'})
    assert result.status_code == 200, result.text

    final = versions(client, base)
    assert [item['version'] for item in final] == [1, 2, 3]
    # v3 的结构等于 v1，且 v2 一字未动
    assert {row['id'] for row in final[2]['summary']['classes']} == v1_classes
    assert final[1]['id'] == v2['id'] and final[1]['note'] == v2['note']
    assert final[2]['note'] == '回退到 v1：设备域方案作废，结构回到 v1'
    assert final[2]['base_ontology_id'] == v1['id']
    # 当前生效版本读到的就是 v1 的结构
    live = client.get(base + '/ontology').json()
    assert {row['id'] for row in live['summary']['classes']} == v1_classes


def test_revert_accepts_the_revert_base_as_expected_ontology(tmp_path):
    """乐观锁前置条件允许两种取值：项目当前最新版，或"我要回到的那一版"。

    前者是界面的取值（"我以为什么是最新的"），后者是调用方自然的表达
    （"我在回到 v1"）。两者都是关于基线的真实陈述，服务端不该把后者判成过期 ——
    事务内复核（repository）与预检（service）两处都要一致，否则会出现
    "预检通过、发布失败"的半通状态。
    """
    client, _, _, base, first = setup(tmp_path)
    draft = add_class(client, base, new_draft(client, base, first['id'], '第一版'),
                      'https://test/Widget', '部件')
    draft = review(client, base, draft)
    assert publish(client, base, draft, '第一版：新增部件').status_code == 200

    v1 = versions(client, base)[0]
    forked = revert_draft(client, base, v1['id'])
    submitted = client.post(base + f"/ontology-drafts/{forked['id']}/submit",
                            json={'expected_revision': forked['revision']})
    assert submitted.status_code == 200, submitted.text
    current = submitted.json()

    result = client.post(base + f"/ontology-drafts/{forked['id']}/publish", json={
        'expected_revision': current['revision'],
        'expected_ontology_id': v1['id'],          # ← 回到的那一版
        'validation_fingerprint': current['validation_fingerprint'],
        'acknowledged_warning_codes': [REVERT],
        'idempotency_key': 'revert-base-precondition-1', 'actor': 'publisher',
        'note': '回退到 v1（前置条件写成目标版本）'})
    assert result.status_code == 200, result.text
    assert len(versions(client, base)) == 3
