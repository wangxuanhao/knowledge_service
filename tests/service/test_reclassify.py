"""C2「受控重分类」契约：本体出了新版本，把还挂在旧版本上的知识迁到当前本体。

对应《完整落地规划》§4.2 与 §8 的 C2 验收口径：
    造一次 v1→v2 迁移 → 断言旧知识**产生新版本（不原地改）**、旧版本**仍可查**、
    整批**可一键撤销**。

三条硬约束各有一组断言：

* 默认不跑 —— 空选、选到「没有去处」的组、选了不存在的组，都必须当面拒绝且一行不改；
* 不原地改 —— 迁移 = 新版本（``record_versions`` 多一行），旧版本按 ``known_at`` 仍读得到；
* 可回滚 —— 整批写成**一条**审计操作，走与合并/删除同一个 ``undo`` 通道整批回退。

另外钉住一件最容易悄悄坏掉的事：**预演与真正写入必须是同一个结论**（服务端把
校验抽成 ``KnowledgeService._timeline_check`` 就是为了这个），以及
「停用类时指定的替代类」与这里的重分类**共用一份数据**（``dcterms:isReplacedBy``）。
"""
import json

import pytest
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.repository import Repository
from knowledge_service.services.governance import Governance
from knowledge_service.services.ontology import Ontology
from knowledge_service.services.ontology_vocabulary import local_name
from knowledge_service.services.reclassify import Reclassify, ReclassifyError
from knowledge_service.services.service import KnowledgeService

#: v1：包裹（留下）、包（要改名）、箱子（要消失）、存放（关系类型，留下）
V1 = '''
@prefix ex: <https://example.test/ns#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:包裹 a owl:Class ; rdfs:label "包裹" .
ex:包 a owl:Class ; rdfs:label "包" .
ex:箱子 a owl:Class ; rdfs:label "箱子" .
ex:存放 a owl:ObjectProperty ; rdfs:label "存放于" .
'''

#: v2：包裹还在；包被停用并指定了替代类「包裹」；箱子直接没有了（没有去处）；存放还在
V2 = '''
@prefix ex: <https://example.test/ns#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
ex:包裹 a owl:Class ; rdfs:label "包裹" .
ex:包 a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:包裹 .
ex:存放 a owl:ObjectProperty ; rdfs:label "存放于" .
'''

#: 替代映射记在**旧版本**上：v1 里类还活着 → 写进去 → v2 停用它并指定替代类 → v3 干脆删掉
V1_ACTIVE = '''
@prefix ex: <https://example.test/ns#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:包裹 a owl:Class ; rdfs:label "包裹" .
ex:包 a owl:Class ; rdfs:label "包" .
'''

V2_RETIRED = '''
@prefix ex: <https://example.test/ns#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
ex:包裹 a owl:Class ; rdfs:label "包裹" .
ex:包 a owl:Class ; owl:deprecated true ; dcterms:isReplacedBy ex:包裹 .
'''

V3_WITHOUT_RETIRED = '''
@prefix ex: <https://example.test/ns#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
ex:包裹 a owl:Class .
'''

#: 新版本给关系加了 domain/range 约束 —— 用来造一个"迁过去会被拦下"的真实场景
V1_LOOSE = '''
@prefix ex: <https://example.test/ns#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
ex:包裹 a owl:Class .
ex:站点 a owl:Class .
ex:存放 a owl:ObjectProperty .
'''

V2_CONSTRAINED = '''
@prefix ex: <https://example.test/ns#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:包裹 a owl:Class .
ex:站点 a owl:Class .
ex:存放 a owl:ObjectProperty ; rdfs:domain ex:包裹 ; rdfs:range ex:包裹 .
'''


def _service(tmp_path, name):
    repo = Repository(tmp_path / name)
    service = KnowledgeService(repo, HashingEncoder())
    project = repo.create_project('迁移')
    return repo, service, project['id']


def _rows(service, project_id, known_at=None):
    scope = {'known_at': known_at} if known_at else {}
    return {row['id']: row for row in service.scoped(project_id, scope)}


