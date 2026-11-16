"""OCR output contracts reject invalid evidence and configure quality gates."""
import pytest


def test_invalid_ocr_blocks_rejected():
    from knowledge_service.integrations.image_ocr import OcrBlock
    from pydantic import ValidationError
    valid=dict(text='rule',normalized_text='rule',confidence=.9,x=.1,y=.1,width=.4,height=.2)
    for patch in ({'confidence':float('nan')},{'text':''},{'normalized_text':' '},{'x':-.1},{'width':0},{'x':.9,'width':.4}):
        with pytest.raises(ValidationError): OcrBlock.model_validate(dict(valid,**patch))


def test_ocr_quality_policy_is_environment_configured(monkeypatch):
    from knowledge_service.services.image_ocr import ImageOCRService
    monkeypatch.setenv('KG_IMAGE_OCR_MIN_CONFIDENCE','.7')
    monkeypatch.setenv('KG_IMAGE_OCR_MIN_AVERAGE_CONFIDENCE','.8')
    monkeypatch.setenv('KG_IMAGE_OCR_MAX_LOW_CONFIDENCE_RATIO','.1')
    monkeypatch.setenv('KG_IMAGE_OCR_MIN_TEXT_CHARS','8')
    service=ImageOCRService(None,None,adapter=object())
    assert service.quality_policy==dict(min_confidence=.7,min_average_confidence=.8,max_low_confidence_ratio=.1,min_text_chars=8)


def test_quality_filters_bad_blocks_and_measures_retained_text():
    from knowledge_service.services.image_ocr import ImageOCRService
    service=ImageOCRService(None,None,adapter=object())
    def block(text,score):return dict(text=text,normalized_text=text,confidence=score,x=0,y=0,width=.5,height=.5)
    accepted,average=service.quality_check({'blocks':[block('9.9',.9977),block('noise',.4713),block('Youcanflyhigher',.865)]})
    assert [b['text'] for b in accepted]==['9.9','Youcanflyhigher']
    assert average==pytest.approx((.9977+.865)/2)


@pytest.mark.parametrize('values',[[],[('noise',.5)],[('可靠文字',.65)],[('a',.99)]])
def test_quality_still_rejects_unusable_text_in_chinese(values):
    from knowledge_service.services.image_ocr import ImageOCRService
    from knowledge_service.integrations.image_ocr import OCRError
    output={'blocks':[dict(text=text,normalized_text=text,confidence=score,x=0,y=0,width=.5,height=.5) for text,score in values]}
    with pytest.raises(OCRError) as error:ImageOCRService(None,None,adapter=object()).quality_check(output)
    assert error.value.code=='ocr_low_confidence'
    assert any('\u4e00'<=char<='\u9fff' for char in str(error.value))


def test_filtered_receipt_is_completed_and_warning_does_not_block_preparation(tmp_path):
    from knowledge_service.repository import Repository
    from pg_support import target
    from source_test_images import png
    from knowledge_service.integrations.source_object_store import SourceObjectStore
    from knowledge_service.services.source_documents import SourceDocumentService
    from knowledge_service.services.source_ingest import prepare_source_ingest
    from knowledge_service.services.preparations import recognition_error
    from knowledge_service.models import UploadOptions
    class Adapter:
        engine='test';languages='ch';configuration_fingerprint='filtered'
        def recognize(self,data):
            return dict(engine_version='test',preprocess_fingerprint='pre',width=8,height=8,blocks=[
                dict(text=text,normalized_text=text,confidence=score,x=0,y=0,width=.5,height=.5)
                for text,score in [('有效规则',.99),('可靠文字',.92),('noise',.47)]])
    repo=Repository(target(tmp_path/'filtered'))
    try:
        project=repo.create_project('filtered')['id']
        sources=SourceDocumentService(repo,SourceObjectStore(tmp_path/'objects'),Adapter())
        source=sources.persist(project,'doc',b'before ![](a.png) after','rule.md',assets=[
            dict(kind='local',status='available',original_path='a.png',data=png())])
        run=sources.image_ocr.process_asset(project,source['id'],source['assets'][0]['id'])
        assert run['status']=='completed' and run['error_code'] is None
        assert len(run['blocks'])==2 and 'noise' not in run['normalized_text']
        quality=run['processing_details']['quality']
        assert quality['discarded_blocks']==1 and quality['accepted_blocks']==2
        assert quality['review_recommended'] is True
        options=UploadOptions(title='Rule',extraction_mode='documents').model_dump()
        chunks,text,warnings=prepare_source_ingest(sources,project,source,options,cached_only=True,strict_media=True)
        assert '有效规则' in text and 'noise' not in text
        assert any(w.get('code')=='ocr_text_filtered' and w['severity']=='warning' for w in warnings)
        assert recognition_error({'warnings':warnings}) is None
    finally:repo.close()


def test_preprocess_fingerprint_captures_limits():
    from knowledge_service.integrations.image_ocr import RapidOCRAdapter
    assert RapidOCRAdapter(max_side=1000).preprocess_fingerprint != RapidOCRAdapter(max_side=2000).preprocess_fingerprint
    assert RapidOCRAdapter(max_pixels=1000).preprocess_fingerprint != RapidOCRAdapter(max_pixels=2000).preprocess_fingerprint


def test_quality_fingerprint_changes_with_each_threshold():
    from knowledge_service.services.image_ocr import ImageOCRService
    baseline=ImageOCRService(None,None,adapter=object()).fingerprint('adapter')
    for options in ({'min_confidence':.7},{'min_average_confidence':.8},{'max_low_confidence_ratio':.1},{'min_text_chars':8}):
        assert ImageOCRService(None,None,adapter=object(),**options).fingerprint('adapter')!=baseline


def test_adapter_rejects_invalid_engine_evidence():
    import io
    from types import SimpleNamespace
    from PIL import Image
    from knowledge_service.integrations.image_ocr import RapidOCRAdapter,OCRError
    image=Image.new('RGB',(100,100),'white'); stream=io.BytesIO();image.save(stream,format='PNG')
    for box,text,score in (([[0,0],[100,0],[100,50],[0,50]],'rule',float('nan')),([[-1,0],[50,0],[50,50],[0,50]],'rule',.9),([[0,0],[50,0],[50,50],[0,50]],'',.9)):
        adapter=RapidOCRAdapter();adapter._engine=lambda pixels:SimpleNamespace(boxes=[box],txts=[text],scores=[score])
        with pytest.raises(OCRError): adapter.recognize(stream.getvalue())
    unsupported=io.BytesIO();image.save(unsupported,format='PPM')
    with pytest.raises(OCRError,match='Unsupported source image format'): adapter.recognize(unsupported.getvalue())



def test_new_quality_policy_does_not_reuse_old_semantics():
    import hashlib,json
    from knowledge_service.services.image_ocr import ImageOCRService
    service=ImageOCRService(None,None,adapter=object())
    previous=hashlib.sha256(json.dumps(dict(adapter='adapter',quality=service.quality_policy),sort_keys=True).encode()).hexdigest()
    assert service.fingerprint('adapter')!=previous
