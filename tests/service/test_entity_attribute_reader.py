from importlib import import_module, util
import pytest
from knowledge_service.repository.core import Repository
from knowledge_service.services.service import KnowledgeService
from knowledge_service.integrations.embeddings import HashingEncoder


def setup(tmp_path):
    repo=Repository(tmp_path/'attribute-reader');project=repo.create_project('属性阅读')['id']
    repo.put_record(project,{'id':'platform','kind':'entity','text':'美团平台','type':'Platform'})
    for index,value in enumerate(['上海','江苏','浙江','正文。\n'*3000+'尾部条件']):
        repo.put_record(project,{'id':'a'+str(index),'kind':'attribute','subject_id':'platform',
            'type':'region' if index<3 else 'description','text':'属性值',
            'value':value,'datatype':'http://www.w3.org/2001/XMLSchema#string'})
    module='knowledge_service.services.entity_attributes'
    assert util.find_spec(module) is not None,'bounded entity attribute reader is missing'
    return repo,project,import_module(module).EntityAttributes(KnowledgeService(repo,HashingEncoder()))


def test_attribute_reader_groups_multiple_values_and_uses_bounded_preview(tmp_path):
    repo,p,reader=setup(tmp_path)
    page=reader.page(p,'platform',{},limit=2)
    assert len([value for group in page['groups'] for value in group['values']])==2
    assert page['next_cursor']
    next_page=reader.page(p,'platform',{},limit=2,cursor=page['next_cursor'])
    assert next_page['next_cursor'] is None
    assert {g['predicate'] for g in page['groups']+next_page['groups']}=={'region','description'}
    values=[v for group in page['groups']+next_page['groups'] for v in group['values']]
    paragraph=next(v for v in values if v['record_id']=='a3')
    assert paragraph['value_truncated'] is True
    assert len(paragraph['value_preview'])<=400
    full=reader.value(p,'platform','a3',{})
    assert full['value'].endswith('尾部条件')
    assert '\n' in full['value']


def test_attribute_cursor_cannot_be_reused_for_changed_scope_or_entity(tmp_path):
    repo,p,reader=setup(tmp_path)
    first=reader.page(p,'platform',{},limit=1)
    with pytest.raises(ValueError,match='游标'):
        reader.page(p,'platform',{'valid_at':'2025-01-01'},limit=1,cursor=first['next_cursor'])


def test_attribute_reader_obeys_time_and_endpoint_qualification(tmp_path):
    repo,p,reader=setup(tmp_path)
    current=repo.get_record(p,'a0')
    repo.put_record(p,{key:value for key,value in {**current,'valid_from':'2025-01-01','valid_until':'2026-01-01'}.items()
        if key in ('id','kind','type','text','subject_id','value','datatype','metadata','valid_from','valid_until')},expected_version=current['version'])
    page=reader.page(p,'platform',{'valid_at':'2024-01-01'},limit=20)
    assert 'a0' not in [v['record_id'] for g in page['groups'] for v in g['values']]
    with pytest.raises(KeyError):reader.value(p,'platform','a0',{'valid_at':'2024-01-01'})
