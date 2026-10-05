"""实体详情与图谱节点要能看出"父类"。

用户的反馈原话："检索 没看到有父类的信息"、"知识图谱也没父类相关的信息"。
层级长在**类**之间（rdfs:subClassOf），实体节点上原本什么都没有，所以后端要在
实体记录上补出它所属类的上位类，前端才有东西可显示。

这个文件锁定四条契约：
1. 实体载荷带 ``class_label`` / ``class_parents`` / ``class_ancestors``；
2. ``class_ancestors`` 是**从直接父类往上的有序链**（不是集合，否则界面上的
   "父类：A → B" 会随迭代顺序跳动）；
3. 顶层类返回空列表 —— 前端据此说"顶层类（本体里没有父类）"，后端不编造父类；
4. ``/subgraph`` 的实体节点与 ``/records/{record_id}`` 用的是同一份数据
   （``service.ontology_family``），两处不能各算各的，否则同一张卡片两个说法。
"""
from fastapi.testclient import TestClient

from knowledge_service.api import create_app
from knowledge_service.integrations.embeddings import HashingEncoder


# 三层继承：顶层类 ← 电商平台 ← 价格类型；另有一个不在继承链上的孤立类。
HIERARCHY = '''
@prefix ex: <https://example.test/hierarchy#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
ex:顶层类 a owl:Class ; rdfs:label "顶层类" .
ex:电商平台 a owl:Class ; rdfs:label "电商平台" ; rdfs:subClassOf ex:顶层类 .
ex:价格类型 a owl:Class ; rdfs:label "价格类型" ; rdfs:subClassOf ex:电商平台 .
ex:孤立类 a owl:Class ; rdfs:label "孤立类" .
'''


def _project(tmp_path):
    """建一个项目：写入本体层级 + 四个实体（分别落在三种类上）。"""
    app = create_app(tmp_path / 'class-parents.sqlite', HashingEncoder())
    client = TestClient(app)
    project_id = client.post('/api/projects', json={
        'name': '父类可见性', 'use_default_ontology': False}).json()['id']
    client.post(f'/api/projects/{project_id}/ontologies', json={'turtle': HIERARCHY})
    client.post(f'/api/projects/{project_id}/records', json={'records': [
        {'id': 'child', 'kind': 'entity', 'type': 'https://example.test/hierarchy#价格类型',
         'text': '实体门店指导价'},
        {'id': 'middle', 'kind': 'entity', 'type': 'https://example.test/hierarchy#电商平台',
         'text': '某电商平台'},
        {'id': 'root', 'kind': 'entity', 'type': 'https://example.test/hierarchy#顶层类',
         'text': '顶层那个人'},
        {'id': 'orphan', 'kind': 'entity', 'type': 'https://example.test/hierarchy#孤立类',
         'text': '孤立实体'},
    ]})
    return client, project_id


def _subgraph_nodes(client, project_id):
    response = client.post(f'/api/projects/{project_id}/subgraph',
                           json={'kinds': ['entity'], 'attribute_mode': 'none'})
    assert response.status_code == 200, response.text
    return {row['id']: row for row in response.json()['nodes']}


def test_entity_node_carries_its_class_and_the_ordered_ancestor_chain(tmp_path):
    client, project_id = _project(tmp_path)
    node = _subgraph_nodes(client, project_id)['child']
    assert node['class_label'] == '价格类型'
    assert [parent['label'] for parent in node['class_parents']] == ['电商平台']
    # 祖先链是"从直接父类往上"的有序链，父类也必须出现在链首（前端把它读成 价格类型 → …）。
    assert [ancestor['label'] for ancestor in node['class_ancestors']] == ['电商平台', '顶层类']
    assert node['class_parents'][0]['id'].endswith('电商平台')


def test_top_level_class_reports_no_parent_instead_of_inventing_one(tmp_path):
    client, project_id = _project(tmp_path)
    nodes = _subgraph_nodes(client, project_id)
    assert nodes['root']['class_parents'] == []
    assert nodes['root']['class_ancestors'] == []
    assert nodes['root']['class_label'] == '顶层类'
    # 不在任何继承链上的类同样只能是空：它没有父类，不是"读不到"。
    assert nodes['orphan']['class_parents'] == []
    assert nodes['orphan']['class_label'] == '孤立类'


def test_record_endpoint_and_subgraph_agree_on_the_same_family(tmp_path):
    client, project_id = _project(tmp_path)
    record = client.get(f'/api/projects/{project_id}/records/child').json()
    node = _subgraph_nodes(client, project_id)['child']
    assert record['class_parents'] == node['class_parents']
    assert record['class_ancestors'] == node['class_ancestors']
    assert record['class_label'] == node['class_label'] == '价格类型'


def test_non_entity_records_are_left_alone(tmp_path):
    """关系/属性没有"父类"这回事：不能给它们凭空挂上一个空的家族字段，前端会误显示。"""
    client, project_id = _project(tmp_path)
    client.post(f'/api/projects/{project_id}/records', json={'records': [
        {'id': 'rel', 'kind': 'relation', 'type': 'https://example.test/hierarchy#属于',
         'text': '关系', 'subject_id': 'child', 'object_id': 'middle'},
    ]})
    record = client.get(f'/api/projects/{project_id}/records/rel').json()
    assert 'class_parents' not in record
    assert 'class_ancestors' not in record