@pytest.fixture
def system(tmp_path):
    """v1 上写着四条知识（含一条关系和一堆要改名的类），随后发布 v2。"""
    repo, service, p = _service(tmp_path, 'reclassify.sqlite')
    v1 = repo.save_ontology(p, V1, Ontology(V1).summary())
    service.write(p, [
        {'id': 'e1', 'kind': 'entity', 'type': '包裹', 'text': '一号包裹',
         'ontology_id': v1['id']},
        {'id': 'e2', 'kind': 'entity', 'type': '箱子', 'text': '一只箱子',
         'ontology_id': v1['id']},
        {'id': 'e3', 'kind': 'entity', 'type': '包', 'text': '旧名叫包的东西',
         'ontology_id': v1['id']},
        {'id': 'r1', 'kind': 'relation', 'type': '存放', 'text': '一号包裹存放于自身',
         'subject_id': 'e1', 'object_id': 'e1', 'ontology_id': v1['id']},
    ])
    v2 = repo.save_ontology(p, V2, Ontology(V2).summary())
    return repo, service, p, v1, v2


def _keys(plan, action):
    return [group['key'] for group in plan['groups'] if group['action'] == action]


def test_plan_groups_stale_knowledge_by_old_type_with_three_outcomes(system):
    """面板口径：只列挂在旧版本上的知识，每组给出三档处置之一。"""
    _, service, p, v1, v2 = system
    plan = Reclassify(service).plan(p)

    assert plan['current_ontology_id'] == v2['id']
    assert plan['stale_records'] == 4, '四条知识都还挂在 v1 上'
    assert plan['total_records'] == 4
    assert plan['migratable_records'] == 3 and plan['unmapped_records'] == 1
    assert plan['current_version'].endswith('（当前）')

    groups = {group['key']: group for group in plan['groups']}
    carry = groups[f"{v1['id']}:entity:包裹"]
    assert carry['action'] == 'carry' and carry['migratable']
    assert carry['from_label'] == '包裹' and carry['to_label'] == '包裹'
    assert carry['from_version'].endswith('（旧版）')
    assert [item['id'] for item in carry['records']] == ['e1']
    assert carry['record_count'] == 1

    # 关系类型的知识也要能迁（它挂的同样是旧本体）
    relation = groups[f"{v1['id']}:relation:存放"]
    assert relation['action'] == 'carry' and relation['kind_label'] == '关系'

    rename = groups[f"{v1['id']}:entity:包"]
    assert rename['action'] == 'rename'
    assert local_name(rename['to_type']) == '包裹', '替代映射来自 dcterms:isReplacedBy'

    unmapped = groups[f"{v1['id']}:entity:箱子"]
    assert unmapped['action'] == 'unmapped' and not unmapped['migratable']
    assert unmapped['to_type'] is None
    assert '本体建模层' in unmapped['note'], '没有去处必须说清下一步去哪'

    # 页面文案与服务端同一口径（三档定义 + 三条硬约束）
    assert plan['definitions']['carry'] == '原样搬到新版本'
    assert plan['definitions']['unmapped'] == '没有去处'
    assert len(plan['constraints']) == 3


def test_plan_is_read_only_and_only_lists_stale_knowledge(system):
    """「看」不改任何东西；已经迁过的知识不再出现在面板里。"""
    _, service, p, v1, v2 = system
    before = {row['id']: row['version'] for row in service.scoped(p, {})}
    Reclassify(service).plan(p)
    Reclassify(service).preview(p, _keys(Reclassify(service).plan(p), 'carry'))
    after = {row['id']: row['version'] for row in service.scoped(p, {})}
    assert after == before, '只读接口不许动版本号'
    assert {row['id']: row['ontology_id'] for row in service.scoped(p, {})} \
        == {row_id: v1['id'] for row_id in after}


