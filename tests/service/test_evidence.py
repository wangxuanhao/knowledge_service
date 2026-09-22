import pytest
from knowledge_service.repository import Repository
from knowledge_service.services.service import KnowledgeService
from knowledge_service.integrations.embeddings import HashingEncoder
from knowledge_service.services.evidence import evidence


def test_evidence_pins_source_version_and_scopes_records(tmp_path):
    repo=Repository(tmp_path/'e.sqlite');service=KnowledgeService(repo,HashingEncoder())
    p=repo.create_project('证据')['id']
    doc=repo.put_record(p,{'id':'doc','kind':'document','text':'开始😀商户规则结束','metadata':{'title':'规则原文'}})
    row=repo.put_record(p,{'id':'e','kind':'entity','text':'商户','metadata':{'region':'北京','start_char':3,'end_char':7,'source_version_id':doc['version_id']},'source_id':'doc'})
    repo.put_record(p,{'id':'doc','kind':'document','text':'已经变更的原文'})
    result=evidence(service,p,'e',{})['documents'][0]
    assert result['version']==1
    assert result['highlight']=='商户规则'
    assert result['before']+result['highlight']+result['after']=='开始😀商户规则结束'
    assert result['mode']=='offset'
    with pytest.raises(KeyError):evidence(service,p,'e',{'filters':{'field':'region','op':'eq','value':'上海'}})
    p2=repo.create_project('隔离')['id']
    with pytest.raises(KeyError):evidence(service,p2,'e',{})
    repo.close()


def test_legacy_match_is_labelled_and_missing_source_not_fabricated(tmp_path):
    repo=Repository(tmp_path/'e.sqlite');service=KnowledgeService(repo,HashingEncoder());p=repo.create_project('旧数据')['id']
    repo.put_record(p,{'id':'d','kind':'document','text':'商户应遵守规则。商户可以申诉。'})
    repo.put_record(p,{'id':'e','kind':'entity','text':'商户','source_id':'d'})
    result=evidence(service,p,'e',{})['documents'][0]
    assert result['mode']=='text' and result['highlight']=='商户'
    assert '不代表原始抽取位置' in result['reason']
    repo.put_record(p,{'id':'m','kind':'entity','text':'手工记录'})
    assert evidence(service,p,'m',{})['documents']==[]
    repo.close()
