"""Partial long-image OCR is durable and retries retain only successful tiles."""
import pytest
from source_test_images import png
from knowledge_service.repository import Repository
from knowledge_service.integrations.source_object_store import SourceObjectStore
from knowledge_service.integrations.image_ocr import OCRError
from knowledge_service.services.source_documents import SourceDocumentService
from pg_support import target


def test_partial_tile_receipt_survives_failure_and_retry(tmp_path):
    block = dict(text='完整安全规则', normalized_text='完整安全规则', confidence=.99,
                 x=.1, y=.1, width=.4, height=.2, page_or_frame=1)
    class Adapter:
        engine='test'; languages='ch'; configuration_fingerprint='tile-config'
        calls=0; received=None
        def recognize(self, raw):
            raise AssertionError('Progress-aware adapter must use tile interface')
        def recognize_with_progress(self, raw, *, resume_tiles=None, on_tile=None):
            self.calls += 1
            self.received = resume_tiles
            tile = dict(id='tile-000001', ordinal=0, tiles_total=2, region=[0,0,100,100],
                        status='completed', blocks=[block], configuration_fingerprint='tile-config')
            on_tile(tile)
            result = dict(engine_version='1', preprocess_fingerprint='tiles', width=100, height=200,
                          blocks=[block], tiles=[tile, dict(id='tile-000002', ordinal=1, tiles_total=2,
                          region=[0,100,100,200], status='failed', blocks=[])], tiles_total=2, tiles_completed=1)
            if self.calls == 1:
                error=OCRError('ocr_partial', 'One image region failed')
                error.partial_result=result
                raise error
            result['tiles'][1]['status']='completed'
            result['tiles_completed']=2
            return result
    repo=Repository(target(tmp_path/'tile-receipt'))
    try:
        project=repo.create_project('Long image receipt')['id']
        adapter=Adapter()
        service=SourceDocumentService(repo, SourceObjectStore(tmp_path/'objects'), adapter)
        source=service.persist(project,'doc',b'![](a.png)','a.md',assets=[dict(
            kind='local',status='available',original_path='a.png',data=png())])
        asset=source['assets'][0]['id']
        with pytest.raises(OCRError):
            service.image_ocr.process_asset(project,source['id'],asset)
        run=service.store.ocr_runs(project,source['id'],asset)[-1]
        assert run['status']=='partial'
        assert run['processing_details']['tiles_completed']==1
        assert run['processing_details']['tiles_total']==2
        assert run['normalized_text']=='完整安全规则'
        assert len(run['blocks'])==1
        completed=service.image_ocr.process_asset(project,source['id'],asset,retry=True)
        assert adapter.received[0]['status']=='completed'
        assert completed['status']=='completed'
        assert completed['processing_details']['tiles_completed']==2
        runs=service.store.ocr_runs(project,source['id'],asset)
        assert len(runs)==2 and runs[0]['status']=='partial'
        assert runs[0]['blocks'][0]['id']!=runs[1]['blocks'][0]['id']
    finally:
        repo.close()


def test_document_ocr_text_keeps_body_order_and_source_offsets(tmp_path):
    from knowledge_service.models import UploadOptions
    from knowledge_service.services.source_ingest import prepare_source_ingest
    class Adapter:
        engine='test'; languages='ch'; configuration_fingerprint='order-config'
        def recognize(self, raw):
            return dict(engine_version='1',preprocess_fingerprint='pre',width=100,height=100,
                        blocks=[dict(text='图片中的安全规则',normalized_text='图片中的安全规则',confidence=.99,
                                     x=.1,y=.1,width=.6,height=.2,page_or_frame=1)])
    repo=Repository(target(tmp_path/'body-order'))
    try:
        project=repo.create_project('Body order')['id']
        service=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'),Adapter())
        raw='前面的正文。\n\n![](a.png)\n\n后面的正文。\n\n> 本规则自2030年1月1日起生效。'.encode()
        source=service.persist(project,'doc',raw,'a.md',assets=[dict(kind='local',status='available',original_path='a.png',data=png())])
        chunks,text,_=prepare_source_ingest(service,project,source,UploadOptions(title='Rule',extraction_mode='documents').model_dump())
        assert text.index('前面的正文') < text.index('图片中的安全规则') < text.index('后面的正文')
        for chunk in chunks:
            assert text[chunk['start_char']:chunk['end_char']]==chunk['text']
            for span in chunk.get('source_spans',[]):
                assert 0<=span['chunk_start_char']<span['chunk_end_char']<=len(chunk['text'])
        from knowledge_service.services.document_temporal import source_temporal_exclusions, resolve_document_temporal
        exclusions=source_temporal_exclusions(source,chunks=chunks)
        resolved=resolve_document_temporal(text,{'document_time_mode':'auto'},excluded_ranges=exclusions)
        assert resolved['valid_from'] is None, 'Quoted dates after images must remain excluded after OCR insertion'
    finally:
        repo.close()