def test_migration_creates_new_versions_and_keeps_old_readable(system):
    """不原地改：每条迁移产生新版本；旧版本按时间点仍读得到；没选的组一条不动。"""
    repo, service, p, v1, v2 = system
    before = _rows(service, p)
    history_before = len(repo.history(p, 'e1'))
    keys = _keys(Reclassify(service).plan(p), 'carry') + \
        _keys(Reclassify(service).plan(p), 'rename')

    result = Reclassify(service).apply(p, keys, '审核员')

    assert result['migrated'] == 3
    assert result['operation_id']
    assert result['undo']['operation_id'] == result['operation_id']
    after = _rows(service, p)
    assert after['e1']['ontology_id'] == v2['id']
    assert after['e1']['version'] == before['e1']['version'] + 1, '版本 +1 而不是原地改'
    assert len(repo.history(p, 'e1')) == history_before + 1
    assert local_name(after['e3']['type']) == '包裹', '改名组连类型一起改'
    # 旧版本仍可查：按迁移前的时间点读，看到的还是 v1 的归属
    old = _rows(service, p, known_at=before['e1']['recorded_at'])
    assert old['e1']['ontology_id'] == v1['id']
    assert local_name(old['e3']['type']) == '包'
    # 没有选中的「没有去处」那条：一条都没动
    assert after['e2']['ontology_id'] == v1['id']
    assert after['e2']['version'] == before['e2']['version']
    assert Reclassify(service).plan(p)['stale_records'] == 1, '迁完只剩那条没去处的'


def test_batch_migration_is_one_undoable_operation(system):
    """可回滚：整批写成一条操作，走既有 undo 通道整批回到迁移前。"""
    _, service, p, v1, v2 = system
    before = _rows(service, p)
    plan = Reclassify(service).plan(p)
    result = Reclassify(service).apply(
        p, _keys(plan, 'carry') + _keys(plan, 'rename'), '审核员')

    operations = Governance(service).operations(p)
    audit = next(item for item in operations
                 if item['metadata'].get('operation_id') == result['operation_id'])
    assert audit['metadata']['operation'] == 'reclassify'
    assert audit['metadata']['reclassify']['to_ontology_id'] == v2['id']
    assert len(audit['metadata']['before']) == 3, '一次迁移 = 一条操作，覆盖全部 3 条'

    restored = Governance(service).undo_merge(p, result['operation_id'])
    assert restored['restored'] == 3
    after = _rows(service, p)
    assert after['e1']['ontology_id'] == v1['id']
    assert local_name(after['e3']['type']) == '包', '类型也要回退'
    assert Reclassify(service).plan(p)['stale_records'] == 4, '回退后面板重新全部列出'


def test_apply_refuses_empty_selection_and_groups_without_a_target(system):
    """默认不跑：空选、选到「没有去处」、选到不存在的组，都当面拒绝且一行不改。"""
    _, service, p, v1, _ = system
    before = {row['id']: row['version'] for row in service.scoped(p, {})}
    plan = Reclassify(service).plan(p)
    without_target = _keys(plan, 'unmapped')

    with pytest.raises(ReclassifyError, match='没有指定要迁移的组'):
        Reclassify(service).apply(p, [], '审核员')
    with pytest.raises(ReclassifyError, match='没有去处'):
        Reclassify(service).apply(p, without_target, '审核员')
    with pytest.raises(ReclassifyError, match='已经不成立'):
        Reclassify(service).apply(p, [f'{v1["id"]}:entity:不存在的类'], '审核员')

    assert {row['id']: row['version'] for row in service.scoped(p, {})} == before


