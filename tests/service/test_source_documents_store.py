"""Fixed source versions preserve bytes, ownership and source spans."""
import pytest
from knowledge_service.repository import Repository
from knowledge_service.repository.source_store import SourceStore
from knowledge_service.integrations.source_object_store import SourceObjectStore
from knowledge_service.services.source_documents import SourceDocumentService
from pg_support import target


def test_source_versions_assets_and_project_isolation(tmp_path):
    repo=Repository(target(tmp_path/'sources'))
    project=repo.create_project('Sources')
    other=repo.create_project('Other')
    service=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
    version=service.persist(project['id'],'document',b'# Rule\n\n![asset](image.png)','rules.md',assets=[dict(kind='local',status='available',original_path='image.png',alt='asset',data=b'bytes',source_start_line=3)])
    assert version['version']==1
    assert service.source(project['id'],version['id'])['data']==b'# Rule\n\n![asset](image.png)'
    rendered=service.rendered(project['id'],version['id'])['html']
    assert 'data-block-id=' in rendered
    assert f'/source-documents/{version["id"]}/assets/' in rendered
    asset=version['assets'][0]
    assert service.asset(project['id'],version['id'],asset['id'])['data']==b'bytes'
    with pytest.raises(KeyError): service.asset(other['id'],version['id'],asset['id'])
    newer=service.persist(project['id'],'document',b'# Changed','rules.md')
    assert newer['version']==2
    assert service.get(project['id'],version['id'])['source_text'].startswith('# Rule')
    assert service.locate(project['id'],version['id'],'legacy')['status']=='mapping_unavailable'
    repo.close()


def test_package_source_download_is_fixed_markdown(tmp_path):
    import io,zipfile
    repo=Repository(target(tmp_path/'package'))
    project=repo.create_project('Package')
    service=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive: archive.writestr('sub/rule.md','# Rule')
    versions=service.persist_package(project['id'],buffer.getvalue(),'rules.zip')
    result=service.source(project['id'],versions[0]['id'])
    assert result['data']==b'# Rule'
    assert result['package_data']==buffer.getvalue()
    assert result['original_name']=='sub/rule.md'
    repo.close()


def test_ocr_receipt_reuse_failure_and_ignore(tmp_path):
    from knowledge_service.integrations.image_ocr import OCRError
    class Adapter:
        engine='test';languages='ch';configuration_fingerprint='config'
        calls=0; low=False
        def recognize(self,data):
            self.calls+=1
            return dict(engine_version='1',preprocess_fingerprint='pre',width=100,height=50,
                blocks=[dict(text='安全规则',normalized_text='安全规则',confidence=.2 if self.low else .95,x=.1,y=.2,width=.5,height=.3,page_or_frame=1)])
    repo=Repository(target(tmp_path/'ocr'))
    project=repo.create_project('OCR')
    adapter=Adapter()
    service=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'),adapter)
    version=service.persist(project['id'],'doc',b'![x](x.png)','a.md',assets=[dict(kind='local',status='available',original_path='x.png',alt='x',data=b'image')])
    asset=version['assets'][0]
    one=service.image_ocr.process_asset(project['id'],version['id'],asset['id'])
    two=service.image_ocr.process_asset(project['id'],version['id'],asset['id'])
    assert one['id']==two['id'] and one['blocks'][0]['id']==two['blocks'][0]['id']
    assert adapter.calls==1
    adapter.low=True
    with pytest.raises(OCRError,match='quality'):
        service.image_ocr.process_asset(project['id'],version['id'],asset['id'],retry=True)
    runs=service.store.ocr_runs(project['id'],version['id'],asset['id'])
    assert len(runs)==2 and runs[-1]['error_code']=='ocr_low_confidence'
    ignored=service.image_ocr.ignore(project['id'],version['id'],asset['id'],'actor','no knowledge')
    assert ignored['ignored_by']=='actor' and ignored['ignore_reason']=='no knowledge'
    assert service.image_ocr.process_asset(project['id'],version['id'],asset['id'])['status']=='ignored'
    repo.close()

def test_source_spans_are_project_scoped_and_immutable(tmp_path):
    repo=Repository(target(tmp_path/'spans'))
    project=repo.create_project('Spans'); other=repo.create_project('Other')
    service=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'))
    version=service.persist(project['id'],'doc',b'# Rule\n\nRequirement','rule.md')
    chunk=repo.put_record(project['id'],{'id':'chunk','kind':'chunk','text':'Requirement'})
    span=dict(block_id=version['blocks'][1]['block_id'],chunk_start_char=0,chunk_end_char=11,block_start_char=0,block_end_char=11)
    service.store.add_spans(project['id'],version['id'],chunk['version_id'],[span])
    service.store.add_spans(project['id'],version['id'],chunk['version_id'],[span])
    location=service.locate(project['id'],version['id'],chunk['version_id'])
    assert location['block_id']==span['block_id'] and len(location['spans'])==1
    with pytest.raises(ValueError,match='conflict'):
        service.store.add_spans(project['id'],version['id'],chunk['version_id'],[dict(span,chunk_end_char=10)])
    foreign=repo.put_record(other['id'],{'id':'chunk','kind':'chunk','text':'Other'})
    with pytest.raises(Exception): service.store.add_spans(project['id'],version['id'],foreign['version_id'],[span])
    assert service.collect_unreferenced_objects()==[]
    repo.close()