def test_rename_falls_back_to_replacement_mapping_recorded_on_older_version(tmp_path):
    """「停用类时指定替代类」与重分类共用一份数据：映射记在**中间版本**上也认得。

    现实序列：v1 里类还活着（知识就是那时写进来的）→ v2 停用它并指定替代类 →
    v3 干脆把这个类删了。只看当前版本会误判成"没有去处"，把本来能自动迁的知识丢给人工。
    """
    repo, service, p = _service(tmp_path, 'reclassify-fallback.sqlite')
    v1 = repo.save_ontology(p, V1_ACTIVE, Ontology(V1_ACTIVE).summary())
    service.write(p, [{'id': 'e1', 'kind': 'entity', 'type': '包', 'text': '旧名叫包的东西',
                       'ontology_id': v1['id']}])
    repo.save_ontology(p, V2_RETIRED, Ontology(V2_RETIRED).summary())
    v3 = repo.save_ontology(p, V3_WITHOUT_RETIRED, Ontology(V3_WITHOUT_RETIRED).summary())

    plan = Reclassify(service).plan(p)
    group = plan['groups'][0]
    assert group['action'] == 'rename'
    assert local_name(group['to_type']) == '包裹', '映射来自那份停用记录，不在当前版本里'

    result = Reclassify(service).apply(p, [group['key']], '审核员')
    assert result['migrated'] == 1
    after = _rows(service, p)
    assert after['e1']['ontology_id'] == v3['id']
    assert local_name(after['e1']['type']) == '包裹'


def test_preview_and_write_reach_the_same_verdict(tmp_path):
    """预演与真正写入同一个结论：预演说会被拦，写入就必须一条都不写。

    场景是真实会遇到的：新本体收紧了关系的 domain/range，而旧知识里那条关系的端点
    类型**在新本体里还在**（所以不会被「结构待定」豁免）—— 这种就一定会被拦。
    """
    repo, service, p = _service(tmp_path, 'reclassify-verdict.sqlite')
    v1 = repo.save_ontology(p, V1_LOOSE, Ontology(V1_LOOSE).summary())
    service.write(p, [
        {'id': 'e1', 'kind': 'entity', 'type': '包裹', 'text': '一号包裹',
         'ontology_id': v1['id']},
        {'id': 'e2', 'kind': 'entity', 'type': '站点', 'text': '一个站点',
         'ontology_id': v1['id']},
        {'id': 'r1', 'kind': 'relation', 'type': '存放', 'text': '包裹存放于站点',
         'subject_id': 'e1', 'object_id': 'e2', 'ontology_id': v1['id']},
    ])
    repo.save_ontology(p, V2_CONSTRAINED, Ontology(V2_CONSTRAINED).summary())

    plan = Reclassify(service).plan(p)
    # 只迁实体、不迁那条关系：关系还挂在 v1 上，新本体管不到它 → 预演必须说"能迁"
    entity_only = [group['key'] for group in plan['groups']
                   if group['action'] == 'carry' and group['kind'] == 'entity']
    assert Reclassify(service).preview(p, entity_only)['blocked'] is False
    # 连关系一起迁：新本体的 domain/range 是「包裹」，端点却是「站点」→ 必被拦
    everything = [group['key'] for group in plan['groups'] if group['migratable']]
    preview = Reclassify(service).preview(p, everything)
    assert preview['blocked'] is True
    assert preview['validation']['errors'], '被拦就得说清是哪几条、为什么'
    assert preview['would_change'] == 3

    with pytest.raises(ReclassifyError, match='会被本体校验拦下'):
        Reclassify(service).apply(p, everything, '审核员')
    assert {row['id']: row['ontology_id'] for row in service.scoped(p, {})} \
        == {'e1': v1['id'], 'e2': v1['id'], 'r1': v1['id']}, '被拦 = 一条都没写'
    # 预演说通过的那批：写入就必须真的通过（同一口径的另一半）
    assert Reclassify(service).apply(p, entity_only, '审核员')['migrated'] == 2


def test_clearing_structure_pending_hands_the_batch_over_to_reclassify(tmp_path):
    """连接点①→②：概念补进本体后「结构待定」自动解除，但这批知识**还得迁**。

    规划 §4.1 末句写的就是这条链路（"收下之后那批知识完成一次受控重分类"）。
    两件事必须分清，否则用户会以为"标记没了就万事大吉"：
      ① 结构待定解除 = 概念进本体了（读取侧推导出来的状态）；
      ② 本体归属迁移 = 这条知识还挂在旧版本上（这里才动手，且默认不跑）。
    """
    repo, service, p = _service(tmp_path, 'reclassify-handoff.sqlite')
    v1 = repo.save_ontology(p, V1, Ontology(V1).summary())
    service.write(p, [{'id': 'e9', 'kind': 'entity', 'type': '站点', 'text': '一个站点',
                       'ontology_id': v1['id']}])
    assert service.structure_pending_report(p)['pending_structure'] == 1, 'C1：先被标成结构待定'

    # 本体补上「站点」这个概念并发布新版本（C1 收件箱的那条出路）
    turtle = V1 + 'ex:站点 a owl:Class ; rdfs:label "站点" .\n'
    v2 = repo.save_ontology(p, turtle, Ontology(turtle).summary())
    assert v2['id'] != v1['id']
    cleared = _rows(service, p)['e9']['structure_pending']
    assert cleared['state'] == 'cleared', '概念进本体后状态自动解除（不需要人工清标记）'
    assert service.structure_pending_report(p)['pending_structure'] == 0

    plan = Reclassify(service).plan(p)
    assert plan['stale_records'] == 1, '标记解除了，但它仍挂在旧版本体上 —— C2 的活'
    group = plan['groups'][0]
    assert group['action'] == 'carry'
    assert group['from_label'] == '站点'

    assert Reclassify(service).apply(p, [group['key']], '审核员')['migrated'] == 1
    assert _rows(service, p)['e9']['ontology_id'] == v2['id']
    assert Reclassify(service).plan(p)['stale_records'] == 0


def test_reclassify_endpoints_are_read_only_until_confirmed(tmp_path):
    """HTTP 契约：GET 看 / POST preview 干跑都不改库；确认后才迁移，撤销走既有通道。"""
    app = create_app(tmp_path / 'reclassify-api.sqlite', encoder=HashingEncoder())
    service = app.state.service
    repo = service.repository
    p = repo.create_project('迁移 API')['id']
    v1 = repo.bootstrap_ontology(p, V1, {})
    service.write(p, [
        {'id': 'e1', 'kind': 'entity', 'type': '包裹', 'text': '一号包裹',
         'ontology_id': v1['id']},
    ])
    repo.save_ontology(p, V2, Ontology(V2).summary())

    with TestClient(app) as client:
        response = client.get(f'/api/projects/{p}/reclassify')
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['stale_records'] == 1
        key = [group['key'] for group in body['groups'] if group['action'] == 'carry'][0]

        preview = client.post(f'/api/projects/{p}/reclassify/preview', json={'groups': [key]})
        assert preview.status_code == 200, preview.text
        assert preview.json()['would_change'] == 1
        assert _rows(service, p)['e1']['ontology_id'] == v1['id'], '预演不写库'

        # 默认不跑：不指定分组 = 422，不是"帮你迁全部"
        assert client.post(f'/api/projects/{p}/reclassify', json={}).status_code == 422

        applied = client.post(f'/api/projects/{p}/reclassify',
                              json={'groups': [key], 'actor': '审核员'})
        assert applied.status_code == 200, applied.text
        operation_id = applied.json()['operation_id']
        assert applied.json()['migrated'] == 1

        undo = client.post(f'/api/projects/{p}/operations/{operation_id}/undo')
        assert undo.status_code == 200, undo.text
        assert undo.json()['restored'] == 1
        assert _rows(service, p)['e1']['ontology_id'] == v1['id']


def test_user_facing_text_carries_no_markdown_markers(system):
    """面板上的文案是**纯文本**（页面不做 markdown 渲染）：出现 `**` 就会原样显示成星号。

    这条是真机截图抓出来的 —— 干跑结论里原本写着"与真正写入\\*\\*同一个\\*\\*校验函数"，
    用户在页面上看到的就是两个星号。凡是往界面送的字符串，都不许带排版记号。
    """
    _, service, p, _, _ = system
    plan = Reclassify(service).plan(p)
    preview = Reclassify(service).preview(
        p, [group['key'] for group in plan['groups'] if group['migratable']])
    blob = json.dumps({'plan': plan, 'preview': preview}, ensure_ascii=False)
    for marker in ('**', '```', '__'):
        assert marker not in blob, f'面向用户的文案里出现了 {marker}（页面上会原样显示成记号）'
